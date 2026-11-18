# Ink2Insights — Phase 1: SQL Verification & Validation Plan

**Document Version:** 2.0.0  
**Target:** Supabase PostgreSQL Database Execution  
**Scope:** Pre-Execution, Post-Execution, and Rollback Verification Protocol  
**Status:** Verification Protocol (Ready for Execution when Authorized)

---

## 1. Overview

This document specifies the exact, step-by-step SQL verification queries and test procedures to validate `phase1_schema_reconciliation_v2.sql` and `phase1_rls_security_v2.sql` upon approved execution.

---

## 2. Pre-Execution Safety Checks

Before applying the Phase 1 V2 scripts, run this read-only query to establish the baseline:

```sql
-- 1. Check existing column counts for public.records
SELECT count(*) AS existing_records_col_count 
FROM information_schema.columns 
WHERE table_schema = 'public' AND table_name = 'records';
-- Expected Baseline: 27

-- 2. Check current RLS status across all public tables
SELECT tablename, rowsecurity 
FROM pg_tables 
WHERE schemaname = 'public' AND rowsecurity = true;
-- Expected Baseline: 0 rows (RLS currently disabled)
```

---

## 3. Post-Execution Schema Verification

Execute immediately after running `phase1_schema_reconciliation_v2.sql`:

### 3.1 Column Parity & Data Type Check
```sql
SELECT column_name, data_type, column_default, is_nullable
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
**Expected Outcome:** 15 rows returned with exact matching data types (`text`, `integer`, `timestamp with time zone`).

### 3.2 Doctor & Community Column Check
```sql
SELECT table_name, column_name, data_type, column_default
FROM information_schema.columns
WHERE table_schema = 'public' 
  AND (
    (table_name = 'doctors' AND column_name = 'is_active') OR
    (table_name = 'communities' AND column_name = 'expires_at')
  );
```
**Expected Outcome:** 2 rows returned (`doctors.is_active` as `boolean`, `communities.expires_at` as `timestamp with time zone`).

### 3.3 Composite Query Indexes Verification
```sql
SELECT indexname, tablename, indexdef
FROM pg_indexes
WHERE schemaname = 'public' 
  AND indexname IN (
    'idx_records_camp_status', 
    'idx_adult_records_camp_status', 
    'idx_paediatric_records_camp_status'
  );
```
**Expected Outcome:** 3 composite indexes present on `(camp_id, status)`.

---

## 4. Post-Execution Security & RLS Verification

Execute immediately after running `phase1_rls_security_v2.sql`:

### 4.1 100% RLS Enablement Check
```sql
SELECT count(*) AS unshielded_tables_count
FROM pg_tables
WHERE schemaname = 'public' AND rowsecurity = false;
```
**Expected Outcome:** `unshielded_tables_count` must be exactly `0`.

### 4.2 Privilege Boundary Simulation (Anon vs Service Role)
```sql
-- Test effective privileges for anon (must all be FALSE)
SELECT 
    has_table_privilege('anon', 'public.records', 'SELECT') AS anon_can_select_records,
    has_table_privilege('anon', 'public.records', 'INSERT') AS anon_can_insert_records,
    has_table_privilege('anon', 'public.adult_records', 'SELECT') AS anon_can_select_adult,
    has_table_privilege('anon', 'public.paediatric_records', 'SELECT') AS anon_can_select_paed,
    has_table_privilege('anon', 'public.files', 'SELECT') AS anon_can_select_files,
    has_table_privilege('anon', 'public.users', 'SELECT') AS anon_can_select_users;
```
**Expected Outcome:** All 6 columns must return `false`.

```sql
-- Test effective privileges for service_role (must all be TRUE)
SELECT 
    has_table_privilege('service_role', 'public.records', 'SELECT') AS sr_can_select_records,
    has_table_privilege('service_role', 'public.records', 'INSERT') AS sr_can_insert_records,
    has_table_privilege('service_role', 'public.records', 'UPDATE') AS sr_can_update_records,
    has_table_privilege('service_role', 'public.files', 'SELECT') AS sr_can_select_files,
    has_table_privilege('service_role', 'public.camps', 'SELECT') AS sr_can_select_camps;
```
**Expected Outcome:** All 5 columns must return `true`.

### 4.3 Atomic RPC Function Access Verification
```sql
SELECT routine_name, grantee, privilege_type
FROM information_schema.routine_privileges
WHERE routine_schema = 'public'
  AND routine_name IN (
    'rpc_create_community_atomic',
    'rpc_accept_invitation_atomic',
    'rpc_redeem_invitation_atomic',
    'rpc_decline_invitation_atomic',
    'rpc_admin_review_membership_atomic'
  )
ORDER BY routine_name, grantee;
```
**Expected Outcome:** `grantee` must show `service_role` with `EXECUTE`. `anon`, `authenticated`, and `PUBLIC` must NOT appear.

---

## 5. Live Client PostgREST Validation (from Bash / Python)

Test direct HTTP calls using curl or Python:

```bash
# 1. Direct Anonymous Query (Must fail or return empty/forbidden)
curl -s -X GET "https://vftssavirdkdcqwqwryk.supabase.co/rest/v1/records" \
  -H "apikey: <SUPABASE_ANON_KEY>" \
  -H "Authorization: Bearer <SUPABASE_ANON_KEY>"
# Expected Response: HTTP 401/403 Forbidden or empty []

# 2. Service Role Backend Query (Must succeed)
curl -s -X GET "https://vftssavirdkdcqwqwryk.supabase.co/rest/v1/records?limit=1" \
  -H "apikey: <SUPABASE_SERVICE_ROLE_KEY>" \
  -H "Authorization: Bearer <SUPABASE_SERVICE_ROLE_KEY>"
# Expected Response: HTTP 200 OK
```

---

## 6. Emergency Rollback Verification Protocol

If rollback is declared, execute the rollback block documented in `phase1_rls_security_v2.sql`:
1. Re-run Section 4.1. Unshielded table count should return 30 (RLS disabled).
2. Verify that Flask application health check `/api/health` returns 200 OK.
