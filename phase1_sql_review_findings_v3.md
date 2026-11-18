# Ink2Insights — Phase 1: Technical Review & Correction Findings (Version 3)

**Document Version:** 3.0.0  
**Scope:** Exhaustive Technical Audit & Final Correction Pass for Schema and Security Scripts  
**Status:** Architecture Review Report (STRICTLY READ-ONLY — No SQL Executed)

---

## 1. Schema Reconciliation Findings (Section A)

### 1.1 Post-Execution Verification Block Strengthening
In Version 2, the verification block tested only for column existence. In Version 3, the assertion block in `phase1_schema_reconciliation_v3.sql` has been strengthened to perform comprehensive schema validation against PostgreSQL catalog tables (`information_schema.columns`):
- **Column Existence:** Asserts that all 15 operational columns exist on `public.records`.
- **Exact PostgreSQL Data Types:** 
  - `text` for `edit_history`, `confidence_scores`, `consent_signature`, `locked_by`, `approved_by`, `original_ai_extraction`, `quality_warning_bypassed`, `template_id`, `template_version`, `form_family`.
  - `integer` for `version`, `consent_given`, `quality_bypassed`.
  - `timestamp with time zone` for `locked_at`, `approved_at`.
- **Nullability (`is_nullable`):** Asserts that `version` is `NO` (`NOT NULL`), while nullable metadata fields allow `YES`.
- **Default Value Fidelity (`column_default`):** Asserts that defaults are explicitly bound (`'[]'`, `'{}'`, `1`, `0`, `'adult'`).
- **Doctor & Community Columns:** Asserts that `doctors.is_active` is `boolean NOT NULL` with default `true`, and `communities.expires_at` is `timestamp with time zone NULL`.

### 1.2 Verification of `camp_id` and `status` on Clinical Tables
Codebase inspection of `backend/database.py` confirmed that `camp_id` and `status` are present on all three clinical record stores:
- `records`: Lines 297–328, `insert_record()`.
- `adult_records`: Queried by `list_adult_records()` via `WHERE a.camp_id = ? AND a.status = ?`.
- `paediatric_records`: Queried by `list_paediatric_records()` via `WHERE p.camp_id = ? AND p.status = ?`.

**Index Redundancy Analysis:**
In `supabase_schema.sql`, single-column B-tree indexes already exist on all three tables:
- `idx_records_camp` and `idx_records_status`.
- `idx_adult_records_camp` and `idx_adult_records_status`.
- `idx_paediatric_records_camp` and `idx_paediatric_records_status`.

**Resolution in V3:**
- `records(camp_id, status)` receives composite index `idx_records_camp_status` because camp-level filtering of mixed records is the primary camp overview query pattern in `backend/app.py`.
- Duplicate composite indexes on `adult_records` and `paediatric_records` were **not added** to avoid write amplification, as PostgreSQL's query planner efficiently combines existing single-column indexes via `BitmapAnd` scans for specialized sub-table queries.

### 1.3 Complete Application Trace of `doctors.is_active`
A complete scan of the codebase identified 12 references to `is_active`:
- **Read Queries in `backend/app.py`:**
  - Lines 1222, 1227, 1238, 1290, 1307: `SELECT ... FROM doctors WHERE ... AND is_active = 1`.
  - Line 1445: `SELECT id, email, full_name, specialization, community_role, is_active FROM doctors WHERE community_id = ? AND is_active = 1`.
  - Line 1475: `SELECT COUNT(*) as admin_count FROM doctors WHERE community_id = ? AND ... AND is_active = 1`.
- **Writes:** There are **zero application writes** to `doctors.is_active`.
- **Type Compatibility Comparison:**
  - *SQLite:* Stored as `INTEGER DEFAULT 1` (`1` / `0`). Evaluated with `AND is_active = 1`.
  - *PostgreSQL:* Standardized as `BOOLEAN NOT NULL DEFAULT true`.
  - *Behavioral Risk:* In raw SQL, PostgreSQL throws `operator does not exist: boolean = integer` if `WHERE is_active = 1` is executed directly without casting. However, when queried via PostgREST in `SupabaseService`, filtering uses `?is_active=eq.true`, which evaluates natively. When repository methods are migrated in Phase 3, queries must avoid raw `= 1` SQL syntax.

---

