"""
Task 9A Regression Test Suite: Private Scan File Authorization.

Covers:
1. Authorized field worker accessing an authorized file (via record_id and via page preview link).
2. Field worker accessing another user's file (403 Forbidden).
3. Field worker accessing an unlinked file with NULL record_id (403 Forbidden).
4. Unauthenticated access (401 Unauthorized).
5. Doctor/admin access under existing permissions (200 OK within tenant, 404 cross-tenant).
6. Signed URL generation authorization for field workers and doctors/admins.
"""

import os
import pytest
from backend.app import create_app
from backend import database
from backend.services.auth_service import create_access_token

TEST_JWT_SECRET = "test-jwt-secret-key-for-file-auth-testing-9a-0123456789"


@pytest.fixture
def auth_fixture(tmp_path, monkeypatch):
    """Setup isolated SQLite DB, upload directory, and test client."""
    db_file = str(tmp_path / "file_auth.db")
    private_dir = str(tmp_path / "private_uploads")
    os.makedirs(private_dir, exist_ok=True)

    monkeypatch.setenv("DATABASE_PATH", db_file)
    monkeypatch.setenv("PRIVATE_UPLOAD_DIR", private_dir)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEMO_MODE", "1")

    database.init_db(db_file)

    # Seed communities
    comm_alpha = "comm_alpha"
    comm_beta = "comm_beta"

    # Seed users in SQLite
    # 1. Field Worker Alice in comm_alpha
    database.get_or_create_doctor(
        email="alice@clinic.org",
        full_name="Worker Alice",
        auth_id="worker_alice",
        community_id=comm_alpha,
        community_role="field_worker",
        db_path=db_file
    )
    # 2. Field Worker Bob in comm_alpha
    database.get_or_create_doctor(
        email="bob@clinic.org",
        full_name="Worker Bob",
        auth_id="worker_bob",
        community_id=comm_alpha,
        community_role="field_worker",
        db_path=db_file
    )
    # 3. Doctor Carol in comm_alpha
    database.get_or_create_doctor(
        email="carol@clinic.org",
        full_name="Dr Carol",
        auth_id="doc_carol",
        community_id=comm_alpha,
        community_role="doctor",
        db_path=db_file
    )
    # 4. Admin Dave in comm_alpha
    database.get_or_create_doctor(
        email="dave@clinic.org",
        full_name="Admin Dave",
        auth_id="admin_dave",
        community_id=comm_alpha,
        community_role="admin",
        db_path=db_file
    )
    # 5. Doctor Foreign in comm_beta (cross-tenant)
    database.get_or_create_doctor(
        email="foreign@clinic.org",
        full_name="Dr Foreign",
        auth_id="doc_foreign",
        community_id=comm_beta,
        community_role="doctor",
        db_path=db_file
    )

    # Create camp in comm_alpha
    camp = database.create_camp(
        "Alpha Health Camp",
        50,
        "Today",
        camp_id="camp_alpha_01",
        community_id=comm_alpha,
        db_path=db_file
    )

    # Tokens
    tok_alice = create_access_token(user_id="worker_alice", role="field_worker", org_id=comm_alpha, email="alice@clinic.org")
    tok_bob = create_access_token(user_id="worker_bob", role="field_worker", org_id=comm_alpha, email="bob@clinic.org")
    tok_doc = create_access_token(user_id="doc_carol", role="doctor", org_id=comm_alpha, email="carol@clinic.org")
    tok_admin = create_access_token(user_id="admin_dave", role="admin", org_id=comm_alpha, email="dave@clinic.org")
    tok_foreign = create_access_token(user_id="doc_foreign", role="doctor", org_id=comm_beta, email="foreign@clinic.org")

    app = create_app(db_path=db_file)
    app.config["TESTING"] = True
    app.config["PRIVATE_UPLOAD_DIR"] = private_dir
    client = app.test_client()

    return {
        "db_file": db_file,
        "private_dir": private_dir,
        "camp_id": camp["id"],
        "comm_alpha": comm_alpha,
        "comm_beta": comm_beta,
        "client": client,
        "tokens": {
            "alice": tok_alice,
            "bob": tok_bob,
            "doc": tok_doc,
            "admin": tok_admin,
            "foreign": tok_foreign,
        }
    }


