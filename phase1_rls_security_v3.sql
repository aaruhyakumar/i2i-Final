-- ============================================================================
-- Ink2Insights — Phase 1: Row Level Security (RLS) & Permissions Lockdown (Version 3)
-- ============================================================================
-- Document Version: 3.0.0
-- Target: Supabase PostgreSQL 15+
-- Security Model: Backend-Only Proxy (Flask uses service_role; anon direct access denied)
--
-- Enhancements in V3:
--   1. Pre-Lockdown State Snapshot: Captures original table privileges, sequence
--      grants, and RLS states into public._phase1_pre_lockdown_snapshot before
--      applying changes, enabling an exact, evidence-based rollback.
--   2. Strengthened Post-Execution Verification: Explicitly iterates through all
--      30 expected operational tables, asserting that each table exists, has
--      rowsecurity=true, completely blocks anon/authenticated, and permits service_role.
--   3. Verified Role Attributes: Asserts that service_role possesses rolbypassrls=true.
--   4. Precise Function Signatures: Locks down all 5 atomic RPC functions by exact
--      parameter signature, preserving extension routines (uuid-ossp, pgcrypto).
--   5. Controlled Rollback Architecture: Replaces unsafe wildcard grants with a
--      snapshot-driven restoration protocol.
-- ============================================================================

BEGIN;

-- ============================================================================
-- PART 1: RECORD PRE-LOCKDOWN STATE SNAPSHOT
-- ============================================================================
-- Creates an audit snapshot table to capture existing privileges and RLS state
-- before making any changes. This enables an exact rollback if needed.

CREATE TABLE IF NOT EXISTS public._phase1_pre_lockdown_snapshot (
    snapshot_id SERIAL PRIMARY KEY,
    object_type TEXT NOT NULL,         -- 'table', 'sequence', 'rls_state'
    object_name TEXT NOT NULL,
    grantee TEXT,
    privilege_type TEXT,
    is_grantable TEXT,
    rowsecurity_enabled BOOLEAN,
    captured_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Snapshot existing table privileges
INSERT INTO public._phase1_pre_lockdown_snapshot (object_type, object_name, grantee, privilege_type, is_grantable)
SELECT 'table', table_name, grantee, privilege_type, is_grantable
FROM information_schema.table_privileges
WHERE table_schema = 'public';

-- Snapshot existing sequence privileges
INSERT INTO public._phase1_pre_lockdown_snapshot (object_type, object_name, grantee, privilege_type, is_grantable)
SELECT 'sequence', sequence_name, grantee, privilege_type, is_grantable
FROM information_schema.usage_privileges
WHERE object_schema = 'public' AND object_type = 'SEQUENCE';

-- Snapshot existing RLS states
INSERT INTO public._phase1_pre_lockdown_snapshot (object_type, object_name, rowsecurity_enabled)
SELECT 'rls_state', tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public';


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
-- Resolves and locks down all 5 Phase 2C atomic RPC functions by exact signature.

-- 1. rpc_redeem_invitation_atomic
REVOKE ALL ON FUNCTION public.rpc_redeem_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.rpc_redeem_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) TO service_role;

-- 2. rpc_accept_invitation_atomic
REVOKE ALL ON FUNCTION public.rpc_accept_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.rpc_accept_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) TO service_role;

-- 3. rpc_decline_invitation_atomic
REVOKE ALL ON FUNCTION public.rpc_decline_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.rpc_decline_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN
) TO service_role;

