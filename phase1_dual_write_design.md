# Ink2Insights — Phase 1: Dual-Write Architecture & Operational Verification Specification

**Document Version:** 1.0.0  
**Target Window:** 48–72 Hours Verification Gate  
**Authoritative Store:** Supabase PostgreSQL  
**Shadow Store:** SQLite (`backend/clinic_records.db`)  
**Status:** Implementation Blueprint (Ready for Review)

---

## 1. Executive Overview

During the transition from SQLite to Supabase PostgreSQL as the primary operational database, the application will operate in a **Supabase-First Dual-Write Mode**. 

The design enforces three non-negotiable principles:
1. **Supabase is Authoritative:** Every operational write (create camp, save record, update clinical notes, link files) commits to Supabase first. If Supabase fails, the user request fails.
2. **Shadow Failure Isolation:** A failure to write to the SQLite shadow copy **never fails the user request** and **never causes a silent rollback or failover to SQLite**.
3. **Controlled Sunsetting:** SQLite is maintained strictly as an emergency snapshot for 48–72 hours of production traffic. Once verification gates pass, SQLite writes are disabled permanently.

---

## 2. Sequence of Operations

```mermaid
sequenceDiagram
    autonumber
    actor Client as Operator / Field Worker
    participant Flask as Flask Application / Repository
    participant Supabase as Supabase PostgreSQL (Authoritative)
    participant ShadowQueue as Memory/Disk Shadow Queue
    participant SQLite as SQLite (Shadow Copy)

    Client->>Flask: POST /api/records (Save Form Data)
    Note over Flask: 1. Generate client UUID for record & children<br/>2. Validate payload schema & clinical rules

    Flask->>Supabase: 3. Synchronous Primary Write (records + adult/paed child)
    alt Supabase Primary Write Succeeds
        Supabase-->>Flask: 200/201 OK (Transaction Committed)
        Flask->>ShadowQueue: 4. Dispatch Async Shadow Task (record_id, payload)
        Flask-->>Client: 201 Created (Success returned immediately)
        
        ShadowQueue->>SQLite: 5. Execute Idempotent Mirror Write
        alt SQLite Shadow Succeeds
            SQLite-->>ShadowQueue: 200 OK (Mirrored)
        else SQLite Shadow Fails (e.g. database locked)
            SQLite-->>ShadowQueue: SQLite Busy / Error
            ShadowQueue->>ShadowQueue: 6. Enqueue to Dead-Letter Queue (DLQ) with backoff retry
            Note over ShadowQueue: Alert ops log; primary is unaffected
        end
    else Supabase Primary Write Fails
        Supabase-->>Flask: Error (500 / Network Timeout)
        Note over Flask: 7. Abort shadow write completely.<br/>Zero phantom records in SQLite.
        Flask-->>Client: 500 Internal Server Error (Write Rejected)
    end
```

---

## 3. Step-by-Step Write Path Architecture

### 3.1 Pre-allocated UUIDs
To guarantee absolute foreign key and ID consistency between Supabase and SQLite:
- Every record ID, child table ID, camp ID, and file ID is generated in Python using `str(uuid.uuid4())` or domain prefixes (e.g., `rec_<uuid_hex>`) **before** invoking the database layer.
- Neither database relies on autoincrementing integers or database-generated default UUIDs for identity resolution.

### 3.2 Synchronous Primary Write (Supabase)
1. The repository wrapper initiates the write against Supabase via `SupabaseService`.
2. For clinical records, the parent `records` row and child `adult_records` or `paediatric_records` row are written sequentially. If the child write fails, a compensating deletion or rollback is executed against the parent row.
3. If Supabase returns an error (HTTP 4xx/5xx or timeout):
   - The error is logged with structured diagnostic metadata.
   - The user request is terminated immediately with an HTTP 500 response.
   - **No shadow write is attempted in SQLite.**

### 3.3 Asynchronous / Guarded Shadow Write (SQLite)
Once Supabase confirms success:
1. The payload is submitted to the shadow executor (`DualWriteManager`).
2. SQLite executes an idempotent upsert:
   ```sql
   INSERT OR REPLACE INTO records (...) VALUES (...);
   INSERT OR REPLACE INTO adult_records (...) VALUES (...);
   ```
3. The shadow write executes inside an isolated `try...except` block with a 5.0-second SQLite `busy_timeout`.
4. If SQLite throws `sqlite3.OperationalError: database is locked` or any other exception:
   - The failure is caught and logged to `sqlite_shadow_failures` log.
   - The transaction is recorded in a dead-letter retry queue.
   - The user request has already succeeded.

---

## 4. Failure Handling, Dead-Letter Queue & Reconciliation

### 4.1 Dead-Letter Queue (DLQ)
When an SQLite shadow write fails:
1. The complete serialized write payload is appended to `backend/data/shadow_dlq.jsonl`.
2. A background worker (`shadow_reconciler.py`) runs every 5 minutes:
   - Reads pending DLQ items.
   - Re-attempts idempotent insertion into SQLite.
   - Alerts engineers if a record fails more than 5 retry attempts.

### 4.2 Automated Hourly Drift Monitor
An automated cron job compares record counts and recent modifications:
```sql
-- Supabase Drift Query
SELECT COUNT(*) FROM records WHERE created_at >= NOW() - INTERVAL '1 hour';

-- SQLite Drift Query
SELECT COUNT(*) FROM records WHERE created_at >= datetime('now', '-1 hour');
```
If $\text{Count}_{\text{Supabase}} - \text{Count}_{\text{SQLite}} \ne 0$, an operational alert is triggered.

---

## 5. Verification Gates (48–72 Hour Window)

The cutover enters a strict 48–72-hour validation period. To graduate from dual-write mode to single-primary Supabase operations, all four gates must pass:

| Gate | Criterion | Target Threshold | Verification Method |
| :---: | :--- | :--- | :--- |
| **Gate 1** | **Primary Write Reliability** | 99.9% write success rate over 72h | Zero unhandled Supabase API errors; p95 latency < 350ms. |
| **Gate 2** | **Shadow Parity & Zero Drift** | $\Delta = 0$ records between stores | Automated checksum diff script: `diff_records(supabase, sqlite)`. |
| **Gate 3** | **End-to-End Operational Lifecycle** | 100% feature verification | 50+ real or synthetic camps processed: intake scan → AI extraction → doctor approval → Excel export download. |
| **Gate 4** | **Excel MIS Export Parity** | 100% cell & highlight parity | Export identical camp from both stores; byte-level comparison of clinical columns. |

---

## 6. Safe Transition of SQLite to Read-Only Archive

Once Gates 1 through 4 have received formal sign-off:

1. **Disable Shadow Writes in Configuration:**
   ```bash
   # backend/.env
   PRIMARY_DATABASE=supabase
   ENABLE_SQLITE_SHADOW_WRITE=false
   ```
2. **Execute Final Reconciliation:**
   Run `sync_supabase_to_sqlite.py` to ensure any delayed DLQ writes are completely flushed.
3. **Lock SQLite Database File:**
   Set the SQLite file permissions to read-only at the OS level:
   ```powershell
   # Windows PowerShell
   Set-ItemProperty -Path "backend/clinic_records.db" -Name IsReadOnly -Value $true
   ```
4. **Archive Cold Snapshot:**
   Create a compressed, timestamped cold archive:
   `backend/data/backups/clinic_records_final_cutover_<timestamp>.db.gz`
5. **Retire Shadow Code:**
   In Phase 6, remove the shadow worker logic from the application repository.
