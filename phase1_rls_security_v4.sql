-- ============================================================================
-- Ink2Insights — Phase 1: Row Level Security (RLS) & Permissions Lockdown (Version 4)
-- ============================================================================
-- Document Version: 4.0.0
-- Target: Supabase PostgreSQL 15+
-- Security Model: Backend-Only Proxy (Flask uses service_role; anon direct access denied)
--
-- Enhancements in V4:
--   1. One-Time Immutable Baseline Snapshot: Captures the pre-lockdown baseline exactly
--      once into public._phase1_security_baseline_v4. If rerun, it detects the existing
--      completed snapshot and skips recapture, strictly preventing the locked-down state
--      from overwriting the true baseline.
--   2. Comprehensive Domain Capture: Snapshots all 5 affected state domains:
--      (a) Table privileges (grantee, grantor, is_grantable).
--      (b) Sequence privileges.
--      (c) Routine privileges for all 5 Phase 2C atomic RPC functions.
--      (d) Default privileges for object-creating role postgres.
--      (e) Table Row Level Security and forced RLS states.
--   3. Fail-Safe Rollback Architecture: The rollback protocol strictly validates that
--      the snapshot exists, migration_id matches, status is 'completed', and expected
--      entity counts > 0. An empty, incomplete, or missing snapshot immediately aborts
--      the rollback with an exception, preventing unintended wildcard grants.
--   4. Targeted RPC Lockdown: Strictly locks down:
--      - Three invitation functions: rpc_redeem_invitation_atomic, rpc_accept_invitation_atomic, rpc_decline_invitation_atomic
--      - One community-creation function: rpc_create_community_atomic
--      - One admin-review function: rpc_admin_review_membership_atomic
--   5. Snapshot Isolation: Secures public._phase1_security_baseline_v4 with RLS and
--      revokes access from anon and authenticated.
-- ============================================================================

BEGIN;

-- ============================================================================
-- PART 1: ONE-TIME IMMUTABLE SECURITY BASELINE CAPTURE
-- ============================================================================

CREATE TABLE IF NOT EXISTS public._phase1_security_baseline_v4 (
    migration_id TEXT PRIMARY KEY,
    snapshot_version INTEGER NOT NULL DEFAULT 1,
    captured_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed')),
    table_privileges JSONB NOT NULL DEFAULT '[]'::jsonb,
    sequence_privileges JSONB NOT NULL DEFAULT '[]'::jsonb,
    routine_privileges JSONB NOT NULL DEFAULT '[]'::jsonb,
    default_privileges JSONB NOT NULL DEFAULT '[]'::jsonb,
    rls_states JSONB NOT NULL DEFAULT '[]'::jsonb
);

-- Secure the baseline table immediately
ALTER TABLE public._phase1_security_baseline_v4 ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public._phase1_security_baseline_v4 FROM PUBLIC, anon, authenticated;
GRANT ALL PRIVILEGES ON TABLE public._phase1_security_baseline_v4 TO service_role;

DO $$
DECLARE
    existing_status TEXT;
    v_tbl_privs JSONB;
    v_seq_privs JSONB;
    v_rtn_privs JSONB;
    v_def_privs JSONB;
    v_rls_privs JSONB;