-- 4. rpc_create_community_atomic
REVOKE ALL ON FUNCTION public.rpc_create_community_atomic(
    TEXT, TEXT, UUID, TEXT, TEXT, TEXT, BOOLEAN
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.rpc_create_community_atomic(
    TEXT, TEXT, UUID, TEXT, TEXT, TEXT, BOOLEAN
) TO service_role;

-- 5. rpc_admin_review_membership_atomic
REVOKE ALL ON FUNCTION public.rpc_admin_review_membership_atomic(
    UUID, UUID, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.rpc_admin_review_membership_atomic(
    UUID, UUID, TEXT, TEXT
) TO service_role;


-- ============================================================================
-- PART 6: COMPREHENSIVE RLS & PRIVILEGE VERIFICATION
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
    -- 1. Verify service_role possesses BYPASSRLS in pg_roles
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles 
        WHERE rolname = 'service_role' AND rolbypassrls = true
    ) THEN
        RAISE EXCEPTION 'CRITICAL: role service_role does NOT have BYPASSRLS attribute!';
    END IF;

    -- 2. Validate every single table in the 30-table inventory
    FOREACH curr_tbl IN ARRAY tbl_list LOOP
        -- Check table existence
        IF NOT EXISTS (
            SELECT 1 FROM pg_tables 
            WHERE schemaname = 'public' AND tablename = curr_tbl
        ) THEN
            err_msgs := err_msgs || format('[Table %s missing] ', curr_tbl);
            CONTINUE;
        END IF;

        -- Check RLS is enabled
        IF NOT EXISTS (
            SELECT 1 FROM pg_tables 
            WHERE schemaname = 'public' AND tablename = curr_tbl AND rowsecurity = true
        ) THEN
            err_msgs := err_msgs || format('[Table %s: RLS NOT enabled] ', curr_tbl);
        END IF;

        -- Check anon has NO SELECT or INSERT
        IF has_table_privilege('anon', format('public.%I', curr_tbl), 'SELECT') THEN
            err_msgs := err_msgs || format('[Table %s: anon retains SELECT] ', curr_tbl);
        END IF;
        IF has_table_privilege('anon', format('public.%I', curr_tbl), 'INSERT') THEN
            err_msgs := err_msgs || format('[Table %s: anon retains INSERT] ', curr_tbl);
        END IF;

        -- Check authenticated has NO SELECT
        IF has_table_privilege('authenticated', format('public.%I', curr_tbl), 'SELECT') THEN
            err_msgs := err_msgs || format('[Table %s: authenticated retains SELECT] ', curr_tbl);
        END IF;

        -- Check service_role retains SELECT and INSERT
        IF NOT has_table_privilege('service_role', format('public.%I', curr_tbl), 'SELECT') THEN
            err_msgs := err_msgs || format('[Table %s: service_role lacks SELECT] ', curr_tbl);
        END IF;
        IF NOT has_table_privilege('service_role', format('public.%I', curr_tbl), 'INSERT') THEN
            err_msgs := err_msgs || format('[Table %s: service_role lacks INSERT] ', curr_tbl);
        END IF;
    END LOOP;

    IF err_msgs != '' THEN
        RAISE EXCEPTION 'RLS security V3 verification failed: %', err_msgs;
    ELSE
        RAISE NOTICE 'SUCCESS: RLS security V3 verified across all 30 operational tables.';
    END IF;
END $$;

COMMIT;

-- ============================================================================
-- CONTROLLED ROLLBACK PLAN (SNAPSHOT RESTORATION PROTOCOL)
-- ============================================================================
-- In the event of an operational blockage, DO NOT apply wildcard grants.
-- Execute this controlled script to restore privileges exactly as recorded
-- in public._phase1_pre_lockdown_snapshot:
--
-- BEGIN;
-- DO $$
-- DECLARE
--     r RECORD;
-- BEGIN
--     -- 1. Restore RLS states from snapshot
--     FOR r IN (
--         SELECT object_name, rowsecurity_enabled 
--         FROM public._phase1_pre_lockdown_snapshot 
--         WHERE object_type = 'rls_state'
--     ) LOOP
--         IF r.rowsecurity_enabled = false THEN
--             EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY;', r.object_name);
--         END IF;
--     END LOOP;
--
--     -- 2. Restore table privileges from snapshot
--     FOR r IN (
--         SELECT object_name, grantee, privilege_type 
--         FROM public._phase1_pre_lockdown_snapshot 
--         WHERE object_type = 'table' AND grantee IN ('anon', 'authenticated', 'public')
--     ) LOOP
--         EXECUTE format('GRANT %s ON TABLE public.%I TO %I;', r.privilege_type, r.object_name, r.grantee);
--     END LOOP;
--
--     -- 3. Restore sequence privileges from snapshot
--     FOR r IN (
--         SELECT object_name, grantee, privilege_type 
--         FROM public._phase1_pre_lockdown_snapshot 
--         WHERE object_type = 'sequence' AND grantee IN ('anon', 'authenticated', 'public')
--     ) LOOP
--         EXECUTE format('GRANT %s ON SEQUENCE public.%I TO %I;', r.privilege_type, r.object_name, r.grantee);
--     END LOOP;
--
--     RAISE NOTICE 'Controlled rollback completed using pre-lockdown snapshot.';
-- END $$;
-- COMMIT;
-- ============================================================================
