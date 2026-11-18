# Ink2Insights — Phase 1: Application Workflow Impact Matrix

**Document Version:** 2.0.0  
**Scope:** End-to-End Tracing of All 11 Core Workflows under Backend-Only Proxy Lockdown  
**Status:** Architecture Impact Assessment (READ-ONLY Verification)

---

## 1. Executive Summary

This matrix traces the operational impact of applying `phase1_schema_reconciliation_v2.sql` and `phase1_rls_security_v2.sql` on all 11 core application workflows.

**Core Architecture Fact:**
The frontend and mobile clients communicate **exclusively** with Flask backend API endpoints (`/api/...`). There are **zero direct Supabase client SDK calls** in the frontend or Android codebases. Flask serves as the single trusted intermediary, connecting to Supabase with the privileged `service_role` key.

---

## 2. Comprehensive Workflow Impact Analysis

| Workflow | Endpoints & Backend Code Paths | Database Entities Involved | Access Mechanism | Impact of V2 Lockdown | Potential Failure Modes & Mitigation |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **1. Signup, Login & Refresh** | `POST /api/auth/login`<br/>`POST /api/auth/signup`<br/>`POST /api/auth/refresh`<br/>`SupabaseService.auth_*` | Supabase Auth (`auth.users`, `auth.refresh_tokens`) | Direct HTTPS to `/auth/v1/*` using `service_role` key | **NO IMPACT** (Green) | Supabase Auth operates in the `auth` schema, completely independent of `public` schema RLS. Login and token generation remain 100% operational. |
| **2. Community Creation & Membership** | `POST /api/communities`<br/>`SupabaseService.create_community_atomic` | `communities`, `community_memberships`, `roles` | PostgREST RPC call to `rpc_create_community_atomic` | **NO IMPACT** (Green) | RPC function explicitly granted `EXECUTE` to `service_role`. `service_role` bypasses table RLS to insert into `communities` and `community_memberships`. |
| **3. Invitations & Acceptance** | `POST /api/invitations`<br/>`POST /api/invitations/accept`<br/>`SupabaseService.accept_invitation` | `invitations`, `invitation_responses`, `community_memberships` | PostgREST RPC calls to `rpc_accept_invitation_atomic`, etc. | **NO IMPACT** (Green) | All 4 invitation RPC functions have explicit `EXECUTE` grants to `service_role`. Function-internal updates succeed via `SECURITY DEFINER`. |
| **4. Doctor Profiles & Roles** | `GET /api/doctors/profile`<br/>`PUT /api/doctors/profile`<br/>`SupabaseService.sync_user_profile` | `doctors`, `users`, `user_profiles` | PostgREST REST calls to `/rest/v1/doctors`, `/rest/v1/users` | **NO IMPACT** (Green) | `service_role` has full `SELECT`/`UPDATE` privileges. V2 adds `is_active BOOLEAN` to `doctors` to ensure active status filtering succeeds. |
| **5. Camp Creation & Listing** | `POST /api/camps`<br/>`GET /api/camps`<br/>`GET /api/camps/<id>`<br/>`SupabaseService.list_camps_with_stats` | `camps`, `communities`, `camp_staff_assignments` | PostgREST REST calls to `/rest/v1/camps` | **NO IMPACT** (Green) | `service_role` has full table privileges. Composite index `idx_camps_community` already present. |
| **6. Record Creation, Edit & Approval** | `POST /api/records`<br/>`PUT /api/records/<id>`<br/>`POST /api/records/<id>/approve`<br/>`SupabaseService.insert_record` | `records`, `adult_records`, `paediatric_records` | PostgREST REST calls to `/rest/v1/records` | **NO IMPACT** (Green) | V2 adds all 15 missing operational columns to `records` (`edit_history`, `approved_by`, `locked_by`, etc.). `service_role` writes without RLS restriction. |
| **7. Adult & Paediatric Retrieval** | `GET /api/records`<br/>`GET /api/records/<id>`<br/>`GET /api/records/paediatric/<id>`<br/>`SupabaseService.get_record_by_id` | `records`, `adult_records`, `paediatric_records` | PostgREST REST calls with `select=*` | **NO IMPACT** (Green) | 100% column parity between parent and child tables in V2 ensures seamless hydration without dropped fields. |
| **8. Private File Authorization** | `GET /api/files/<file_id>`<br/>`app.py::get_private_file`<br/>`check_field_worker_file_access` | `files`, `records`, `camps` (plus local disk binary) | Flask queries Supabase `files` and `records`, then serves file via `send_file()` from local disk | **NO IMPACT** (Green) | Task 9A authorization evaluates `file.record_id` and `record.scanned_by` retrieved via `service_role`. Binaries remain securely on local disk outside static root. |
| **9. Usage Ledger & Audit Logging** | `RecordService.log_usage`<br/>`app.py::record_audit_log` | `usage_ledger`, `record_audit_log` | PostgREST REST calls to `/rest/v1/usage_ledger` | **NO IMPACT** (Green) | `service_role` has full `INSERT` privileges. Audit records persist normally. |
| **10. Excel MIS Exports** | `GET /api/camps/<id>/export`<br/>`RecordService.generate_excel_export` | In-memory processing of records retrieved from Supabase | Flask retrieves record dicts via `service_role`, compiles `.xlsx`, and streams to client | **NO IMPACT** (Green) | Exporter processes top-level dictionary fields and `structured_record`. Tested 100% compatible in Task 8. Zero database writes during export. |
| **11. Direct Client Requests (Anon Key)** | Any rogue script or external probe sending `apikey: <anon_key>` to PostgREST | All 30 public tables | Direct PostgREST HTTP queries | **STRICTLY BLOCKED** (Intended Security Outcome) | Tables have RLS enabled with default-deny and no SELECT/INSERT privileges for `anon`. Direct probes return HTTP 401/403 or empty sets `[]`. Zero PII leakage. |

---

## 3. Potential Failure Modes Identified & Mitigated in V2

1. **PostgREST Schema Reload Crash (Mitigated):**
   - *Failure Mode:* Revoking schema `USAGE` causes PostgREST to fail health checks and crash.
   - *Mitigation in V2:* `GRANT USAGE ON SCHEMA public` is preserved for `anon` and `authenticated`. Security is enforced at the table level and via RLS.
2. **Extension Routine Lockout (Mitigated):**
   - *Failure Mode:* Blanket routine revocation disables `uuid_generate_v4()` for table defaults.
   - *Mitigation in V2:* Targeted revocation by exact function signature for the 5 business RPCs only.
3. **Table Owner Lockout via `FORCE RLS` (Mitigated):**
   - *Failure Mode:* `FORCE ROW LEVEL SECURITY` blocks administrative tools and table owners (`postgres`).
   - *Mitigation in V2:* Standard `ENABLE ROW LEVEL SECURITY` blocks non-owner clients while leaving administrative maintenance functional.
4. **Child-Parent Type Mismatch (Mitigated):**
   - *Failure Mode:* Typing `records.edit_history` as `JSONB` while `adult_records.edit_history` is `TEXT` causes schema divergence.
   - *Mitigation in V2:* Strict data type alignment across parent and child tables.