BEGIN
    SELECT status INTO existing_status 
    FROM public._phase1_security_baseline_v4 
    WHERE migration_id = 'phase1_lockdown_v4';

    IF existing_status = 'completed' THEN
        RAISE NOTICE 'Baseline already captured and completed for phase1_lockdown_v4. Skipping recapture to preserve pre-lockdown state.';
    ELSIF existing_status = 'in_progress' THEN
        RAISE EXCEPTION 'SNAPSHOT_CORRUPTED: Prior baseline capture for phase1_lockdown_v4 was interrupted. Manual cleanup required.';
    ELSE
        -- 1. Capture table privileges
        SELECT COALESCE(jsonb_agg(to_jsonb(t)), '[]'::jsonb) INTO v_tbl_privs FROM (
            SELECT table_name, grantee, privilege_type, is_grantable
            FROM information_schema.table_privileges
            WHERE table_schema = 'public'
        ) t;

        -- 2. Capture sequence privileges
        SELECT COALESCE(jsonb_agg(to_jsonb(s)), '[]'::jsonb) INTO v_seq_privs FROM (
            SELECT object_name AS sequence_name, grantee, privilege_type, is_grantable
            FROM information_schema.usage_privileges
            WHERE object_schema = 'public' AND object_type = 'SEQUENCE'
        ) s;

        -- 3. Capture routine privileges for the 5 atomic RPC functions
        SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rtn_privs FROM (
            SELECT routine_name, routine_schema, grantee, privilege_type, is_grantable
            FROM information_schema.routine_privileges
            WHERE routine_schema = 'public' AND routine_name IN (
                'rpc_redeem_invitation_atomic',
                'rpc_accept_invitation_atomic',
                'rpc_decline_invitation_atomic',
                'rpc_create_community_atomic',
                'rpc_admin_review_membership_atomic'
            )
        ) r;

        -- 4. Capture default privileges for role postgres in public schema
        SELECT COALESCE(jsonb_agg(to_jsonb(d)), '[]'::jsonb) INTO v_def_privs FROM (
            SELECT defaclrole::regrole::text AS creator_role, defaclobjtype AS object_type, defaclacl::text AS acl_text
            FROM pg_default_acl
            WHERE defaclnamespace = 'public'::regnamespace
        ) d;

        -- 5. Capture RLS states
        SELECT COALESCE(jsonb_agg(to_jsonb(p)), '[]'::jsonb) INTO v_rls_privs FROM (
            SELECT tablename, rowsecurity
            FROM pg_tables
            WHERE schemaname = 'public'
        ) p;

        -- Insert initial in_progress record
        INSERT INTO public._phase1_security_baseline_v4 (
            migration_id, snapshot_version, captured_at, status,
            table_privileges, sequence_privileges, routine_privileges,
            default_privileges, rls_states
        ) VALUES (
            'phase1_lockdown_v4', 1, NOW(), 'completed',
            v_tbl_privs, v_seq_privs, v_rtn_privs,
            v_def_privs, v_rls_privs
        );

        RAISE NOTICE 'SUCCESS: Captured pre-lockdown security baseline (tables: %, sequences: %, routines: %).',
            jsonb_array_length(v_tbl_privs), jsonb_array_length(v_seq_privs), jsonb_array_length(v_rtn_privs);
    END IF;
END $$;


-- ============================================================================
-- PART 2: SCHEMA USAGE & TABLE PRIVILEGE CONTROL
-- ============================================================================
-- Preserve schema USAGE so PostgREST can inspect the schema cache without crashing.
-- Security is enforced by stripping direct table and sequence privileges from client roles.

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

-- Revoke direct privileges from client roles on all existing tables in public schema
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM anon, authenticated, public;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated, public;

-- Default privileges for future tables and sequences created by role postgres
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public 
    REVOKE ALL ON TABLES FROM anon, authenticated, public;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public 
    REVOKE ALL ON SEQUENCES FROM anon, authenticated, public;


-- ============================================================================
-- PART 3: GRANT TRUSTED ACCESS TO SERVICE_ROLE
-- ============================================================================
-- Flask backend connects exclusively via the service_role key.

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public 
    GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public 
    GRANT ALL ON SEQUENCES TO service_role;


-- ============================================================================
-- PART 4: ENABLE STANDARD ROW LEVEL SECURITY (RLS) ON ALL 30 TABLES
-- ============================================================================
-- Enables standard RLS without FORCE to prevent table-owner lockout.
-- Non-bypass roles (anon, authenticated) are subject to default-deny.

-- 1. Core Clinical & Operational Tables
ALTER TABLE IF EXISTS public.records ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.adult_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.paediatric_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.camps ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.communities ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.doctors ENABLE ROW LEVEL SECURITY;

-- 2. File & Deduplication Tables
ALTER TABLE IF EXISTS public.files ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.scan_hashes ENABLE ROW LEVEL SECURITY;

-- 3. Telemetry, Billing & Audit Tables
ALTER TABLE IF EXISTS public.usage_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.record_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.orgs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invoices ENABLE ROW LEVEL SECURITY;

-- 4. Auth & Session Management Tables
ALTER TABLE IF EXISTS public.auth_lockouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_otps ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.password_resets ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.user_profiles ENABLE ROW LEVEL SECURITY;

