-- ============================================================================
-- I2I CLINICAL INTELLIGENCE PLATFORM
-- PHASE 2C: PASS 10 DEEP READ-ONLY POSTGRESQL CATALOG AUDIT QUERIES
-- ============================================================================
-- Execution Mode: STRICTLY READ-ONLY / AUDIT-ONLY
-- Target: Supabase SQL Editor (Run as postgres or service_role)
-- Safety Guarantee: ZERO DDL, ZERO DML, ZERO RPC EXECUTION, ZERO STATE MUTATION.
-- Instructions: Copy and execute these queries in the Supabase SQL Editor,
--               then save or share the query output for Phase 2C verification.
-- ============================================================================

-- ============================================================================
-- QUERY 1: RPC FUNCTION SIGNATURES, PROPERTIES & CATALOG ATTRIBUTES
-- ============================================================================
-- Purpose: Inspects pg_proc to verify function signatures, return types,
--          SECURITY DEFINER status (prosecdef), configured search_path (proconfig),
--          volatility, and routine ownership for all Phase 2C community RPCs.
-- ============================================================================

SELECT 
    n.nspname AS schema_name,
    p.proname AS function_name,
    pg_catalog.pg_get_function_identity_arguments(p.oid) AS identity_arguments,
    pg_catalog.pg_get_function_result(p.oid) AS return_type,
    r.rolname AS function_owner,
    p.prosecdef AS is_security_definer,
    CASE p.provolatile 
        WHEN 'i' THEN 'IMMUTABLE'
        WHEN 's' THEN 'STABLE'
        WHEN 'v' THEN 'VOLATILE'
        ELSE p.provolatile::text
    END AS volatility,
    p.proisstrict AS is_strict,
    p.proconfig AS configured_search_path,
    p.prokind AS routine_kind
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
LEFT JOIN pg_catalog.pg_roles r ON r.oid = p.proowner
WHERE n.nspname = 'public'
  AND p.proname IN (
    'rpc_redeem_invitation_atomic',
    'rpc_accept_invitation_atomic',
    'rpc_decline_invitation_atomic',
    'rpc_create_community_atomic',
    'rpc_admin_review_membership_atomic',
    'rpc_approve_membership_atomic'
  )
ORDER BY p.proname, identity_arguments;


-- ============================================================================
-- QUERY 2: EFFECTIVE EXECUTE PRIVILEGES ACROSS ROLES
-- ============================================================================
-- Purpose: Evaluates effective permissions for PUBLIC, anon, authenticated,
--          and service_role using PostgreSQL's built-in has_function_privilege().
-- Expected Security State:
--   - public_can_execute        : FALSE
--   - anon_can_execute          : FALSE
--   - authenticated_can_execute : FALSE
--   - service_role_can_execute  : TRUE
-- ============================================================================

SELECT 
    p.proname AS function_name,
    pg_catalog.pg_get_function_identity_arguments(p.oid) AS signature,
    pg_catalog.has_function_privilege('public', p.oid, 'EXECUTE') AS public_can_execute,
    pg_catalog.has_function_privilege('anon', p.oid, 'EXECUTE') AS anon_can_execute,
    pg_catalog.has_function_privilege('authenticated', p.oid, 'EXECUTE') AS authenticated_can_execute,
    pg_catalog.has_function_privilege('service_role', p.oid, 'EXECUTE') AS service_role_can_execute,
    p.proacl AS raw_acl
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN (
    'rpc_redeem_invitation_atomic',
    'rpc_accept_invitation_atomic',
    'rpc_decline_invitation_atomic',
    'rpc_create_community_atomic',
    'rpc_admin_review_membership_atomic',
    'rpc_approve_membership_atomic'
  )
ORDER BY p.proname, signature;


-- ============================================================================
-- QUERY 3: RETRIEVE ACTUAL FUNCTION SOURCE DEFINITIONS
-- ============================================================================
-- Purpose: Extracts the full decompiled function body for all target RPCs
--          to inspect locking statements, exception handling, and invariants.
-- ============================================================================

SELECT 
    p.proname AS function_name,
    pg_catalog.pg_get_function_identity_arguments(p.oid) AS signature,
    pg_catalog.pg_get_functiondef(p.oid) AS function_definition
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN (
    'rpc_redeem_invitation_atomic',
    'rpc_accept_invitation_atomic',
    'rpc_decline_invitation_atomic',
    'rpc_create_community_atomic',
    'rpc_admin_review_membership_atomic',
    'rpc_approve_membership_atomic'
  )
ORDER BY p.proname, signature;


-- ============================================================================
-- QUERY 4: COMPLETE CONSTRAINTS & FOREIGN KEY DELETION ACTIONS
-- ============================================================================
-- Purpose: Inspects pg_constraint to audit Primary Keys, Foreign Keys,
--          ON DELETE actions (RESTRICT, CASCADE, SET NULL), Unique constraints,
--          and Check constraints across the 6 core community tables.
-- ============================================================================

