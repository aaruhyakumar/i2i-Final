# Ink2Insights — Phase 1: Application Workflow Impact Matrix (Version 3)

**Document Version:** 3.0.0  
**Scope:** Evidence-Based Impact Analysis Across All 11 Core Workflows  
**Status:** Architecture Assessment (STRICTLY READ-ONLY — No SQL Executed)

---

## 1. Methodology & Evidence Classification

To eliminate unverified claims of "zero impact," all findings in this matrix are explicitly categorized by evidence type:
1. **[Code-Inspection Evidence]:** Verified by direct inspection of Python endpoints in `backend/app.py`, repository logic in `backend/database.py`, or service calls in `backend/services/supabase_service.py`.
2. **[Database Metadata Evidence]:** Verified by live PostgREST OpenAPI inspection, catalog table structures, or existing DDL scripts (`supabase_schema.sql`, `phase2c_community_migration_final.sql`).
3. **[Architectural Assumption]:** Logical deductions based on standard PostgreSQL/Supabase engine behavior that have not yet been validated against a running instance.
4. **[Pending Test]:** Explicit functional or integration test that must be executed during Phase 1 staging to confirm expected behavior.

---

## 2. Evidence-Based Workflow Impact Matrix

| Workflow | Relevant Endpoints & Code Paths | Database Entities Involved | Evidence-Based Assessment | Evidence Sources & Verified Facts | Residual Risk & Tests Requiring Execution |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Signup, Login & Token Refresh** | `POST /api/auth/login`<br/>`POST /api/auth/signup`<br/>`POST /api/auth/refresh`<br/>`SupabaseService.auth_*` | Supabase Auth (`auth.users`, `auth.refresh_tokens`) | **Expected: No Impact**<br/>(Supabase Auth operates in the `auth` schema, completely outside `public` schema RLS boundaries.) | - **[Code-Inspection]:** Calls hit `/auth/v1/*` HTTPS endpoints using `service_role` key.<br/>- **[Metadata]:** Tables belong to `auth` schema. | **[Pending Test]:** Execute live JWT generation and verify token expiration handling via Flask. |
| **2. Community Creation & Membership** | `POST /api/communities`<br/>`SupabaseService.create_community_atomic` | `communities`, `community_memberships`, `roles` | **Expected: Fully Functional**<br/>(RPC function granted `EXECUTE` to `service_role`; internal inserts run via `SECURITY DEFINER`.) | - **[Code-Inspection]:** `supabase_service.py` calls `/rest/v1/rpc/rpc_create_community_atomic`.<br/>- **[Metadata]:** Exact signature matches DDL. | **[Pending Test]:** Call `/api/communities` to verify founder membership creation with community_admin role. |
| **3. Invitations & Acceptance** | `POST /api/invitations`<br/>`POST /api/invitations/accept`<br/>`POST /api/invitations/decline`<br/>`POST /api/invitations/redeem`<br/>`POST /api/memberships/<id>/review` | `invitations`, `invitation_responses`, `community_memberships` | **Expected: Fully Functional**<br/>(All 4 invitation/membership review RPCs granted `EXECUTE` to `service_role`.) | - **[Code-Inspection]:** Traced to the 4 atomic RPCs in `supabase_service.py`.<br/>- **[Metadata]:** All 4 functions have pinned `search_path`. | **[Pending Test]:** End-to-end invite lifecycle test: generate invite code → redeem preview → accept membership. |
| **4. Doctor Profiles & Roles** | `GET /api/doctors/profile`<br/>`PUT /api/doctors/profile`<br/>`SupabaseService.sync_user_profile` | `doctors`, `users`, `user_profiles` | **Expected: Fully Functional**<br/>(`is_active` added to `doctors`; `service_role` has full `SELECT`/`UPDATE`.) | - **[Code-Inspection]:** `app.py` queries `doctors` using `auth_id`.<br/>- **[Metadata]:** `doctors.is_active` added as `BOOLEAN DEFAULT true`. | **[Pending Test]:** Verify `/api/doctors/profile` returns `is_active: true` and resolves role. |
| **5. Camp Creation, Listing & Stats** | `POST /api/camps`<br/>`GET /api/camps`<br/>`GET /api/camps/<id>`<br/>`SupabaseService.list_camps_with_stats` | `camps`, `communities`, `camp_staff_assignments` | **Expected: Fully Functional**<br/>(`service_role` bypasses table RLS to perform aggregate joins.) | - **[Code-Inspection]:** `supabase_service.py` performs PostgREST select with `camps(name)`.<br/>- **[Metadata]:** `idx_camps_community` exists. | **[Pending Test]:** Create a new camp and verify camp card statistics in dashboard UI. |
| **6. Record Creation, Edit & Approval** | `POST /api/records`<br/>`PUT /api/records/<id>`<br/>`POST /api/records/<id>/approve`<br/>`SupabaseService.insert_record` | `records`, `adult_records`, `paediatric_records` | **Expected: Fully Functional**<br/>(15 missing columns added; `service_role` writes without RLS restriction.) | - **[Code-Inspection]:** Traced 15 missing columns in `database.py`.<br/>- **[Metadata]:** Added with exact types matching child tables. | **[Pending Test]:** Save a record with `original_ai_extraction` and verify approval timestamp write. |
| **7. Adult & Paediatric Retrieval** | `GET /api/records`<br/>`GET /api/records/<id>`<br/>`GET /api/records/paediatric/<id>`<br/>`SupabaseService.get_record_by_id` | `records`, `adult_records`, `paediatric_records` | **Expected: Fully Functional**<br/>(Parent-child tables share 100% column parity in V3.) | - **[Code-Inspection]:** Segregated retrieval in `database.py` and `supabase_service.py`.<br/>- **[Metadata]:** Schema parity confirmed. | **[Pending Test]:** Retrieve a paediatric record and verify ATR, centiles, and WHO classifications unpack cleanly. |
| **8. Private File Authorization** | `GET /api/files/<file_id>`<br/>`app.py::get_private_file`<br/>`check_field_worker_file_access` | `files`, `records`, `camps` (plus local disk binary) | **Expected: Fully Functional**<br/>(Flask queries Supabase metadata, then streams binary from private local disk.) | - **[Code-Inspection]:** Task 9A authorization evaluates `file.record_id` and `record.scanned_by`.<br/>- **[Metadata]:** Local paths mapped. | **[Pending Test]:** Field worker accesses authorized scan file (HTTP 200) vs unauthorized file (HTTP 403). |
| **9. Usage Ledger & Audit Logging** | `RecordService.log_usage`<br/>`app.py::record_audit_log` | `usage_ledger`, `record_audit_log` | **Expected: Fully Functional**<br/>(`service_role` has full `INSERT` privileges.) | - **[Code-Inspection]:** Token counts and costs logged per record.<br/>- **[Metadata]:** Tables exist with RLS enabled. | **[Pending Test]:** Execute test AI extraction and verify cost calculation row in `usage_ledger`. |
| **10. Excel MIS Generation & Download** | `GET /api/camps/<id>/export`<br/>`RecordService.generate_excel_export` | In-memory processing of records retrieved from Supabase | **Expected: Fully Functional**<br/>(Exporter processes generic dict lists; zero database writes.) | - **[Code-Inspection]:** Formatted in Task 8; decoupled from SQL queries.<br/>- **[Metadata]:** Clinical columns match. | **[Pending Test]:** Download Excel export for a 20-record camp; verify paediatric alert cell highlighting. |
| **11. Direct Anonymous PostgREST Probes** | Direct client requests sending `apikey: <anon_key>` to PostgREST endpoints | All 30 public tables | **Expected: Strictly Blocked**<br/>(All table privileges revoked; default-deny RLS active.) | - **[Code-Inspection]:** Zero client-side Supabase SDK calls exist in `frontend/` or `android/`.<br/>- **[Metadata]:** RLS default-deny active. | **[Pending Test]:** Curl `/rest/v1/records` with anon key; assert HTTP 401/403 or empty `[]`. |

---

## 3. Summary of Function Classification

All five custom database routines are strictly classified as **Phase 2C Multi-Tenant Identity RPC Functions**:
1. `public.rpc_redeem_invitation_atomic`: Preview/read-only validation of join invitation codes.
2. `public.rpc_accept_invitation_atomic`: Consumes invitation, updates usage count, creates community membership.
3. `public.rpc_decline_invitation_atomic`: Records rejection, invalidates one-time code.
4. `public.rpc_create_community_atomic`: Atomically creates organization community and assigns creator as community admin.
5. `public.rpc_admin_review_membership_atomic`: Approves or rejects pending community membership applications.

All five are `SECURITY DEFINER`, specify `SET search_path = pg_catalog, public, pg_temp`, and are invoked exclusively by the Flask backend using the `service_role` key.