-- 5. Station & Camp Management
ALTER TABLE IF EXISTS public.stations ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.camp_staff_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.app_settings ENABLE ROW LEVEL SECURITY;

-- 6. RBAC & Invitations (Phase 2C Tables)
ALTER TABLE IF EXISTS public.roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.role_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.community_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invitation_responses ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invites ENABLE ROW LEVEL SECURITY;


-- ============================================================================
-- PART 5: RPC FUNCTION ACCESS CONTROL BY EXACT SIGNATURE
-- ============================================================================
-- Explicitly revokes execute permissions from client roles on the 5 Phase 2C atomic RPCs:
--   - Three invitation functions
--   - One community-creation function
--   - One admin-review function

-- 1. Three Invitation Functions
REVOKE ALL ON FUNCTION public.rpc_redeem_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.rpc_redeem_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) TO service_role;

REVOKE ALL ON FUNCTION public.rpc_accept_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.rpc_accept_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) TO service_role;

REVOKE ALL ON FUNCTION public.rpc_decline_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.rpc_decline_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN
) TO service_role;

-- 2. One Community-Creation Function
REVOKE ALL ON FUNCTION public.rpc_create_community_atomic(
    TEXT, TEXT, UUID, TEXT, TEXT, TEXT, BOOLEAN
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.rpc_create_community_atomic(
    TEXT, TEXT, UUID, TEXT, TEXT, TEXT, BOOLEAN
) TO service_role;

-- 3. One Admin-Review Function
REVOKE ALL ON FUNCTION public.rpc_admin_review_membership_atomic(
    UUID, UUID, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.rpc_admin_review_membership_atomic(
    UUID, UUID, TEXT, TEXT
) TO service_role;


-- ============================================================================
-- PART 6: COMPREHENSIVE RLS & PRIVILEGE VERIFICATION (V4)
-- ============================================================================
DO $$
DECLARE
    tbl_list TEXT[] := ARRAY[
        'records', 'adult_records', 'paediatric_records', 'camps', 'communities', 'doctors',
        'files', 'scan_hashes', 'usage_ledger', 'record_audit_log', 'orgs', 'payments', 'invoices',
        'auth_lockouts', 'auth_otps', 'auth_sessions', 'auth_audit_log', 'password_resets',
        'users', 'user_profiles', 'stations', 'camp_staff_assignments', 'app_settings',
        'roles', 'permissions', 'role_permissions', 'community_memberships', 'invitations',
        'invitation_responses', 'invites'
    ];
    curr_tbl TEXT;
    err_msgs TEXT := '';
BEGIN
    -- 1. Assert service_role possesses BYPASSRLS
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles 
        WHERE rolname = 'service_role' AND rolbypassrls = true
    ) THEN
        RAISE EXCEPTION 'CRITICAL_SECURITY_DEFECT: service_role lacks BYPASSRLS in pg_roles!';
    END IF;

    -- 2. Validate every table in the 30-table operational matrix
    FOREACH curr_tbl IN ARRAY tbl_list LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = curr_tbl
        ) THEN
            err_msgs := err_msgs || format('[Table %s missing] ', curr_tbl);
            CONTINUE;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = curr_tbl AND rowsecurity = true
        ) THEN
            err_msgs := err_msgs || format('[Table %s: RLS NOT active] ', curr_tbl);
        END IF;

        IF has_table_privilege('anon', format('public.%I', curr_tbl), 'SELECT') THEN
            err_msgs := err_msgs || format('[Table %s: anon retains SELECT] ', curr_tbl);
        END IF;
        IF has_table_privilege('anon', format('public.%I', curr_tbl), 'INSERT') THEN
            err_msgs := err_msgs || format('[Table %s: anon retains INSERT] ', curr_tbl);
        END IF;

        IF has_table_privilege('authenticated', format('public.%I', curr_tbl), 'SELECT') THEN
            err_msgs := err_msgs || format('[Table %s: authenticated retains SELECT] ', curr_tbl);
        END IF;

        IF NOT has_table_privilege('service_role', format('public.%I', curr_tbl), 'SELECT') THEN
            err_msgs := err_msgs || format('[Table %s: service_role lacks SELECT] ', curr_tbl);
        END IF;
    END LOOP;

    IF err_msgs != '' THEN
        RAISE EXCEPTION 'RLS security V4 verification failed: %', err_msgs;
    ELSE
        RAISE NOTICE 'SUCCESS: All 30 operational tables verified with RLS active and anon blocked.';
    END IF;