SELECT 
    t.relname AS table_name,
    c.conname AS constraint_name,
    CASE c.contype
        WHEN 'p' THEN 'PRIMARY KEY'
        WHEN 'f' THEN 'FOREIGN KEY'
        WHEN 'u' THEN 'UNIQUE'
        WHEN 'c' THEN 'CHECK'
        WHEN 'x' THEN 'EXCLUSION'
        ELSE c.contype::text
    END AS constraint_type,
    pg_catalog.pg_get_constraintdef(c.oid, true) AS constraint_definition,
    CASE c.confdeltype
        WHEN 'a' THEN 'NO ACTION'
        WHEN 'r' THEN 'RESTRICT'
        WHEN 'c' THEN 'CASCADE'
        WHEN 'n' THEN 'SET NULL'
        WHEN 'd' THEN 'SET DEFAULT'
        ELSE c.confdeltype::text
    END AS on_delete_action,
    CASE c.confupdtype
        WHEN 'a' THEN 'NO ACTION'
        WHEN 'r' THEN 'RESTRICT'
        WHEN 'c' THEN 'CASCADE'
        WHEN 'n' THEN 'SET NULL'
        WHEN 'd' THEN 'SET DEFAULT'
        ELSE c.confupdtype::text
    END AS on_update_action,
    c.condeferrable AS is_deferrable,
    c.condeferred AS is_deferred
FROM pg_catalog.pg_constraint c
JOIN pg_catalog.pg_class t ON t.oid = c.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND t.relname IN (
    'communities',
    'community_memberships',
    'invitations',
    'invitation_responses',
    'users',
    'roles'
  )
ORDER BY t.relname, constraint_type, c.conname;


-- ============================================================================
-- QUERY 5: TARGETED AUDIT OF INVITATION_RESPONSES DELETION POLICY
-- ============================================================================
-- Purpose: Specifically checks foreign key deletion actions on
--          invitation_responses (invitation_id, user_id, community_id)
--          to confirm whether ON DELETE RESTRICT or CASCADE is configured.
-- ============================================================================

SELECT 
    c.conname AS foreign_key_name,
    t.relname AS source_table,
    conf_t.relname AS referenced_table,
    pg_catalog.pg_get_constraintdef(c.oid, true) AS definition,
    CASE c.confdeltype
        WHEN 'a' THEN 'NO ACTION'
        WHEN 'r' THEN 'RESTRICT'
        WHEN 'c' THEN 'CASCADE'
        WHEN 'n' THEN 'SET NULL'
        WHEN 'd' THEN 'SET DEFAULT'
    END AS on_delete_action
FROM pg_catalog.pg_constraint c
JOIN pg_catalog.pg_class t ON t.oid = c.conrelid
JOIN pg_catalog.pg_class conf_t ON conf_t.oid = c.confrelid
JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND t.relname = 'invitation_responses'
  AND c.contype = 'f';


-- ============================================================================
-- QUERY 6: TABLE INDEXES & DUPLICATE INDEX DETECTION
-- ============================================================================
-- Purpose: Inspects pg_indexes and pg_index to verify active indexes,
--          uniqueness flags, and index definitions on all target tables.
-- ============================================================================

SELECT 
    i.schemaname,
    i.tablename,
    i.indexname,
    idx.indisunique AS is_unique,
    idx.indisprimary AS is_primary,
    i.indexdef
FROM pg_catalog.pg_indexes i
JOIN pg_catalog.pg_class c ON c.relname = i.indexname
JOIN pg_catalog.pg_index idx ON idx.indexrelid = c.oid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace AND n.nspname = i.schemaname
WHERE i.schemaname = 'public'
  AND i.tablename IN (
    'communities',
    'community_memberships',
    'invitations',
    'invitation_responses',
    'users',
    'roles'
  )
ORDER BY i.tablename, i.indexname;


-- ============================================================================
-- QUERY 7: TABLE TRIGGERS AUDIT
-- ============================================================================
-- Purpose: Inspects pg_trigger to identify any custom or auto-generated
--          triggers that could conflict with migration transactions.
-- ============================================================================

SELECT 
    t.relname AS table_name,
    trg.tgname AS trigger_name,
    p.proname AS trigger_function,
    CASE 
        WHEN (trg.tgtype & 2) = 2 THEN 'BEFORE'
        WHEN (trg.tgtype & 64) = 64 THEN 'INSTEAD OF'
        ELSE 'AFTER'
    END AS trigger_timing,
    CASE 
        WHEN (trg.tgtype & 4) = 4 THEN 'INSERT'
        WHEN (trg.tgtype & 8) = 8 THEN 'DELETE'
        WHEN (trg.tgtype & 16) = 16 THEN 'UPDATE'
        WHEN (trg.tgtype & 32) = 32 THEN 'TRUNCATE'
        ELSE 'OTHER'
    END AS trigger_event,
    CASE trg.tgenabled
        WHEN 'O' THEN 'ENABLED (origin/default)'
        WHEN 'D' THEN 'DISABLED'
        WHEN 'A' THEN 'ENABLED (always)'
        WHEN 'R' THEN 'ENABLED (replica only)'
        ELSE trg.tgenabled::text
    END AS trigger_status,
    trg.tgenabled
FROM pg_catalog.pg_trigger trg
JOIN pg_catalog.pg_class t ON t.oid = trg.tgrelid
JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
JOIN pg_catalog.pg_proc p ON p.oid = trg.tgfoid
WHERE n.nspname = 'public'
  AND NOT trg.tgisinternal
  AND t.relname IN (
    'communities',
    'community_memberships',
    'invitations',
    'invitation_responses',
    'users',
    'roles'
  )
ORDER BY t.relname, trg.tgname;


-- ============================================================================
-- QUERY 8: COLUMN DATA TYPES, NULLABILITY & DEFAULTS AUDIT
-- ============================================================================
-- Purpose: Inspects information_schema.columns to confirm exact SQL data types,
--          nullability, and default expressions for every column.
-- ============================================================================

SELECT 
    table_name,
    ordinal_position,
    column_name,
    data_type,
    udt_name,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN (
    'communities',
    'community_memberships',
    'invitations',
    'invitation_responses',
    'users',
    'roles'
  )
ORDER BY table_name, ordinal_position;
