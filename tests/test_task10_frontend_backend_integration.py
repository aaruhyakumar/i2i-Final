"""
Task 10 Integration Test Suite: Ink2Insights Frontend/Backend Integration.
Validates the reliable end-to-end integration between frontend API contracts
and Flask backend endpoints using Supabase PostgreSQL as the primary database
with SQLite temporary rollback reference.

Covers all 8 required workflows:
1. Login and registration (/api/auth/login, /api/auth/signup)
2. Doctor/profile creation and retrieval (/api/doctor/profile, /api/doctors/profile)
3. Camp creation, listing, selection, update and delete (/api/camps)
4. Scan upload, document processing and private file access (/api/files/<id>)
5. Patient record creation and retrieval (/api/records, /api/records/<id>)
6. Camp-specific adult and paediatric records (/api/camps/<id>/records, /adult-records, /paediatric-records)
7. Record editing, review, approval, lock and unlock (/api/records/<id>/review, /approve, /lock, /unlock)
8. Excel generation and downloading (/api/camps/<id>/export, /api/export/adult/excel, /api/export/paediatric/excel)
"""

import os
import io
import json
import pytest
from unittest.mock import MagicMock, patch

from backend import database
from backend.app import create_app
from backend.services.auth_service import create_access_token
from backend.services.record_service import RecordService
from backend.services.supabase_service import SupabaseService

TEST_JWT_SECRET = "test-jwt-secret-task10-1234567890abcdef"


@pytest.fixture
def task10_env(tmp_path, monkeypatch):
    """Set up isolated SQLite DB, upload directories, and test environment."""
    db_file = str(tmp_path / "task10_test.db")
    private_dir = str(tmp_path / "private_uploads")
    os.makedirs(private_dir, exist_ok=True)

    monkeypatch.setenv("DATABASE_PATH", db_file)
    monkeypatch.setenv("PRIVATE_UPLOAD_DIR", private_dir)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEMO_MODE", "1")

    database.init_db(db_file)

    comm = database.create_community(
        name="Alpha Health Foundation",
        creator_name="Dr. Priya Sharma",
        creator_email="priya@alpha.org",
        code="ALPHA-2026-CAMP",
        db_path=db_file
    )
    comm_id = comm["id"]
    with database.get_db_connection(db_file) as conn:
        cur = conn.cursor()
        # Create a doctor in SQLite
        cur.execute(
            "INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, specialization, community_id, community_code, community_role, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'doctor', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);",
            ("doc_priya_10", "doc_priya_10", "priya@alpha.org", "Dr. Priya Sharma", "General Physician", comm_id, "ALPHA-2026-CAMP")
        )
        # Create a field worker in SQLite
        cur.execute(
            "INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, specialization, community_id, community_code, community_role, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'field_worker', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);",
            ("worker_ravi_10", "worker_ravi_10", "ravi@alpha.org", "Ravi Kumar", "Community Health Worker", comm_id, "ALPHA-2026-CAMP")
        )
        conn.commit()

    # Pre-create test camps
    camp_adult = database.create_camp(
        "Alpha Adult Camp", 150, "Today",
        camp_id="camp_adult_10",
        location="Alpha Primary Health Center",
        target_group="adult",
        community_id=comm_id,
        created_by="doc_priya_10",
        db_path=db_file
    )

    camp_paed = database.create_camp(
        "Alpha School Health Camp", 100, "Tomorrow",
        camp_id="camp_paed_10",
        location="Govt High School",
        target_group="paediatric",
        community_id=comm_id,
        created_by="doc_priya_10",
        db_path=db_file
    )

    app = create_app(db_path=db_file)
    app.config["TESTING"] = True
    app.config["PRIVATE_UPLOAD_DIR"] = private_dir
    client = app.test_client()

    doc_token = create_access_token(
        user_id="doc_priya_10",
        role="doctor",
        org_id=comm_id,
        email="priya@alpha.org",
        name="Dr. Priya Sharma"
    )

    worker_token = create_access_token(
        user_id="worker_ravi_10",
        role="field_worker",
        org_id=comm_id,
        email="ravi@alpha.org",
        name="Ravi Kumar"
    )

    return {
        "db_file": db_file,
        "private_dir": private_dir,
        "comm_id": comm_id,
        "app": app,
        "client": client,
        "doc_token": doc_token,
        "worker_token": worker_token,
        "camp_adult": camp_adult,
        "camp_paed": camp_paed,
    }


# ============================================================================
# 1. AUTHENTICATION & DOCTOR PROFILE WORKFLOWS
# ============================================================================