def test_authorized_field_worker_access_via_record_id(auth_fixture):
    """Case 1A: Field worker accesses their own file linked via files.record_id -> 200 OK."""
    env = auth_fixture
    client = env["client"]
    db_file = env["db_file"]
    p_dir = env["private_dir"]

    # 1. Create a record scanned by Alice
    rec = database.insert_record({
        "camp_id": env["camp_id"],
        "community_id": env["comm_alpha"],
        "scanned_by": "worker_alice",
        "person_name": "Alice Patient",
        "status": "needs_review"
    }, db_path=db_file)

    # 2. Save a private file with record_id pointing to Alice's record
    raw_bytes = b"BINARY_IMAGE_DATA_ALICE_FILE"
    file_id = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=rec["id"],
        file_type="scan_page1",
        filename="alice_scan.jpg",
        data=raw_bytes,
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=p_dir
    )

    # Alice requests her file -> 200 OK
    res = client.get(f"/api/files/{file_id}", headers={"Authorization": f"Bearer {env['tokens']['alice']}"})
    assert res.status_code == 200
    assert res.data == raw_bytes
    assert res.mimetype == "image/jpeg"


def test_authorized_field_worker_access_via_preview_link(auth_fixture):
    """Case 1B: Field worker accesses file where files.record_id is NULL, but records.page1_preview links it -> 200 OK."""
    env = auth_fixture
    client = env["client"]
    db_file = env["db_file"]
    p_dir = env["private_dir"]

    # 1. Save private file with record_id=None
    raw_bytes = b"PREVIEW_LINKED_IMAGE_DATA"
    file_id = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="upload",
        filename="alice_preview.jpg",
        data=raw_bytes,
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=p_dir
    )

    # 2. Record references file via page1_preview, scanned_by=worker_alice
    database.insert_record({
        "camp_id": env["camp_id"],
        "community_id": env["comm_alpha"],
        "scanned_by": "worker_alice",
        "person_name": "Alice Preview Patient",
        "page1_preview": f"/api/files/{file_id}",
        "status": "needs_review"
    }, db_path=db_file)

    # Alice requests her file -> 200 OK
    res = client.get(f"/api/files/{file_id}", headers={"Authorization": f"Bearer {env['tokens']['alice']}"})
    assert res.status_code == 200
    assert res.data == raw_bytes


def test_field_worker_accessing_other_users_file_denied_403(auth_fixture):
    """Case 2: Field worker Bob tries to access Alice's file -> 403 Forbidden."""
    env = auth_fixture
    client = env["client"]
    db_file = env["db_file"]
    p_dir = env["private_dir"]

    # Alice's record and file
    rec_alice = database.insert_record({
        "camp_id": env["camp_id"],
        "community_id": env["comm_alpha"],
        "scanned_by": "worker_alice",
        "person_name": "Alice Confidential Patient",
        "status": "needs_review"
    }, db_path=db_file)

    file_alice = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=rec_alice["id"],
        file_type="scan_page1",
        filename="alice_secret.jpg",
        data=b"ALICE_CONFIDENTIAL_SCAN",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=p_dir
    )

    # Bob (same community, different field worker) attempts access -> 403 Forbidden
    res = client.get(f"/api/files/{file_alice}", headers={"Authorization": f"Bearer {env['tokens']['bob']}"})
    assert res.status_code == 403
    json_data = res.get_json()
    assert json_data["error"] == "forbidden"
    assert "field_worker can only access files for their own scanned records" in json_data["message"]


def test_field_worker_accessing_null_record_file_denied_403(auth_fixture):
    """Case 3: Field worker accesses an unassociated file with record_id=NULL -> 403 Forbidden."""
    env = auth_fixture
    client = env["client"]
    db_file = env["db_file"]
    p_dir = env["private_dir"]

    # File uploaded but never linked to any record
    orphan_file = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="upload",
        filename="orphan_scan.jpg",
        data=b"ORPHAN_IMAGE_BYTES",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=p_dir
    )

    # Alice attempts access without established record ownership -> 403 Forbidden
    res = client.get(f"/api/files/{orphan_file}", headers={"Authorization": f"Bearer {env['tokens']['alice']}"})
    assert res.status_code == 403
    json_data = res.get_json()
    assert json_data["error"] == "forbidden"
    assert "field_worker cannot access unlinked or unassociated files" in json_data["message"]


def test_unauthenticated_access_denied_401(auth_fixture):
    """Case 4: Unauthenticated request without token -> 401 Unauthorized."""
    env = auth_fixture
    client = env["client"]
    db_file = env["db_file"]
    p_dir = env["private_dir"]

    file_id = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="upload",
        filename="any_file.jpg",
        data=b"ANY_DATA",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=p_dir
    )

    res = client.get(f"/api/files/{file_id}")
    assert res.status_code == 401
    assert res.get_json()["error"] == "unauthorized"


