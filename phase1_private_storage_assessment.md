# Ink2Insights — Phase 1: Private File Storage Architecture & Security Assessment

**Document Version:** 1.0.0  
**Binary Location:** Private Local Disk (`data/private_uploads/`)  
**Metadata Store:** Supabase PostgreSQL (`public.files`)  
**Scope:** File Storage Security, Multi-Instance Persistence & Task 9A Authorization  
**Status:** Architecture Assessment (Ready for Review)

---

## 1. Executive Summary

Under the approved architecture:
1. **Medical scan image binaries (`.jpg`, `.png`, `.pdf`) remain exclusively on private local disk storage.**
2. **Supabase PostgreSQL stores only file metadata, ownership references, and storage paths in `public.files`.**
3. **No public Supabase Storage bucket (`intake-scans`) is introduced.**
4. **Task 9A private scan file authorization remains fully enforced.**

This assessment evaluates filesystem persistence across restarts, multi-instance deployment implications, path normalization, and Task 9A authorization continuity.

---

## 2. Inventory & Storage Architecture

### 2.1 File Ingestion & Path Storage
When a physical paper intake form is photographed and uploaded:
1. `backend/app.py::upload_image()` receives image bytes via multipart form upload.
2. The image is saved to disk via `backend/database.py::save_private_file()`.
3. The binary is written to `data/private_uploads/<random_32char_hex>.jpg`.
4. A metadata record is registered in the database `files` table:
   - `id`: Unique file ID (`file_<24hex>`)
   - `org_id`: Tenant / organization ID
   - `record_id`: Nullable initially; linked to `records.id` upon form saving (Task 9B)
   - `file_type`: `scan_page1`, `scan_page2`, or `upload`
   - `original_filename`: Original camera filename
   - `storage_path`: Filesystem path to the encrypted/private binary
   - `mime_type`: `image/jpeg`, `image/png`, etc.
   - `file_size`: Binary size in bytes
   - `created_at`: UTC timestamp

### 2.2 Critical Discovery: Hardcoded Absolute Host Paths
Inspection of the existing `clinic_records.db` database revealed that `storage_path` values currently contain absolute Windows paths from a prior workstation:
```
E:\i2i-main\data\private_uploads\9816ac12d4ffc52fcd3abff3d47c8614.jpg
```
On the current host machine, the workspace is located at:
```
c:\Users\ADMIN\Downloads\i2i-main (2)\data\private_uploads\
```
If an absolute path from one host is stored in Supabase, any other host or container attempting `send_file(storage_path)` will fail with `FileNotFoundError`.

**Path Normalization Solution:**
1. The metadata stored in Supabase `files.storage_path` will store **normalized relative paths** (e.g. `private_uploads/9816ac12d4ffc52fcd3abff3d47c8614.jpg`) or the basename.
2. `get_private_file(file_id)` resolves the relative path against the runtime environment's `PRIVATE_UPLOAD_DIR`:
   ```python
   def resolve_storage_path(stored_path: str) -> str:
       basename = os.path.basename(stored_path)
       upload_dir = os.environ.get("PRIVATE_UPLOAD_DIR") or os.path.join(BASE_DIR, "data", "private_uploads")
       return os.path.join(upload_dir, basename)
   ```
This guarantees portability across Windows, Linux, Docker, and cloud environments.

---

## 3. Persistence Across Restarts & Deployment Topologies

### 3.1 Single-Instance Bare Metal / VM (Current Setup)
- **Persistence:** Local disk writes to `data/private_uploads/` persist indefinitely across application restarts, process reboots, and OS reboots.
- **Backup Requirement:** A local cron or scheduled backup task must rsync/copy `data/private_uploads/` to encrypted external/cold backup storage.

### 3.2 Multi-Instance / Containerized Deployment Assessment
If the Flask backend is scaled horizontally across multiple instances (e.g. Kubernetes, AWS ECS, or multiple Railway replicas):
- **Problem:** If Instance A handles the upload of Page 1 and stores it on its local ephemeral container disk, Instance B handling the doctor review will not find the file.
- **Solution Options for Multi-Instance:**
  1. **Option A (Shared Volume):** Mount a shared persistent network volume (NFS, AWS EFS, or Azure Files) to `data/private_uploads/` across all container instances.
  2. **Option B (Private Cloud Object Storage - Future Phase):** In a future phase, introduce a strictly private (non-public) Supabase Storage bucket or S3 bucket using pre-signed temporary URLs. *Out of scope for this phase.*
- **Current Phase Rule:** Single-instance or sticky-session shared-volume hosting is required.

---

## 4. Continuity of Task 9A File Authorization

In Task 9A, `check_field_worker_file_access()` was implemented in `backend/app.py` to prevent unauthorized file access:
```
GET /api/files/<file_id>
```

### Authorization Flow After Supabase Cutover:
1. **User Identity Resolution:** The user's role and identity are extracted from the verified Supabase JWT (`g.user_id`, `g.user_role`, `g.user_community_id`).
2. **Doctor & Admin Privileges:** Doctors and admins continue to enjoy full authorized access to view scans within their community.
3. **Field Worker Boundary Check:**
   - The file metadata is retrieved from Supabase: `file = supabase_service.get_file(file_id)`.
   - If `file.record_id` is present, the associated record is retrieved from Supabase: `record = supabase_service.get_record(file.record_id)`.
   - The field worker is authorized **if and only if**:
     - `record.scanned_by == g.user_id`, OR
     - The field worker is explicitly assigned to `record.camp_id` in `camp_staff_assignments`.
   - If the file is unlinked (`record_id IS NULL`), access is restricted to the specific worker who uploaded it.
   - Unauthorized attempts return **HTTP 403 Forbidden**.

Because authorization logic is enforced by Flask before executing `send_file()`, migrating the database to Supabase does **not weaken file security**. It actually improves it by centralizing ownership data.

---

## 5. Elimination of Dead Supabase Cloud Storage Calls

In `backend/app.py::upload_image()`, there is an existing legacy attempt:
```python
# Try Supabase Storage first
if supabase_service.is_configured():
    try:
        storage_path = f"{camp_id}/{filename}"
        public_url = supabase_service.upload_storage_image(...)
```
Since the `intake-scans` bucket does not exist and the user has explicitly confirmed that **no public cloud storage bucket should be introduced**, this code currently fails silently on every upload and falls through to local storage.

**Recommendation:**
In Phase 3 (Application Cutover), explicitly remove this dead cloud-storage branch from `upload_image()` to eliminate unnecessary HTTP round-trips and error logs.