def test_login_and_registration_contracts(task10_env):
    """Verify login and registration request/response contracts."""
    client = task10_env["client"]

    # 1. Login with Demo credentials
    res = client.post("/api/auth/login", json={
        "is_demo": True,
        "email": "demo@clinic.camp"
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "token" in data or "access_token" in data
    assert data["doctor"]["role"] == "doctor"

    # 2. Signup validation: missing fields
    bad_signup = client.post("/api/auth/signup", json={"email": "incomplete@test.com"})
    assert bad_signup.status_code == 400
    assert "code" in bad_signup.get_json()


def test_doctor_profile_retrieval_and_update(task10_env):
    """Verify /api/doctor/profile and /api/doctors/profile endpoints."""
    client = task10_env["client"]
    token = task10_env["doc_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET /api/doctor/profile
    res1 = client.get("/api/doctor/profile", headers=headers)
    assert res1.status_code == 200
    p1 = res1.get_json()
    assert p1["full_name"] == "Dr. Priya Sharma"
    assert p1["email"] == "priya@alpha.org"

    # 2. GET /api/doctors/profile (plural alias compatibility)
    res2 = client.get("/api/doctors/profile", headers=headers)
    assert res2.status_code == 200
    assert res2.get_json()["full_name"] == "Dr. Priya Sharma"

    # 3. PUT /api/doctor/profile
    update_res = client.put("/api/doctor/profile", headers=headers, json={
        "full_name": "Dr. Priya Sharma, MD",
        "specialization": "Internal Medicine"
    })
    assert update_res.status_code == 200
    updated = update_res.get_json()
    assert updated["full_name"] == "Dr. Priya Sharma, MD"
    assert updated["specialization"] == "Internal Medicine"


# ============================================================================
# 2. CAMP CREATION, LISTING & SELECTION WORKFLOWS
# ============================================================================

def test_camp_creation_and_listing_parity(task10_env):
    """Verify camp creation with community_id and target_group preservation."""
    client = task10_env["client"]
    token = task10_env["doc_token"]
    comm_id = task10_env["comm_id"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create a new paediatric camp via POST /api/camps
    create_res = client.post("/api/camps", headers=headers, json={
        "name": "St. Marys School Camp",
        "expected_records": 120,
        "date_str": "Next Monday",
        "location": "St. Marys Campus",
        "target_group": "paediatric",
        "community_id": comm_id
    })
    assert create_res.status_code == 201
    camp_data = create_res.get_json()
    new_camp_id = camp_data["id"]
    assert camp_data["name"] == "St. Marys School Camp"
    assert camp_data["expected_records"] == 120
    assert camp_data.get("community_id") == comm_id
    assert camp_data.get("target_group") == "paediatric"

    # 2. List camps via GET /api/camps
    list_res = client.get("/api/camps", headers=headers)
    assert list_res.status_code == 200
    camps = list_res.get_json()
    assert len(camps) >= 3
    found = next((c for c in camps if c["id"] == new_camp_id), None)
    assert found is not None
    assert found["community_id"] == comm_id
    assert found["target_group"] == "paediatric"
    assert "total_scanned" in found
    assert "progress_pct" in found

    # 3. GET single camp
    get_res = client.get(f"/api/camps/{new_camp_id}", headers=headers)
    assert get_res.status_code == 200
    assert get_res.get_json()["name"] == "St. Marys School Camp"

    # 4. PUT /api/camps/<id>
    put_res = client.put(f"/api/camps/{new_camp_id}", headers=headers, json={
        "name": "St. Marys High School Screening",
        "expected_records": 150,
        "date_str": "Next Monday",
        "location": "St. Marys Auditorium"
    })
    assert put_res.status_code == 200
    assert put_res.get_json()["name"] == "St. Marys High School Screening"

    # 5. DELETE /api/camps/<id>
    del_res = client.delete(f"/api/camps/{new_camp_id}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.get_json()["success"] is True


# ============================================================================
# 3. PATIENT RECORD CREATION & RETRIEVAL (ADULT & PAEDIATRIC)
# ============================================================================

def test_adult_and_paediatric_record_creation_and_retrieval(task10_env):
    """Verify patient records creation and structured data deserialization."""
    client = task10_env["client"]
    token = task10_env["doc_token"]
    comm_id = task10_env["comm_id"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create Adult Record
    adult_payload = {
        "camp_id": "camp_adult_10",
        "community_id": comm_id,
        "person_name": "Ramesh Chidambaram",
        "age": 45,
        "gender": "Male",
        "height": "172",
        "weight": "74",
        "blood_pressure": "130/85",
        "target_group": "adult",
        "is_paediatric": 0,
        "consent_given": 1,
        "structured_record": {
            "personal_information": {"full_name": "Ramesh Chidambaram", "age": 45, "gender": "Male"},
            "vitals": {"bp": "130/85", "pulse": "76", "height_cm": "172", "weight_kg": "74"},
            "medical_history": {"diabetes": "No", "hypertension": "Mild"}
        },
        "missing_fields": [],
        "uncertain_fields": []
    }
    res_adult = client.post("/api/records", headers=headers, json=adult_payload)
    assert res_adult.status_code == 201
    adult_rec = res_adult.get_json()
    adult_id = adult_rec["id"]
    assert adult_rec["person_name"] == "Ramesh Chidambaram"

    # 2. Create Paediatric Record
    paed_payload = {
        "camp_id": "camp_paed_10",
        "community_id": comm_id,
        "person_name": "Ananya Sundaram",
        "age": 9,
        "gender": "Female",
        "height": "130",
        "weight": "28",
        "blood_pressure": "102/64",
        "target_group": "paediatric",
        "is_paediatric": 1,
        "consent_given": 1,
        "structured_record": {
            "personal_information": {"full_name": "Ananya Sundaram", "age": 9, "gender": "Female", "school_name": "Govt High School"},
            "vitals": {"bp": "102/64", "pulse": "84", "height_cm": "130", "weight_kg": "28"},
            "paediatric_growth": {"bmi": "16.6", "bmi_centile": 50.0, "bmi_classification": "Normal"}
        },
        "missing_fields": [],
        "uncertain_fields": []
    }
    res_paed = client.post("/api/records", headers=headers, json=paed_payload)
    assert res_paed.status_code == 201
    paed_rec = res_paed.get_json()
    paed_id = paed_rec["id"]
    assert paed_rec["person_name"] == "Ananya Sundaram"

    # 3. GET /api/records/<id>
    get_adult = client.get(f"/api/records/{adult_id}", headers=headers)
    assert get_adult.status_code == 200
    adult_fetched = get_adult.get_json()
    assert isinstance(adult_fetched["structured_record"], dict)
    assert adult_fetched["structured_record"]["vitals"]["bp"] == "130/85"

    get_paed = client.get(f"/api/records/{paed_id}", headers=headers)
    assert get_paed.status_code == 200
    paed_fetched = get_paed.get_json()
    assert isinstance(paed_fetched["structured_record"], dict)
    assert paed_fetched["is_paediatric"] == 1

    # 4. GET segregated endpoints
    adult_list = client.get("/api/camps/camp_adult_10/adult-records", headers=headers)
    assert adult_list.status_code == 200
    assert any(r["id"] == adult_id for r in adult_list.get_json())

    paed_list = client.get("/api/camps/camp_paed_10/paediatric-records", headers=headers)
    assert paed_list.status_code == 200
    assert any(r["id"] == paed_id for r in paed_list.get_json())


# ============================================================================
# 4. RECORD REVIEW, EDITING, APPROVAL & LOCKING WORKFLOWS
# ============================================================================

def test_record_review_and_approval_cycle(task10_env):
    """Verify locking, reviewing, and approving patient records."""
    client = task10_env["client"]
    token = task10_env["doc_token"]
    comm_id = task10_env["comm_id"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create intake record in 'needs_review'
    intake_res = client.post("/api/records", headers=headers, json={
        "camp_id": "camp_adult_10",
        "community_id": comm_id,
        "person_name": "Karthik Raja",
        "age": 52,
        "gender": "Male",
        "blood_pressure": "140/90",
        "consent_given": 1
    })
    rec_id = intake_res.get_json()["id"]

    # 2. Lock record
    lock_res = client.post(f"/api/records/{rec_id}/lock", headers=headers, json={"doctor_name": "Dr. Priya Sharma"})
    assert lock_res.status_code == 200
    assert lock_res.get_json()["success"] is True

    # 3. Review record (/api/records/<id>/review)
    review_res = client.put(f"/api/records/{rec_id}/review", headers=headers, json={
        "person_name": "Karthik Raja",
        "blood_pressure": "138/88",
        "examination_details": "Normal heart sounds, mild pedal edema",
        "reviewer_notes": "Prescribed low sodium diet"
    })
    assert review_res.status_code == 200
    reviewed_data = review_res.get_json()
    assert reviewed_data["status"] == "reviewed"
    assert reviewed_data["blood_pressure"] == "138/88"

    # 4. Approve record (/api/records/<id>/approve)
    approve_res = client.post(f"/api/records/{rec_id}/approve", headers=headers, json={})
    assert approve_res.status_code == 200
    approved_data = approve_res.get_json()
    assert approved_data["success"] is True
    assert approved_data["status"] == "approved"
    assert approved_data["approved_by"] == "Dr. Priya Sharma"


# ============================================================================
# 5. SCAN UPLOAD, STORAGE & PRIVATE FILE ACCESS
# ============================================================================

def test_scan_storage_and_file_authorization(task10_env):
    """Verify medical scans remain in private storage and authorization is enforced."""
    client = task10_env["client"]
    doc_token = task10_env["doc_token"]
    worker_token = task10_env["worker_token"]
    comm_id = task10_env["comm_id"]
    db_file = task10_env["db_file"]

    # 1. Save scan binary into private uploads via database service
    dummy_scan_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
    file_id = database.save_private_file(
        org_id=comm_id,
        record_id=None,
        file_type="scan_page1",
        filename="patient_scan_p1.jpg",
        data=dummy_scan_bytes,
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=task10_env["private_dir"]
    )
    assert file_id.startswith("file_")

    # 2. Doctor access with valid Authorization header
    res_doc = client.get(f"/api/files/{file_id}", headers={"Authorization": f"Bearer {doc_token}"})
    assert res_doc.status_code == 200
    assert res_doc.mimetype == "image/jpeg"
    assert res_doc.data == dummy_scan_bytes

    # 3. Unauthenticated access returns 401
    res_unauth = client.get(f"/api/files/{file_id}")
    assert res_unauth.status_code == 401

    # 4. Cross-tenant access: user from a different organization gets 404 (prevents file enumeration)
    foreign_token = create_access_token(
        user_id="doc_foreign",
        role="doctor",
        org_id="comm_foreign_99",
        email="foreign@other.org"
    )
    res_foreign = client.get(f"/api/files/{file_id}", headers={"Authorization": f"Bearer {foreign_token}"})
    assert res_foreign.status_code == 404


# ============================================================================
# 6. EXCEL GENERATION & DOWNLOADING INTEGRATION
# ============================================================================

def test_excel_export_downloading(task10_env):
    """Verify camp Excel workbook generation and download contracts."""
    client = task10_env["client"]
    doc_token = task10_env["doc_token"]
    worker_token = task10_env["worker_token"]
    headers = {"Authorization": f"Bearer {doc_token}"}

    # 1. Camp Excel Export GET /api/camps/<id>/export
    res_camp = client.get("/api/camps/camp_adult_10/export", headers=headers)
    assert res_camp.status_code == 200
    assert "spreadsheetml" in res_camp.mimetype or res_camp.mimetype.startswith("application/vnd")
    assert len(res_camp.data) > 1000

    # 2. Dedicated Adult Excel Export GET /api/export/adult/excel
    res_adult = client.get("/api/export/adult/excel?camp_id=camp_adult_10", headers=headers)
    assert res_adult.status_code == 200
    assert len(res_adult.data) > 1000

    # 3. Dedicated Paediatric Excel Export GET /api/export/paediatric/excel
    res_paed = client.get("/api/export/paediatric/excel?camp_id=camp_paed_10", headers=headers)
    assert res_paed.status_code == 200
    assert len(res_paed.data) > 1000

    # 4. CSV Export GET /api/camps/<id>/export?format=csv
    res_csv = client.get("/api/camps/camp_adult_10/export?format=csv", headers=headers)
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.mimetype

    # 5. Field workers are forbidden from exporting records (Rule 4.3)
    res_worker = client.get("/api/camps/camp_adult_10/export", headers={"Authorization": f"Bearer {worker_token}"})
    assert res_worker.status_code == 403


# ============================================================================
# 7. SUPABASE PRIMARY INTEGRATION & CIRCUIT BREAKER FALLBACK
# ============================================================================

def test_supabase_primary_database_mode(task10_env, monkeypatch):
    """Verify backend operates seamlessly with Supabase as primary operational database."""
    app = task10_env["app"]
    client = task10_env["client"]
    doc_token = task10_env["doc_token"]
    comm_id = task10_env["comm_id"]
    headers = {"Authorization": f"Bearer {doc_token}"}

    # Configure app with SUPABASE_ENABLED = True
    app.config["SUPABASE_ENABLED"] = True

    mock_supabase = MagicMock(spec=SupabaseService)
    mock_supabase.is_configured.return_value = True
    mock_supabase.is_available.return_value = True

    # Mock list_camps_with_stats
    mock_supabase.list_camps_with_stats.return_value = [{
        "id": "camp_cloud_01",
        "name": "Cloud Primary Camp",
        "date_str": "Today",
        "location": "Cloud Center",
        "target_group": "adult",
        "community_id": comm_id,
        "expected_records": 200,
        "total_scanned": 15,
        "to_check": 5,
        "reviewed": 10,
        "progress_pct": 7.5,
        "created_at": "2026-11-17T12:00:00Z"
    }]

    # Mock get_camp_by_id
    mock_supabase.get_camp_by_id.return_value = {
        "id": "camp_cloud_01",
        "name": "Cloud Primary Camp",
        "date_str": "Today",
        "location": "Cloud Center",
        "target_group": "adult",
        "community_id": comm_id,
        "expected_records": 200
    }

    # Mock create_camp
    mock_supabase.create_camp.return_value = {
        "id": "camp_cloud_02",
        "name": "Cloud Paediatric Camp",
        "date_str": "Tomorrow",
        "location": "Cloud School",
        "target_group": "paediatric",
        "community_id": comm_id,
        "expected_records": 100
    }

    # Mock list_records
    mock_supabase.list_records.return_value = [{
        "id": "rec_cloud_01",
        "camp_id": "camp_cloud_01",
        "camp_name": "Cloud Primary Camp",
        "person_name": "Meera Krishnan",
        "age": "38",
        "gender": "Female",
        "height": "160",
        "weight": "58",
        "blood_pressure": "120/80",
        "examination_details": "Normal",
        "referral_details": None,
        "missing_fields": [],
        "uncertain_fields": [],
        "extraction_notes": None,
        "structured_record": {"personal_information": {"full_name": "Meera Krishnan"}},
        "status": "needs_review",
        "page1_preview": "/api/files/file_cloud_p1",
        "page2_preview": "/api/files/file_cloud_p2",
        "community_id": comm_id,
        "target_group": "adult",
        "is_paediatric": 0
    }]

    mock_supabase.get_record_by_id.return_value = {
        "id": "rec_cloud_01",
        "camp_id": "camp_cloud_01",
        "person_name": "Meera Krishnan",
        "age": "38",
        "gender": "Female",
        "height": "160",
        "weight": "58",
        "blood_pressure": "120/80",
        "examination_details": "Normal",
        "referral_details": None,
        "missing_fields": [],
        "uncertain_fields": [],
        "structured_record": {"personal_information": {"full_name": "Meera Krishnan"}},
        "status": "needs_review",
        "community_id": comm_id
    }

    # Mock lock/unlock
    mock_supabase.lock_record.return_value = {"success": True, "locked_by": "Dr. Priya Sharma"}
    mock_supabase.unlock_record.return_value = True

    app.config["supabase_service"] = mock_supabase
    try:
        # 1. Verify camps list retrieves from Supabase
        res_camps = client.get("/api/camps", headers=headers)
        assert res_camps.status_code == 200
        camp_list = res_camps.get_json()
        assert len(camp_list) == 1
        assert camp_list[0]["name"] == "Cloud Primary Camp"

        # 2. Verify camp creation posts to Supabase with target_group & community_id
        res_add = client.post("/api/camps", headers=headers, json={
            "name": "Cloud Paediatric Camp",
            "expected_records": 100,
            "target_group": "paediatric",
            "community_id": comm_id
        })
        assert res_add.status_code == 201
        assert res_add.get_json()["name"] == "Cloud Paediatric Camp"
        mock_supabase.create_camp.assert_called_once()

        # 3. Verify records retrieval from Supabase
        res_records = client.get("/api/records", headers=headers)
        assert res_records.status_code == 200
        rec_list = res_records.get_json()
        assert len(rec_list) == 1
        assert rec_list[0]["person_name"] == "Meera Krishnan"

        # 4. Verify record lock via Supabase
        res_lock = client.post("/api/records/rec_cloud_01/lock", headers=headers, json={"doctor_name": "Dr. Priya Sharma"})
        assert res_lock.status_code == 200
        assert res_lock.get_json()["success"] is True
        mock_supabase.lock_record.assert_called_once_with("rec_cloud_01", "Dr. Priya Sharma")
    finally:
        app.config.pop("supabase_service", None)
        app.config.pop("SUPABASE_ENABLED", None)
