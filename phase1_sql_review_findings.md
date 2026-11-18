# Ink2Insights — Phase 1: SQL Technical Review & Correction Findings

**Document Version:** 2.0.0  
**Scope:** In-Depth Technical Audit of `phase1_schema_reconciliation.sql` and `phase1_rls_security.sql`  
**Status:** Review Findings & Correction Rationale (READ-ONLY Verification)

---

## 1. Schema Reconciliation Findings (Section A)

### 1.1 Data Type Alignment across Parent and Child Tables
In version 1 of the schema reconciliation script, several JSON and flag fields were provisionally typed as `JSONB` or `BOOLEAN` on `public.records`. 

A deep inspection of the live Supabase database and existing child tables revealed:
- `adult_records` and `paediatric_records` in Supabase already define:
  - `edit_history TEXT DEFAULT '[]'`
  - `confidence_scores TEXT DEFAULT '{}'`
  - `consent_given INTEGER DEFAULT 1`
  - `quality_warning_bypassed TEXT DEFAULT '[]'`
  - `quality_bypassed INTEGER DEFAULT 0`
  - `original_ai_extraction TEXT`
- In SQLite, these same attributes are stored as `TEXT` (JSON strings) and `INTEGER` (`1` / `0`).
- In `backend/database.py`, `insert_record()`, `update_reviewed_record()`, and `_format_record_row()` serialize and deserialize these fields using `json.dumps()` and `json.loads()`.
- **Correction in V2:** `public.records` now uses `TEXT DEFAULT '[]'`, `TEXT DEFAULT '{}'`, `INTEGER DEFAULT 1`, and `INTEGER DEFAULT 0`. This guarantees 100% data type consistency across `records`, `adult_records`, and `paediatric_records`.

### 1.2 Boolean and Integer Compatibility (`consent_given`, `quality_bypassed`, `doctors.is_active`)
- **`consent_given` & `quality_bypassed`:** Stored as `INTEGER` (`1`/`0`) in SQLite and both existing child tables in Supabase. Using `INTEGER` avoids PostgREST type-casting errors when Python passes integer flags.
- **`doctors.is_active`:** In SQLite, `backend/app.py` queries `WHERE is_active = 1`. In Supabase, `communities.is_active` is `BOOLEAN DEFAULT true`. In V2, `doctors.is_active` is added as `BOOLEAN NOT NULL DEFAULT true`. When transitioning queries from SQLite raw SQL to `SupabaseService` via PostgREST, PostgREST queries evaluate `?is_active=eq.true` cleanly.

### 1.3 Timestamp Handling & Timezone Assumptions
- All timestamps in SQLite (`created_at`, `updated_at`, `approved_at`, `locked_at`) are stored as UTC ISO 8601 strings (`datetime.now(timezone.utc).isoformat()`).
- Supabase columns use `TIMESTAMP WITH TIME ZONE` (`TIMESTAMPTZ`).
- PostgreSQL natively parses ISO 8601 strings (e.g. `2026-11-17T09:30:00+00:00`) into `TIMESTAMPTZ` without data loss or timezone shifts.

### 1.4 Genuine Necessity of `communities.expires_at`
- Investigation of the codebase confirmed that `backend/database.py::get_community_by_code()` (lines 2114–2119) actively evaluates `expires_at`:
  ```python
  exp_str = comm.get("expires_at")
  if exp_str:
      exp_dt = datetime.fromisoformat(exp_str)
      if datetime.now(timezone.utc) > exp_dt:
          return None  # Code expired
  ```
- While Phase 2C community creation (`rpc_create_community_atomic`) delegates expiry to invitations (`invitations.expires_at`), legacy demo communities created in SQLite require `expires_at` to remain valid during backfill and rollback.
- **Conclusion:** `expires_at TIMESTAMP WITH TIME ZONE DEFAULT NULL` on `public.communities` is genuinely necessary and preserved in V2.

### 1.5 Query-Driven Index Analysis & Pruning
A complete audit of query patterns across `backend/app.py` and `backend/database.py` evaluated the four indexes proposed in V1:

