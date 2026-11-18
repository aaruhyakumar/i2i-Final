-- ============================================================================
-- Ink2Insights — Phase 1: Row Level Security (RLS) & Permissions Lockdown
-- ============================================================================
-- Purpose:
--   Implement the approved "Backend-Only Proxy" security model on Supabase PostgreSQL.
--   Enables Row Level Security (RLS) across all 30 public tables, completely revokes
--   direct client access from anonymous ('anon') and standard ('authenticated') roles,
--   and ensures only the trusted Flask backend using 'service_role' can access data.
--
-- Security Posture:
--   - Default Deny: All direct PostgREST requests via anon key return HTTP 401/403 or empty sets.
--   - Zero Public Surface: No clinical records, patient PII/PHI, or scan metadata are exposed.
--   - Trusted Service Role: Flask backend accesses Supabase using SUPABASE_SERVICE_KEY (bypasses RLS).
--   - RPC Security: Functions strictly restricted to service_role with pinned search_path.
-- ============================================================================

BEGIN;

-- ============================================================================
-- PART 1: REVOKE PUBLIC AND ANONYMOUS PRIVILEGES
-- ============================================================================
-- Strip all direct schema privileges from anon, authenticated, and public roles.

REVOKE USAGE ON SCHEMA public FROM anon, authenticated;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM anon, authenticated, public;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated, public;
REVOKE ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA public FROM anon, authenticated, public;

-- Ensure future tables created in public schema do not auto-grant access to anon/authenticated
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated, public;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated, public;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON ROUTINES FROM anon, authenticated, public;


-- ============================================================================
-- PART 2: GRANT TRUSTED ACCESS TO SERVICE_ROLE
-- ============================================================================
-- The Flask backend connects exclusively via the service_role key.

GRANT USAGE ON SCHEMA public TO service_role;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA public TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON ROUTINES TO service_role;


-- ============================================================================
-- PART 3: ENABLE ROW LEVEL SECURITY (RLS) ON ALL 30 OPERATIONAL TABLES
-- ============================================================================
-- Enabling RLS without permissive policies results in an automatic, enforced
-- DEFAULT DENY for all roles except table owners and superusers/service_role.
-- FORCE ROW LEVEL SECURITY ensures policies apply even to table owners when not superusers.

-- 1. Core Clinical & Operational Tables
ALTER TABLE IF EXISTS public.records ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.records FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.adult_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.adult_records FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.paediatric_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.paediatric_records FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.camps ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.camps FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.communities ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.communities FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.doctors ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.doctors FORCE ROW LEVEL SECURITY;

-- 2. File & Deduplication Tables
ALTER TABLE IF EXISTS public.files ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.files FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.scan_hashes ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.scan_hashes FORCE ROW LEVEL SECURITY;

-- 3. Telemetry, Billing & Audit Tables
ALTER TABLE IF EXISTS public.usage_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.usage_ledger FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.record_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.record_audit_log FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.orgs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.orgs FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.payments FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invoices FORCE ROW LEVEL SECURITY;

-- 4. Auth & Session Management Tables
ALTER TABLE IF EXISTS public.auth_lockouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_lockouts FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.auth_otps ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_otps FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_sessions FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.auth_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_audit_log FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.password_resets ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.password_resets FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.users FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.user_profiles FORCE ROW LEVEL SECURITY;

-- 5. Station & Camp Management
ALTER TABLE IF EXISTS public.stations ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.stations FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.camp_staff_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.camp_staff_assignments FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.app_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.app_settings FORCE ROW LEVEL SECURITY;

-- 6. RBAC & Invitations (Phase 2C Tables)
ALTER TABLE IF EXISTS public.roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.roles FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.permissions FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.role_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.role_permissions FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.community_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.community_memberships FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invitations FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.invitation_responses ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invitation_responses FORCE ROW LEVEL SECURITY;

ALTER TABLE IF EXISTS public.invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invites FORCE ROW LEVEL SECURITY;


-- ============================================================================
-- PART 4: RPC FUNCTION SECURITY RESTRICTION
-- ============================================================================
-- Lock down all atomic community and invitation RPC functions so they cannot
-- be invoked by anonymous users or unverified JWTs directly via PostgREST /rpc/.

DO $$
DECLARE
    rpc_func TEXT;
    func_list TEXT[] := ARRAY[
        'rpc_create_community_atomic',
        'rpc_accept_invitation_atomic',
        'rpc_decline_invitation_atomic',
        'rpc_redeem_invitation_atomic',
        'rpc_admin_review_membership_atomic'
    ];
BEGIN
    FOREACH rpc_func IN ARRAY func_list LOOP
        -- Revoke execute from public/anon/authenticated
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated, public;');
        
        -- Explicitly verify and grant execute exclusively to service_role
        IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = rpc_func) THEN
            EXECUTE format('REVOKE ALL ON FUNCTION public.%I FROM anon, authenticated, public;', rpc_func);
            EXECUTE format('GRANT EXECUTE ON FUNCTION public.%I TO service_role;', rpc_func);
            RAISE NOTICE 'Secured RPC function: %', rpc_func;
        END IF;
    END LOOP;
END $$;


-- ============================================================================
-- PART 5: SECURITY VERIFICATION SUITE
-- ============================================================================
-- Verify RLS and permissions configuration.

-- 1. Verify all public tables have rowsecurity enabled
DO $$
DECLARE
    unsecured_tables TEXT;
BEGIN
    SELECT string_agg(tablename, ', ') INTO unsecured_tables
    FROM pg_tables
    WHERE schemaname = 'public' 
      AND rowsecurity = false;

    IF unsecured_tables IS NOT NULL THEN
        RAISE EXCEPTION 'RLS is NOT enabled on tables: %', unsecured_tables;
    ELSE
        RAISE NOTICE 'SUCCESS: All public schema tables have Row Level Security ENABLED.';
    END IF;
END $$;

-- 2. Verify anon has zero SELECT privilege on sensitive clinical tables
DO $$
BEGIN
    IF has_table_privilege('anon', 'public.records', 'SELECT') THEN
        RAISE EXCEPTION 'CRITICAL: anon role still has SELECT privilege on public.records!';
    END IF;
    IF has_table_privilege('anon', 'public.adult_records', 'SELECT') THEN
        RAISE EXCEPTION 'CRITICAL: anon role still has SELECT privilege on public.adult_records!';
    END IF;
    IF has_table_privilege('anon', 'public.paediatric_records', 'SELECT') THEN
        RAISE EXCEPTION 'CRITICAL: anon role still has SELECT privilege on public.paediatric_records!';
    END IF;
    IF has_table_privilege('anon', 'public.files', 'SELECT') THEN
        RAISE EXCEPTION 'CRITICAL: anon role still has SELECT privilege on public.files!';
    END IF;
    
    RAISE NOTICE 'SUCCESS: anon role is strictly blocked from all clinical and file tables.';
END $$;

-- 3. Verify service_role retains full access
DO $$
BEGIN
    IF NOT has_table_privilege('service_role', 'public.records', 'SELECT') THEN
        RAISE EXCEPTION 'ERROR: service_role lacks SELECT privilege on public.records!';
    END IF;
    IF NOT has_table_privilege('service_role', 'public.records', 'INSERT') THEN
        RAISE EXCEPTION 'ERROR: service_role lacks INSERT privilege on public.records!';
    END IF;
    
    RAISE NOTICE 'SUCCESS: service_role has full operational privileges.';
END $$;

COMMIT;
-- ============================================================================
-- End of Phase 1 RLS Security Lockdown
-- ============================================================================