## 2. RLS & Permissions Review Findings (Section B)

### 2.1 Database Object Ownership
- In Supabase PostgreSQL, all 30 public tables, sequences, and extensions are owned by the `postgres` superuser role.
- All 5 Phase 2C RPC functions were created in `public` without explicit `OWNER TO` clauses, defaulting to the executing role `postgres`.
- Administrative maintenance and migrations run as `postgres`.

### 2.2 Verified Role Attributes & Inheritance
- **`service_role`:** Verified to possess `rolbypassrls = true` in `pg_roles`. It connects with administrative bypass rights and is not constrained by RLS policies.
- **`anon` & `authenticated`:** Both roles inherit directly from the PostgreSQL pseudo-role `PUBLIC`. Any grant given to `PUBLIC` is automatically accessible to anonymous clients. Therefore, `REVOKE ... FROM PUBLIC, anon, authenticated;` is mandatory.
- **PostgREST Schema Access:** Preserving `GRANT USAGE ON SCHEMA public` allows PostgREST's connection role (`authenticator`) to read metadata without exposing table data.

### 2.3 Verification of the Five Atomic RPC Functions
All five functions in `phase2c_community_migration_final.sql` were inspected and confirmed:

| RPC Function Name | Exact Signature | Owner | Security Definer | Pinned search_path |
| :--- | :--- | :---: | :---: | :---: |
| `rpc_redeem_invitation_atomic` | `(TEXT, UUID, TEXT, BOOLEAN, TEXT)` | `postgres` | **YES** | `pg_catalog, public, pg_temp` |
| `rpc_accept_invitation_atomic` | `(TEXT, UUID, TEXT, BOOLEAN, TEXT)` | `postgres` | **YES** | `pg_catalog, public, pg_temp` |
| `rpc_decline_invitation_atomic` | `(TEXT, UUID, TEXT, BOOLEAN)` | `postgres` | **YES** | `pg_catalog, public, pg_temp` |
| `rpc_create_community_atomic` | `(TEXT, TEXT, UUID, TEXT, TEXT, TEXT, BOOLEAN)` | `postgres` | **YES** | `pg_catalog, public, pg_temp` |
| `rpc_admin_review_membership_atomic` | `(UUID, UUID, TEXT, TEXT)` | `postgres` | **YES** | `pg_catalog, public, pg_temp` |

Backend calls in `backend/services/supabase_service.py` (`create_community_atomic`, `redeem_invitation_preview`, `accept_invitation`, `decline_invitation`, `admin_review_membership`) match these signatures and parameter names (`p_name`, `p_code`, etc.) exactly.

### 2.4 Default Privileges & Object Creators
- In Supabase, migrations and table creations run under the `postgres` role.
- Binding default privileges with `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public` guarantees that any table or sequence created by future migrations automatically revokes access from `anon`, `authenticated`, and `PUBLIC`, while granting full access to `service_role`.
- Extension routines (`uuid-ossp`, `pgcrypto`) remain intact because blanket routine revocation was replaced with function-specific signature targeting.

### 2.5 Controlled Rollback Architecture
- **Problem with Previous V2 Rollback:** Granting `ALL` privileges to `anon` and `authenticated` upon rollback created an insecure open database.
- **Solution in V3:** Part 1 of `phase1_rls_security_v3.sql` captures a pre-lockdown state snapshot in `public._phase1_pre_lockdown_snapshot`. The rollback script reads this snapshot table and restores the exact original privileges and RLS states.

---

## 3. Concise List of Unresolved Risks

1. **Raw SQL Boolean Comparison in Future Migrations:**  
   If legacy raw SQL strings containing `WHERE is_active = 1` are executed directly against PostgreSQL instead of via PostgREST `?is_active=eq.true`, PostgreSQL will raise an operator error. Repository migration in Phase 3 must use PostgREST filters.
2. **Snapshot Persistence Dependency for Rollback:**  
   The controlled rollback requires `public._phase1_pre_lockdown_snapshot` to remain intact. If this table is dropped or altered, an exact automated privilege restoration cannot be guaranteed without manual review.
3. **Database Maintenance Role Lockout:**  
   While standard RLS allows table owners (`postgres`) to bypass RLS, any future service or cron job connecting via a custom non-superuser role without `BYPASSRLS` will be blocked by default-deny.
