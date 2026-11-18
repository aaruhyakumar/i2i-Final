# Ink2Insights — Phase 1: SQL Verification & Validation Plan (Version 4)

**Document Version:** 4.0.0  
**Target:** Supabase PostgreSQL Database Execution  
**Scope:** Pre-Execution, Post-Execution, and Controlled Rollback Verification Protocol  
**Status:** Verification Protocol (Ready for Controlled Execution)

---

## 1. Overview

This document specifies the exact, step-by-step SQL verification queries and test procedures to validate `phase1_schema_reconciliation_v4.sql` and `phase1_rls_security_v4.sql` during controlled execution.

---

## 2. Pre-Execution Safety Baseline

Before executing the Phase 1 V4 scripts, run this read-only query in the Supabase SQL Editor to capture the initial baseline:

```sql
-- 1. Check existing column count for public.records
SELECT count(*) AS existing_records_col_count 
FROM information_schema.columns 
WHERE table_schema = 'public' AND table_name = 'records';
-- Expected Baseline: 27

-- 2. Check current RLS status across all public tables
SELECT count(*) AS rls_enabled_tables_count 
FROM pg_tables 
WHERE schemaname = 'public' AND rowsecurity = true;
-- Expected Baseline: 0 rows (RLS currently disabled)

-- 3. Confirm service_role possesses BYPASSRLS attribute
SELECT rolname, rolbypassrls, rolsuper 
FROM pg_roles 
WHERE rolname = 'service_role';
-- Expected Baseline: rolbypassrls = true
```

---

## 3. Post-Execution Schema Verification (Script 1)

Execute immediately after running `phase1_schema_reconciliation_v4.sql`:

### 3.1 Comprehensive Attribute Verification (Types, Nullability & Defaults)
```sql
SELECT 
    column_name, 
    data_type, 
    is_nullable, 
    column_default
FROM information_schema.columns
WHERE table_schema = 'public' 
  AND table_name = 'records'
  AND column_name IN (
    'edit_history', 'version', 'confidence_scores', 'consent_given',
    'consent_signature', 'locked_by', 'locked_at', 'approved_by',
    'approved_at', 'original_ai_extraction', 'quality_warning_bypassed',
    'quality_bypassed', 'template_id', 'template_version', 'form_family'
  )
ORDER BY column_name;
```
**Expected Outcome:** 15 rows returned with exact matching data types (`text`, `integer`, `timestamp with time zone`), exact nullability (`version` is `NO`; others `YES`), and explicit defaults.

### 3.2 Doctor Status & Community Expiry Verification
```sql
SELECT 
    table_name, 
    column_name, 
    data_type, 
    is_nullable, 
    column_default
FROM information_schema.columns
WHERE table_schema = 'public' 
  AND (
    (table_name = 'doctors' AND column_name = 'is_active') OR
    (table_name = 'communities' AND column_name = 'expires_at')
  );
```
**Expected Outcome:**
- `doctors.is_active`: `data_type = 'boolean'`, `is_nullable = 'NO'`, `column_default = 'true'`.
- `communities.expires_at`: `data_type = 'timestamp with time zone'`, `is_nullable = 'YES'`.

### 3.3 Composite Query Index Verification
```sql
SELECT indexname, tablename, indexdef
FROM pg_indexes
WHERE schemaname = 'public' AND indexname = 'idx_records_camp_status';
```
**Expected Outcome:** 1 row returned showing `CREATE INDEX idx_records_camp_status ON public.records USING btree (camp_id, status)`.

---

## 4. Post-Execution RLS & Security Verification (Script 2)

Execute immediately after running `phase1_rls_security_v4.sql`:

### 4.1 Immutable Baseline Snapshot Verification
```sql
SELECT 
    migration_id,
    snapshot_version,
    status,
    jsonb_array_length(table_privileges) AS table_grants_count,
    jsonb_array_length(sequence_privileges) AS sequence_grants_count,
    jsonb_array_length(routine_privileges) AS routine_grants_count,
    jsonb_array_length(default_privileges) AS default_privs_count,
    jsonb_array_length(rls_states) AS rls_states_count
FROM public._phase1_security_baseline_v4;
```
**Expected Outcome:** Exactly 1 row with `migration_id = 'phase1_lockdown_v4'`, `status = 'completed'`, and all array lengths $> 0$.