def test_doctor_and_admin_retain_full_community_access_200(auth_fixture):
    """Case 5A: Doctors and Admins in the same community access any file (owned, unlinked, or other workers') -> 200 OK."""
    env = auth_fixture
    client = env["client"]
    db_file = env["db_file"]
    p_dir = env["private_dir"]

    # 1. File belonging to Alice's record
    rec_alice = database.insert_record({
        "camp_id": env["camp_id"],
        "community_id": env["comm_alpha"],
        "scanned_by": "worker_alice",
        "person_name": "Alice Patient",
        "status": "needs_review"
    }, db_path=db_file)

    alice_file = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=rec_alice["id"],
        file_type="scan_page1",
        filename="alice.jpg",
        data=b"ALICE_DATA",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=p_dir
    )

    # 2. File with record_id=NULL
    orphan_file = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="upload",
        filename="orphan.jpg",
        data=b"ORPHAN_DATA",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=p_dir
    )

    # Doctor Carol can access Alice's file
    res_doc1 = client.get(f"/api/files/{alice_file}", headers={"Authorization": f"Bearer {env['tokens']['doc']}"})
    assert res_doc1.status_code == 200
    assert res_doc1.data == b"ALICE_DATA"

    # Doctor Carol can access orphan file
    res_doc2 = client.get(f"/api/files/{orphan_file}", headers={"Authorization": f"Bearer {env['tokens']['doc']}"})
    assert res_doc2.status_code == 200
    assert res_doc2.data == b"ORPHAN_DATA"

    # Admin Dave can access Alice's file
    res_adm1 = client.get(f"/api/files/{alice_file}", headers={"Authorization": f"Bearer {env['tokens']['admin']}"})
    assert res_adm1.status_code == 200
    assert res_adm1.data == b"ALICE_DATA"

    # Admin Dave can access orphan file
    res_adm2 = client.get(f"/api/files/{orphan_file}", headers={"Authorization": f"Bearer {env['tokens']['admin']}"})
    assert res_adm2.status_code == 200
    assert res_adm2.data == b"ORPHAN_DATA"


def test_cross_tenant_access_denied_404_anti_enumeration(auth_fixture):
    """Case 5B: User from foreign tenant (comm_beta) accessing comm_alpha file gets 404 (anti-enumeration)."""
    env = auth_fixture
    client = env["client"]
    db_file = env["db_file"]
    p_dir = env["private_dir"]

    file_alpha = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="scan_page1",
        filename="alpha.jpg",
        data=b"COMM_ALPHA_DATA",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=p_dir
    )

    res_foreign = client.get(f"/api/files/{file_alpha}", headers={"Authorization": f"Bearer {env['tokens']['foreign']}"})
    assert res_foreign.status_code == 404
    assert res_foreign.get_json()["error"] == "not_found"


def test_signed_url_generation_authorization(auth_fixture):
    """Case 6: Signed URL generation enforces the exact same authorization rules."""
    env = auth_fixture
    client = env["client"]
    db_file = env["db_file"]
    p_dir = env["private_dir"]

    # Alice's file
    rec_alice = database.insert_record({
        "camp_id": env["camp_id"],
        "community_id": env["comm_alpha"],
        "scanned_by": "worker_alice",
        "person_name": "Alice Patient",
        "status": "needs_review"
    }, db_path=db_file)

    alice_file = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=rec_alice["id"],
        file_type="scan_page1",
        filename="alice.jpg",
        data=b"ALICE_DATA",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=p_dir
    )

    # Orphan file
    orphan_file = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="upload",
        filename="orphan.jpg",
        data=b"ORPHAN_DATA",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=p_dir
    )

    # 1. Alice can generate signed URL for her own file -> 200 OK
    res_sig_alice = client.get(f"/api/files/{alice_file}/signed-url", headers={"Authorization": f"Bearer {env['tokens']['alice']}"})
    assert res_sig_alice.status_code == 200
    signed_url = res_sig_alice.get_json()["signed_url"]
    assert f"/api/files/{alice_file}?token=" in signed_url

    # Redeeming the signed URL without Authorization header succeeds -> 200 OK
    res_redeem = client.get(signed_url)
    assert res_redeem.status_code == 200
    assert res_redeem.data == b"ALICE_DATA"

    # 2. Bob cannot generate signed URL for Alice's file -> 403 Forbidden
    res_sig_bob = client.get(f"/api/files/{alice_file}/signed-url", headers={"Authorization": f"Bearer {env['tokens']['bob']}"})
    assert res_sig_bob.status_code == 403

    # 3. Alice cannot generate signed URL for orphan file -> 403 Forbidden
    res_sig_orphan = client.get(f"/api/files/{orphan_file}/signed-url", headers={"Authorization": f"Bearer {env['tokens']['alice']}"})
    assert res_sig_orphan.status_code == 403

    # 4. Doctor Carol can generate signed URL for both files -> 200 OK
    res_doc_sig1 = client.get(f"/api/files/{alice_file}/signed-url", headers={"Authorization": f"Bearer {env['tokens']['doc']}"})
    assert res_doc_sig1.status_code == 200
    res_doc_sig2 = client.get(f"/api/files/{orphan_file}/signed-url", headers={"Authorization": f"Bearer {env['tokens']['doc']}"})
    assert res_doc_sig2.status_code == 200