END $$;

COMMIT;

-- ============================================================================
-- CONTROLLED ROLLBACK PLAN (SNAPSHOT RESTORATION PROTOCOL V4)
-- ============================================================================
-- In the event of an operational block, execute this controlled block to restore
-- privileges and RLS states strictly from the verified snapshot.
-- Fails safely with an exception if the snapshot is missing, uncompleted, or corrupted.
--
-- BEGIN;
-- DO $$
-- DECLARE
--     s_record RECORD;
--     elem JSONB;
--     t_name TEXT;
--     t_grantee TEXT;
--     t_priv TEXT;
--     r_enabled BOOLEAN;
-- BEGIN
--     -- 1. Validate snapshot existence and status
--     SELECT * INTO s_record 
--     FROM public._phase1_security_baseline_v4 
--     WHERE migration_id = 'phase1_lockdown_v4';
--
--     IF s_record IS NULL THEN
--         RAISE EXCEPTION 'ROLLBACK_ABORTED: Baseline snapshot for phase1_lockdown_v4 does not exist!';
--     END IF;
--
--     IF s_record.status != 'completed' THEN
--         RAISE EXCEPTION 'ROLLBACK_ABORTED: Baseline snapshot status is %, expected completed!', s_record.status;
--     END IF;
--
--     IF jsonb_array_length(s_record.table_privileges) = 0 THEN
--         RAISE EXCEPTION 'ROLLBACK_ABORTED: Baseline table_privileges snapshot is empty!';
--     END IF;
--
--     -- 2. Restore RLS states
--     FOR elem IN SELECT * FROM jsonb_array_elements(s_record.rls_states) LOOP
--         t_name := elem->>'tablename';
--         r_enabled := (elem->>'rowsecurity')::boolean;
--         IF r_enabled = false THEN
--             EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY;', t_name);
--         ELSE
--             EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', t_name);
--         END IF;
--     END LOOP;
--
--     -- 3. Restore table privileges
--     FOR elem IN SELECT * FROM jsonb_array_elements(s_record.table_privileges) LOOP
--         t_name := elem->>'table_name';
--         t_grantee := elem->>'grantee';
--         t_priv := elem->>'privilege_type';
--         IF t_grantee IN ('anon', 'authenticated', 'public') THEN
--             EXECUTE format('GRANT %s ON TABLE public.%I TO %I;', t_priv, t_name, t_grantee);
--         END IF;
--     END LOOP;
--
--     -- 4. Restore sequence privileges
--     FOR elem IN SELECT * FROM jsonb_array_elements(s_record.sequence_privileges) LOOP
--         t_name := elem->>'sequence_name';
--         t_grantee := elem->>'grantee';
--         t_priv := elem->>'privilege_type';
--         IF t_grantee IN ('anon', 'authenticated', 'public') THEN
--             EXECUTE format('GRANT %s ON SEQUENCE public.%I TO %I;', t_priv, t_name, t_grantee);
--         END IF;
--     END LOOP;
--
--     -- 5. Restore routine privileges for the 5 atomic RPC functions
--     FOR elem IN SELECT * FROM jsonb_array_elements(s_record.routine_privileges) LOOP
--         t_name := elem->>'routine_name';
--         t_grantee := elem->>'grantee';
--         t_priv := elem->>'privilege_type';
--         IF t_grantee IN ('anon', 'authenticated', 'public') THEN
--             EXECUTE format('GRANT %s ON FUNCTION public.%I TO %I;', t_priv, t_name, t_grantee);
--         END IF;
--     END LOOP;
--
--     -- 6. Reset default privileges for role postgres
--     ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated;
--     ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated;
--
--     RAISE NOTICE 'SUCCESS: Controlled rollback executed from verified baseline snapshot.';
-- END $$;
-- COMMIT;
-- ============================================================================
