"""Tests for the three runtime error fixes:
1. SQLite Community Fallback (Dev/Demo vs. Strict Production Fail-Closed)
2. Scanned File Ownership in /api/extract
3. Usage & Billing Telemetry Role-Gate (/api/usage & /api/usage/ledger)
"""

import io
import os
import pytest
from unittest.mock import MagicMock

from backend.app import create_app
from backend import database
from backend.services.auth_service import create_access_token
from backend.services.supabase_service import SupabaseService

TEST_JWT_SECRET = "test_runtime_fixes_jwt_secret_32bytes_long!"


@pytest.fixture
def test_setup(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_runtime.db")
    private_dir = str(tmp_path / "private_uploads")
    os.makedirs(private_dir, exist_ok=True)

    monkeypatch.setenv("DATABASE_PATH", db_file)
    monkeypatch.setenv("PRIVATE_UPLOAD_DIR", private_dir)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("AI_PROVIDER", "claude")

    database.init_db(db_file)
    return {"db_file": db_file, "private_dir": private_dir, "monkeypatch": monkeypatch}


def test_sqlite_community_fallback_in_development(test_setup):
    """Safeguard 1: In development/demo mode, if Supabase has 0 memberships,
    fallback to SQLite doctor community and grant demo communities.
    """
    monkeypatch = test_setup["monkeypatch"]
    db_file = test_setup["db_file"]
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEMO_MODE", "1")

    # Doctor registered in SQLite with community comm_clinic_01
    database.get_or_create_doctor(
        email="doctor_dev@example.com",
        full_name="Dr. Developer",
        auth_id="doc_auth_123",
        community_id="comm_clinic_01",
        db_path=db_file
    )

    # Camp in comm_clinic_01
    database.create_camp(
        name="Outreach Camp Dev",
        expected_records=50,
        date_str="16 Nov",
        community_id="comm_clinic_01",
        db_path=db_file
    )

    # Supabase returns 0 memberships
    monkeypatch.setattr(SupabaseService, "is_available", lambda self: True)
    monkeypatch.setattr(SupabaseService, "get_user_community_memberships", lambda self, uid: [])

    app = create_app()
    app.config["TESTING"] = True
    app.config["SUPABASE_ENABLED"] = True

    client = app.test_client()
    token = create_access_token(
        user_id="doc_auth_123",
        role="doctor",
        org_id="comm_clinic_01",
        email="doctor_dev@example.com"
    )

    # In dev/demo mode, doctor can access camps and records
    resp_camps = client.get("/api/camps", headers={"Authorization": f"Bearer {token}"})
    assert resp_camps.status_code == 200, f"Expected 200, got {resp_camps.status_code}: {resp_camps.data}"

    resp_records = client.get("/api/records", headers={"Authorization": f"Bearer {token}"})
    assert resp_records.status_code == 200, f"Expected 200, got {resp_records.status_code}: {resp_records.data}"


def test_strict_production_fails_closed_without_fallback(test_setup):
    """Safeguard 1: In strict production (non-demo), when Supabase is enabled
    and returns 0 memberships, access MUST fail closed (403 MISSING_TENANT_IDENTITY).
    Never fall back to org_default or SQLite in production.
    """
    monkeypatch = test_setup["monkeypatch"]
    db_file = test_setup["db_file"]
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_MODE", "0")

    # Even if doctor exists in SQLite:
    database.get_or_create_doctor(
        email="doctor_prod@example.com",
        full_name="Dr. Production",
        auth_id="doc_prod_auth",
        community_id="comm_clinic_prod",
        db_path=db_file
    )

    # Supabase returns 0 memberships
    monkeypatch.setattr(SupabaseService, "is_available", lambda self: True)
    monkeypatch.setattr(SupabaseService, "get_user_community_memberships", lambda self, uid: [])

    app = create_app()
    app.config["TESTING"] = True
    app.config["SUPABASE_ENABLED"] = True

    client = app.test_client()
    token = create_access_token(
        user_id="doc_prod_auth",
        role="doctor",
        org_id="comm_clinic_prod",
        email="doctor_prod@example.com"
    )

    # In strict production, fail-closed: returns 403 MISSING_TENANT_IDENTITY
    resp_camps = client.get("/api/camps", headers={"Authorization": f"Bearer {token}"})
    assert resp_camps.status_code == 403
    assert resp_camps.get_json().get("code") == "MISSING_TENANT_IDENTITY"

    resp_records = client.get("/api/records", headers={"Authorization": f"Bearer {token}"})
    assert resp_records.status_code == 403
    assert resp_records.get_json().get("code") == "MISSING_TENANT_IDENTITY"


def test_supabase_memberships_preferred_when_available(test_setup):
    """Safeguard 1: Live Supabase memberships are always preferred when available."""
    monkeypatch = test_setup["monkeypatch"]
    db_file = test_setup["db_file"]
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_MODE", "0")

    # Supabase returns active membership for comm_supabase_123
    monkeypatch.setattr(SupabaseService, "is_available", lambda self: True)
    monkeypatch.setattr(
        SupabaseService,
        "get_user_community_memberships",
        lambda self, uid: [{"community_id": "comm_supabase_123", "status": "active", "role": "doctor"}]
    )
    monkeypatch.setattr(
        SupabaseService,
        "list_camps_with_stats",
        lambda self: [{"id": "camp_sb_01", "name": "Supabase Community Camp", "community_id": "comm_supabase_123"}]
    )

    app = create_app()
    app.config["TESTING"] = True
    app.config["SUPABASE_ENABLED"] = True

    client = app.test_client()
    token = create_access_token(
        user_id="doc_sb_user",
        role="doctor",
        org_id="comm_supabase_123",
        email="doc_sb@example.com"
    )

    resp_camps = client.get("/api/camps", headers={"Authorization": f"Bearer {token}"})
    assert resp_camps.status_code == 200
    camps = resp_camps.get_json()
    assert any(c["community_id"] == "comm_supabase_123" for c in camps)


def test_scanned_file_ownership_derivation_in_extract(test_setup):
    """Safeguard 2: Scanned File Ownership:
    Derive active community ID from authenticated user's verified membership.
    Ensure /api/extract stores files against that verified community.
    Never trust arbitrary frontend-supplied org_id.
    """
    monkeypatch = test_setup["monkeypatch"]
    db_file = test_setup["db_file"]
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEMO_MODE", "1")

    # Doctor is verified member of comm_real_tenant
    database.get_or_create_doctor(
        email="doctor_extract@example.com",
        full_name="Dr. Extract",
        auth_id="doc_extract_123",
        community_id="comm_real_tenant",
        db_path=db_file
    )

    camp = database.create_camp(
        name="Tenant Camp",
        expected_records=25,
        date_str="16 Nov",
        community_id="comm_real_tenant",
        db_path=db_file
    )
    camp_id = camp["id"]

    # Mock extract function in backend.app
    def mock_extract(**kwargs):
        return {
            "success": True,
            "record_id": kwargs.get("record_id") or "rec_test_001",
            "name": "Aarav Sharma",
            "age": 10,
            "gender": "Male",
            "student_name": "Aarav Sharma",
            "confidence_score": 0.95
        }

    monkeypatch.setattr("backend.app.ai_extract", mock_extract)

    app = create_app()
    app.config["TESTING"] = True
    app.config["SUPABASE_ENABLED"] = False

    client = app.test_client()
    token = create_access_token(
        user_id="doc_extract_123",
        role="doctor",
        org_id="comm_real_tenant",
        email="doctor_extract@example.com"
    )

    fake_img1 = (io.BytesIO(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb" + b"\x00" * 100), "p1.jpg")
    fake_img2 = (io.BytesIO(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb" + b"\x00" * 100), "p2.jpg")

    # Client attempts to spoof an arbitrary org_id
    resp = client.post(
        "/api/extract",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Org-ID": "attacker_arbitrary_org"  # Untrusted header
        },
        data={
            "student_form": fake_img1,
            "health_assessment": fake_img2,
            "camp_id": camp_id,
            "org_id": "attacker_arbitrary_org"  # Untrusted form field
        },
        content_type="multipart/form-data"
    )
    assert resp.status_code == 200, f"Extract failed: {resp.data}"
    extracted_data = resp.get_json()

    # Check files saved in database
    with database.get_db_connection(db_file) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, org_id FROM files ORDER BY id DESC LIMIT 2;")
        rows = cur.fetchall()
        assert len(rows) == 2
        for r in rows:
            # File MUST NOT be assigned to spoofed attacker org
            assert r["org_id"] != "attacker_arbitrary_org"
            # File MUST be assigned to the camp/doctor verified community
            assert r["org_id"] == "comm_real_tenant"

        # Verify the doctor can retrieve the file
        file_id = rows[0]["id"]
        file_resp = client.get(f"/api/files/{file_id}", headers={"Authorization": f"Bearer {token}"})
        assert file_resp.status_code == 200


def test_billing_usage_endpoints_role_gate(test_setup):
    """Safeguard 3: Settings Billing Telemetry:
    Request /api/usage and /api/usage/ledger only for authenticated users with authorized admin role.
    Prevent unnecessary 403 requests for doctors.
    """
    monkeypatch = test_setup["monkeypatch"]
    monkeypatch.setenv("APP_ENV", "development")

    app = create_app()
    app.config["TESTING"] = True
    app.config["SUPABASE_ENABLED"] = False
    client = app.test_client()

    doc_token = create_access_token(
        user_id="doc_user_1",
        role="doctor",
        email="doctor@hospital.org"
    )

    admin_token = create_access_token(
        user_id="admin_user_1",
        role="admin",
        email="admin@hospital.org"
    )

    comm_admin_token = create_access_token(
        user_id="comm_admin_user_1",
        role="community_admin",
        email="comm_admin@hospital.org"
    )

    # 1. Doctor requests /api/usage -> 403 Forbidden
    res_doc_usage = client.get("/api/usage", headers={"Authorization": f"Bearer {doc_token}"})
    assert res_doc_usage.status_code == 403

    # 2. Doctor requests /api/usage/ledger -> 403 Forbidden
    res_doc_ledger = client.get("/api/usage/ledger", headers={"Authorization": f"Bearer {doc_token}"})
    assert res_doc_ledger.status_code == 403

    # 3. Admin requests /api/usage -> 200 OK
    res_admin_usage = client.get("/api/usage", headers={"Authorization": f"Bearer {admin_token}"})
    assert res_admin_usage.status_code == 200

    # 4. Admin requests /api/usage/ledger -> 200 OK
    res_admin_ledger = client.get("/api/usage/ledger", headers={"Authorization": f"Bearer {admin_token}"})
    assert res_admin_ledger.status_code == 200

    # 5. Community Admin requests /api/usage -> 200 OK
    res_comm_admin_usage = client.get("/api/usage", headers={"Authorization": f"Bearer {comm_admin_token}"})
    assert res_comm_admin_usage.status_code == 200


def test_supabase_authenticated_role_resolution_for_review_and_approval(test_setup):
    """Verify that when a user logs in via Supabase Auth (which issues role='authenticated'),
    their clinical role is properly resolved from the registered database profile,
    allowing review and approval rather than returning 403 Forbidden.
    """
    monkeypatch = test_setup["monkeypatch"]
    db_file = test_setup["db_file"]
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEMO_MODE", "1")

    # Register doctor in SQLite
    doc_auth_id = "doc_supabase_auth_uuid_456"
    doc_email = "doctor_supabase@hospital.org"
    database.get_or_create_doctor(
        email=doc_email,
        full_name="Dr. Supabase Doctor",
        auth_id=doc_auth_id,
        community_id="comm_clinic_01",
        db_path=db_file
    )

    app = create_app()
    app.config["TESTING"] = True
    app.config["SUPABASE_ENABLED"] = False
    client = app.test_client()

    # Create camp and record
    camp_id = "camp_review_test"
    database.create_camp("Review Test Camp", 100, community_id="comm_clinic_01", camp_id=camp_id, db_path=db_file)
    rec_id = "rec_review_role_test_01"
    database.insert_record({
        "id": rec_id,
        "camp_id": camp_id,
        "community_id": "comm_clinic_01",
        "person_name": "Test Patient",
        "status": "needs_review",
        "consent_given": 1
    }, db_path=db_file)

    # Token with role='authenticated' (exact Supabase Auth token claim)
    token = create_access_token(
        user_id=doc_auth_id,
        role="authenticated",
        email=doc_email
    )

    # 1. Review record should succeed (HTTP 200, not 403)
    review_resp = client.put(
        f"/api/records/{rec_id}/review",
        json={"person_name": "Test Patient Updated", "reviewed_by": "Dr. Supabase Doctor"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert review_resp.status_code == 200, f"Review failed: {review_resp.data}"

    # 2. Approve record should succeed (HTTP 200, not 403)
    approve_resp = client.post(
        f"/api/records/{rec_id}/approve",
        json={"approved_by": "Dr. Supabase Doctor"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert approve_resp.status_code == 200, f"Approve failed: {approve_resp.data}"


def test_export_download_authorization_with_query_token_and_bearer(test_setup):
    """Verify that export downloads succeed with query ?token= or Bearer header, and 401 when missing."""
    monkeypatch = test_setup["monkeypatch"]
    db_file = test_setup["db_file"]
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEMO_MODE", "1")

    doc_auth_id = "doc_export_uuid_789"
    doc_email = "doctor_export@hospital.org"
    database.get_or_create_doctor(
        email=doc_email,
        full_name="Dr. Export Doctor",
        auth_id=doc_auth_id,
        community_id="comm_demo",
        db_path=db_file
    )

    app = create_app()
    app.config["TESTING"] = True
    app.config["SUPABASE_ENABLED"] = False
    client = app.test_client()

    camp_id = "camp_export_test"
    database.create_camp("Export Test Camp", 50, community_id="comm_demo", camp_id=camp_id, db_path=db_file)
    rec_id = "rec_export_test_01"
    database.insert_record({
        "id": rec_id,
        "camp_id": camp_id,
        "community_id": "comm_demo",
        "person_name": "Export Patient",
        "status": "approved",
        "consent_given": 1
    }, db_path=db_file)

    token = create_access_token(
        user_id=doc_auth_id,
        role="authenticated",
        email=doc_email
    )

    # 1. Missing token -> 401
    no_auth_resp = client.get(f"/api/camps/{camp_id}/export?format=xlsx")
    assert no_auth_resp.status_code == 401

    # 2. Query parameter ?token= (used by browser downloads) -> 200 OK
    query_auth_resp = client.get(f"/api/camps/{camp_id}/export?format=xlsx&token={token}")
    assert query_auth_resp.status_code == 200
    assert "spreadsheetml" in query_auth_resp.content_type

    # 3. Header Authorization: Bearer -> 200 OK
    header_auth_resp = client.get(
        f"/api/camps/{camp_id}/export?format=xlsx",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert header_auth_resp.status_code == 200
    assert "spreadsheetml" in header_auth_resp.content_type


def test_paediatric_screening_evaluation_and_atr_binding():
    """Verify paediatric screening engine evaluates spinal_caliper_atr, DOB, and returns alerts."""
    from backend.services.paediatric_screening import evaluate_paediatric_record, classify_paediatric

    sample_fields = {
        "person_name": ["B. SADHANA", 0.99],
        "dob": ["08/07/2013", 0.99],
        "gender": ["F", 0.99],
        "height": ["154.4", 0.98],
        "weight": ["35.7", 0.98],
        "spinal_caliper_atr": ["5", 0.95],
        "bp": ["100/60", 0.95],
    }

    eval_res = evaluate_paediatric_record(sample_fields)
    assert eval_res["is_paediatric"] is True
    assert eval_res["spinal_caliper_atr"] == 5.0
    assert eval_res["atr_classification"]["alert_triggered"] is True
    assert eval_res["atr_classification"]["alert_level"] == "advisory"
    assert any("Scoliosis" in a for a in eval_res["alerts"])


def test_validate_and_sanitize_extraction_paediatric_mapping():
    """Verify that paediatric-specific keys are correctly mapped into structured groups."""
    from backend.services.ai_service import validate_and_sanitize_extraction

    raw_data = {
        "person_name": "B. SADHANA",
        "parent_name": "B. Radhika",
        "mobile_number": "9884783689",
        "height": "154.4",
        "weight": "35.7",
        "waist_circumference": "86",
        "spinal_caliper_atr": "5",
        "sys_nose": "Normal",
        "sys_throat": "Normal",
        "sys_skin": "Normal",
        "vision_distant_right": "6/6",
        "vision_distant_left": "6/6",
        "vision_aid_status": "Unaided",
        "dental_status": "Normal"
    }

    sanitized = validate_and_sanitize_extraction(raw_data)
    sr = sanitized["structured_record"]

    assert sr["personal_information"]["parent_spouse_name"]["value"] == "B. Radhika"
    assert sr["personal_information"]["mobile_number"]["value"] == "9884783689"
    assert sr["health_assessment"]["waist_circumference_cm"]["value"] == "86"
    assert sr["health_assessment"]["atr_degrees"]["value"] == "5"
    assert sr["system_examination"]["nose"]["value"] == "Normal"
    assert sr["vision"]["distant_vision_right"]["value"] == "6/6"
    assert sr["dental"]["normal"]["value"] == "Normal"


