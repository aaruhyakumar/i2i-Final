-- ============================================================================
-- Ink2Insights — Phase 1: Row Level Security (RLS) & Permissions Lockdown (Version 2)
-- ============================================================================
-- Document Version: 2.0.0
-- Target: Supabase PostgreSQL 15+
-- Security Model: Backend-Only Proxy (Flask uses service_role; anon direct access denied)
--
-- Corrections Applied in V2:
--   1. Schema USAGE Preserved: GRANT USAGE ON SCHEMA public preserved for anon
--      and authenticated. This allows PostgREST to inspect the schema cache without
--      500 internal errors, while data access is completely blocked at the table level.
--   2. Standard RLS vs FORCE RLS: Removed indiscriminate FORCE ROW LEVEL SECURITY.
--      Standard ENABLE ROW LEVEL SECURITY blocks anon/authenticated while allowing
--      table owners (postgres) and service_role (via BYPASSRLS) to operate without lockout.
--   3. Exact Function Signatures: Blanket routine revocation removed (preventing
--      breakage of uuid-ossp and pgcrypto extensions). All 5 Phase 2C RPC functions
--      are resolved and locked down by exact schema, name, and argument signature.
--   4. Explicit Object Creator Defaults: ALTER DEFAULT PRIVILEGES bound explicitly
--      FOR ROLE postgres.
-- ============================================================================

BEGIN;

-- ============================================================================
-- PART 1: SCHEMA USAGE & TABLE PRIVILEGE CONTROL
-- ============================================================================
-- PostgREST requires schema USAGE to build its OpenAPI definitions.
-- Security is enforced by stripping all table/sequence access from anon, authenticated, and public.

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

-- Revoke all direct data privileges on all existing tables in public schema
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM anon, authenticated, public;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated, public;

-- Explicitly ensure future tables created by postgres do not auto-grant access to anon/authenticated
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public 
    REVOKE ALL ON TABLES FROM anon, authenticated, public;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public 
    REVOKE ALL ON SEQUENCES FROM anon, authenticated, public;


-- ============================================================================
-- PART 2: GRANT TRUSTED ACCESS TO SERVICE_ROLE
-- ============================================================================
-- Flask backend connects exclusively via the service_role key.
-- In Supabase, the service_role possesses the BYPASSRLS attribute.

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public 
    GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public 
    GRANT ALL ON SEQUENCES TO service_role;


-- ============================================================================
-- PART 3: ENABLE STANDARD ROW LEVEL SECURITY (RLS) ON ALL 30 TABLES
-- ============================================================================
-- Enables standard RLS without FORCE. Because no permissive policies exist,
-- PostgreSQL applies default-deny for all non-owner/non-bypass roles (anon, authenticated).

-- Core Clinical & Operational Tables
ALTER TABLE IF EXISTS public.records ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.adult_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.paediatric_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.camps ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.communities ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.doctors ENABLE ROW LEVEL SECURITY;

-- File & Deduplication Tables
ALTER TABLE IF EXISTS public.files ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.scan_hashes ENABLE ROW LEVEL SECURITY;

-- Telemetry, Billing & Audit Tables
ALTER TABLE IF EXISTS public.usage_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.record_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.orgs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invoices ENABLE ROW LEVEL SECURITY;

-- Auth & Session Management Tables
ALTER TABLE IF EXISTS public.auth_lockouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_otps ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.password_resets ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.user_profiles ENABLE ROW LEVEL SECURITY;

-- Station & Camp Management
ALTER TABLE IF EXISTS public.stations ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.camp_staff_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.app_settings ENABLE ROW LEVEL SECURITY;

-- RBAC & Invitations (Phase 2C Tables)
ALTER TABLE IF EXISTS public.roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.role_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.community_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invitation_responses ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.invites ENABLE ROW LEVEL SECURITY;


-- ============================================================================
-- PART 4: RPC FUNCTION ACCESS CONTROL BY EXACT SIGNATURE
-- ============================================================================
-- Explicitly revokes execute permissions from anon, authenticated, and public
-- on the exact signatures of the 5 Phase 2C RPC functions, granting access
-- solely to service_role.

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
-- PART 5: SECURITY VERIFICATION SUITE
-- ============================================================================
DO $$
DECLARE
    unsecured_tables TEXT;
BEGIN
    -- 1. Verify RLS is enabled on all public tables
    SELECT string_agg(tablename, ', ') INTO unsecured_tables
    FROM pg_tables
    WHERE schemaname = 'public' 
      AND rowsecurity = false;

    IF unsecured_tables IS NOT NULL THEN
        RAISE EXCEPTION 'RLS is NOT enabled on tables: %', unsecured_tables;
    END IF;

    -- 2. Verify anon has NO SELECT privilege on clinical or user tables
    IF has_table_privilege('anon', 'public.records', 'SELECT') THEN
        RAISE EXCEPTION 'Security check failed: anon retains SELECT on public.records';
    END IF;
    IF has_table_privilege('anon', 'public.adult_records', 'SELECT') THEN
        RAISE EXCEPTION 'Security check failed: anon retains SELECT on public.adult_records';
    END IF;
    IF has_table_privilege('anon', 'public.paediatric_records', 'SELECT') THEN
        RAISE EXCEPTION 'Security check failed: anon retains SELECT on public.paediatric_records';
    END IF;
    IF has_table_privilege('anon', 'public.files', 'SELECT') THEN
        RAISE EXCEPTION 'Security check failed: anon retains SELECT on public.files';
    END IF;
    IF has_table_privilege('anon', 'public.users', 'SELECT') THEN
        RAISE EXCEPTION 'Security check failed: anon retains SELECT on public.users';
    END IF;

    -- 3. Verify service_role retains full operational access
    IF NOT has_table_privilege('service_role', 'public.records', 'SELECT') THEN
        RAISE EXCEPTION 'Security check failed: service_role lacks SELECT on public.records';
    END IF;
    IF NOT has_table_privilege('service_role', 'public.records', 'INSERT') THEN
        RAISE EXCEPTION 'Security check failed: service_role lacks INSERT on public.records';
    END IF;

    RAISE NOTICE 'SUCCESS: RLS security v2 verified. Backend-only isolation active.';
END $$;

COMMIT;

-- ============================================================================
-- ROLLBACK INSTRUCTIONS (IF REVERSAL IS REQUIRED)
-- ============================================================================
-- To revert Part 3 & Part 1 in the event of an unforeseen operational block:
--
-- BEGIN;
-- DO $$
-- DECLARE
--     r RECORD;
-- BEGIN
--     FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
--         EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY;', r.tablename);
--         EXECUTE format('GRANT ALL PRIVILEGES ON TABLE public.%I TO anon, authenticated, service_role;', r.tablename);
--     END LOOP;
--     FOR r IN (SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public') LOOP
--         EXECUTE format('GRANT ALL PRIVILEGES ON SEQUENCE public.%I TO anon, authenticated, service_role;', r.sequence_name);
--     END LOOP;
-- END $$;
-- COMMIT;
-- ============================================================================