| Proposed Index in V1 | Query Pattern Audit | Finding & Action in V2 |
| :--- | :--- | :--- |
| `idx_records_form_family` | `form_family` is stored and returned on individual rows, but **never filtered in a WHERE clause** anywhere in the application. | **REMOVED:** Zero query selectivity; adds unnecessary write amplification. |
| `idx_records_approved_by` | Records are approved via `UPDATE ... WHERE id = ?`. Approvals are never queried via `SELECT WHERE approved_by = ?`. | **REMOVED:** Redundant. |
| `idx_records_locked_by` | Lock status is checked via `SELECT locked_by FROM records WHERE id = ?`. The primary key index on `id` resolves this in $O(1)$. | **REMOVED:** Completely redundant with PK index. |
| `idx_doctors_is_active` | `doctors` queries filter by `auth_id = ?` or `email = ?` (which are unique keys with existing indexes `idx_doctors_auth_id` and `idx_doctors_email`). | **REMOVED:** Low cardinality boolean index will never be selected by PostgreSQL query planner. |
| **`idx_records_camp_status`** *(NEW)* | `list_records(camp_id, status)` queries: `SELECT * FROM records WHERE camp_id = ? AND status = ?`. | **ADDED in V2:** High-impact composite index on `(camp_id, status)` for `records`, `adult_records`, and `paediatric_records`. |

---

## 2. RLS & Permissions Review Findings (Section B)

### 2.1 Service Role and `BYPASSRLS`
- In Supabase PostgreSQL, the `service_role` role is pre-configured with the PostgreSQL `BYPASSRLS` attribute (`ALTER ROLE service_role BYPASSRLS`).
- Inspection of `backend/.env` and `backend/services/supabase_service.py` confirmed that `SupabaseService` connects using `SUPABASE_SERVICE_ROLE_KEY` (`sb_secret_...`), which PostgREST maps directly to `service_role`.
- Therefore, `service_role` bypasses all Row Level Security policies automatically.

### 2.2 Dangers of `FORCE ROW LEVEL SECURITY`
- In V1, `FORCE ROW LEVEL SECURITY` was applied to all 30 tables.
- **Critical Risk:** PostgreSQL documentation states that `FORCE ROW LEVEL SECURITY` forces RLS policies to apply **even to the table owner** (`postgres`). Because no permissive policies exist (default-deny), applying `FORCE` locks out the table owner, breaking Supabase Studio UI, internal triggers, and database maintenance tools.
- **Correction in V2:** Removed `FORCE`. Standard `ENABLE ROW LEVEL SECURITY` applies default-deny to all client roles (`anon`, `authenticated`), while permitting table owners and `service_role` to operate unimpeded.

### 2.3 PostgREST Schema USAGE Dependency
- In V1, `REVOKE USAGE ON SCHEMA public FROM anon, authenticated;` was included.
- **Critical Risk:** PostgREST requires `USAGE ON SCHEMA public` to build its OpenAPI metadata cache. Revoking schema USAGE causes PostgREST to return HTTP 500 errors on API startup or schema reload.
- **Correction in V2:** `GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;` is preserved. Security is enforced strictly at the table level via `REVOKE ALL PRIVILEGES ON ALL TABLES ... FROM anon, authenticated, public;` and RLS default-deny.

### 2.4 Prevention of Extension Routine Breakage
- In V1, blanket revocation `REVOKE ALL ON ALL ROUTINES IN SCHEMA public FROM anon, authenticated, public;` was proposed.
- **Critical Risk:** If PostgreSQL extensions (`uuid-ossp`, `pgcrypto`) install functions in `public`, revoking all routines breaks `uuid_generate_v4()` and `digest()`, causing default column UUID generation and triggers to fail with `permission denied`.
- **Correction in V2:** Blanket routine revocation removed. Only the 5 specific business RPC functions are resolved and secured by exact parameter signatures:
  1. `public.rpc_redeem_invitation_atomic(TEXT, UUID, TEXT, BOOLEAN, TEXT)`
  2. `public.rpc_accept_invitation_atomic(TEXT, UUID, TEXT, BOOLEAN, TEXT)`
  3. `public.rpc_decline_invitation_atomic(TEXT, UUID, TEXT, BOOLEAN)`
  4. `public.rpc_create_community_atomic(TEXT, TEXT, UUID, TEXT, TEXT, TEXT, BOOLEAN)`
  5. `public.rpc_admin_review_membership_atomic(UUID, UUID, TEXT, TEXT)`

### 2.5 Role Inheritance & Default Privileges
- In PostgreSQL, `anon` and `authenticated` inherit from the pseudo-role `PUBLIC`. Revoking privileges from `anon` while leaving `PUBLIC` granted allows indirect access.
- **Correction in V2:** Explicitly revokes privileges from `PUBLIC, anon, authenticated` simultaneously.
- **Default Privileges:** Bound explicitly to `FOR ROLE postgres` to ensure future tables created by migrations inherit the lockdown.

### 2.6 Key Isolation Verification
- A full-codebase scan across `frontend/`, `android/`, and `backend/` confirmed that `SUPABASE_SERVICE_ROLE_KEY` exists **exclusively** on the Flask backend (`backend/.env` and `supabase_service.py`).
- Zero references to the service-role key or secret tokens exist in client-side mobile or web code.