### 4.2 Explicit Table-by-Table Privilege & RLS Verification
Run this PL/pgSQL block to test all 30 operational tables:
```sql
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
BEGIN
    FOREACH curr_tbl IN ARRAY tbl_list LOOP
        -- Assert RLS is active
        IF NOT EXISTS (
            SELECT 1 FROM pg_tables 
            WHERE schemaname = 'public' AND tablename = curr_tbl AND rowsecurity = true
        ) THEN
            RAISE EXCEPTION 'Verification Failed: Table % does NOT have RLS enabled!', curr_tbl;
        END IF;

        -- Assert anon cannot read or write
        IF has_table_privilege('anon', format('public.%I', curr_tbl), 'SELECT') THEN
            RAISE EXCEPTION 'Security Breach: anon retains SELECT on table %!', curr_tbl;
        END IF;
        IF has_table_privilege('anon', format('public.%I', curr_tbl), 'INSERT') THEN
            RAISE EXCEPTION 'Security Breach: anon retains INSERT on table %!', curr_tbl;
        END IF;

        -- Assert authenticated cannot read
        IF has_table_privilege('authenticated', format('public.%I', curr_tbl), 'SELECT') THEN
            RAISE EXCEPTION 'Security Breach: authenticated retains SELECT on table %!', curr_tbl;
        END IF;

        -- Assert service_role has full read/write access
        IF NOT has_table_privilege('service_role', format('public.%I', curr_tbl), 'SELECT') THEN
            RAISE EXCEPTION 'Access Error: service_role lacks SELECT on table %!', curr_tbl;
        END IF;
    END LOOP;
    RAISE NOTICE 'SUCCESS: All 30 tables verified with RLS active and client roles blocked.';
END $$;
```

### 4.3 Five Atomic RPC Functions Privilege Verification
```sql
SELECT 
    routine_name, 
    grantee, 
    privilege_type
FROM information_schema.routine_privileges
WHERE routine_schema = 'public'
  AND routine_name IN (
    'rpc_redeem_invitation_atomic',
    'rpc_accept_invitation_atomic',
    'rpc_decline_invitation_atomic',
    'rpc_create_community_atomic',
    'rpc_admin_review_membership_atomic'
  )
ORDER BY routine_name, grantee;
```
**Expected Outcome:** `service_role` is the ONLY grantee with `EXECUTE`. `anon`, `authenticated`, and `PUBLIC` do not appear.

---

## 5. Live PostgREST API Smoke Tests (from Terminal)

### Test 1: Direct Anonymous Client Probe (Must Fail)
```bash
curl -i -X GET "https://vftssavirdkdcqwqwryk.supabase.co/rest/v1/records" \
  -H "apikey: <SUPABASE_ANON_KEY>" \
  -H "Authorization: Bearer <SUPABASE_ANON_KEY>"
```
**Expected Response:** HTTP 401 Unauthorized or HTTP 403 Forbidden, or empty JSON array `[]`.

### Test 2: Trusted Backend Service Role Probe (Must Succeed)
```bash
curl -i -X GET "https://vftssavirdkdcqwqwryk.supabase.co/rest/v1/records?limit=1" \
  -H "apikey: <SUPABASE_SERVICE_ROLE_KEY>" \
  -H "Authorization: Bearer <SUPABASE_SERVICE_ROLE_KEY>"
```
**Expected Response:** HTTP 200 OK with JSON representation.

---

## 6. Controlled Rollback Execution & Verification Protocol

If an unexpected operational issue arises, execute the snapshot-driven rollback block documented at the end of `phase1_rls_security_v4.sql`:

1. **Safety Pre-Check:** The rollback block verifies that `status = 'completed'` and `jsonb_array_length(table_privileges) > 0`. If unpopulated, it raises an exception and aborts without altering privileges.
2. **Execute Snapshot Restoration:** Re-applies the exact privilege states, sequence grants, RPC execute grants, and RLS states recorded in `public._phase1_security_baseline_v4`.
3. **Post-Rollback Health Check:**
   ```sql
   SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND rowsecurity = false;
   ```
   Verify that row security states match the pre-execution baseline.
4. **Application Verification:** Test `/api/health` to confirm the Flask service continues serving normally.
