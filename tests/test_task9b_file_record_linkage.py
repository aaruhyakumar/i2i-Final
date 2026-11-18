"""
Task 9B Regression Test Suite: Link Uploaded Scan Files to Saved Records.

Covers:
1. One-page record links its uploaded file.
2. Two-page record links both uploaded files.
3. Adult record linkage.
4. Paediatric record linkage.
5. Files retain their original camp and community.
6. A file belonging to another camp cannot be linked.
7. A file already linked to another record cannot be overwritten.
8. Failed record creation does not leave an incorrect relationship (atomic rollback).
9. Existing Task 9A authorization tests still pass.
"""

import os
import pytest
from backend import database
from backend.services.record_service import RecordService

TEST_JWT_SECRET = "test-jwt-secret-task9b-9876543210"


@pytest.fixture
def linkage_env(tmp_path, monkeypatch):
    """Setup isolated SQLite DB and private uploads directory."""
    db_file = str(tmp_path / "linkage_test.db")
    private_dir = str(tmp_path / "private_uploads")
    os.makedirs(private_dir, exist_ok=True)

    monkeypatch.setenv("DATABASE_PATH", db_file)
    monkeypatch.setenv("PRIVATE_UPLOAD_DIR", private_dir)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEMO_MODE", "1")

    database.init_db(db_file)

    comm_alpha = "comm_alpha"
    comm_beta = "comm_beta"

    # Create Camp A and Camp B
    camp_a = database.create_camp(
        "Camp Alpha", 100, "Today",
        camp_id="camp_alpha_01",
        community_id=comm_alpha,
        db_path=db_file
    )
    camp_b = database.create_camp(
        "Camp Beta", 50, "Tomorrow",
        camp_id="camp_beta_01",
        community_id=comm_beta,
        db_path=db_file
    )

    rec_service = RecordService(db_path=db_file)

    return {
        "db_file": db_file,
        "private_dir": private_dir,
        "comm_alpha": comm_alpha,
        "comm_beta": comm_beta,
        "camp_a": camp_a["id"],
        "camp_b": camp_b["id"],
        "record_service": rec_service
    }


def test_one_page_record_links_uploaded_file(linkage_env):
    """Requirement 1: One-page record links its uploaded file."""
    env = linkage_env
    db_file = env["db_file"]

    # 1. Upload one file with record_id=None
    f1_id = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="scan_page1",
        filename=f"{env['camp_a']}_p1.jpg",
        data=b"PAGE_ONE_BYTES",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=env["private_dir"]
    )

    # Verify initially NULL
    f1_before = database.get_file_by_id(f1_id, db_path=db_file)
    assert f1_before["record_id"] is None

    # 2. Create one-page record
    rec = env["record_service"].create_record({
        "camp_id": env["camp_a"],
        "person_name": "Single Page Patient",
        "page1_preview": f"/api/files/{f1_id}",
        "page2_preview": None,
        "target_group": "adult"
    })
    rec_id = rec["id"]

    # 3. Verify file is now linked to rec_id
    f1_after = database.get_file_by_id(f1_id, db_path=db_file)
    assert f1_after["record_id"] == rec_id


def test_two_page_record_links_both_uploaded_files(linkage_env):
    """Requirement 2: Two-page record links both uploaded files."""
    env = linkage_env
    db_file = env["db_file"]

    # 1. Upload two files with record_id=None
    f1_id = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="scan_page1",
        filename=f"{env['camp_a']}_p1.jpg",
        data=b"PAGE_1_BINARY",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=env["private_dir"]
    )
    f2_id = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="scan_page2",
        filename=f"{env['camp_a']}_p2.jpg",
        data=b"PAGE_2_BINARY",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=env["private_dir"]
    )

    # 2. Create two-page record
    rec = env["record_service"].create_record({
        "camp_id": env["camp_a"],
        "person_name": "Two Page Patient",
        "page1_preview": f"/api/files/{f1_id}",
        "page2_preview": f"/api/files/{f2_id}",
        "target_group": "adult"
    })
    rec_id = rec["id"]

    # 3. Verify both files are linked to rec_id
    f1_after = database.get_file_by_id(f1_id, db_path=db_file)
    f2_after = database.get_file_by_id(f2_id, db_path=db_file)
    assert f1_after["record_id"] == rec_id
    assert f2_after["record_id"] == rec_id


def test_adult_record_linkage(linkage_env):
    """Requirement 3: Adult record linkage properly updates both adult_records and files.record_id."""
    env = linkage_env
    db_file = env["db_file"]

    f_id = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="scan_page1",
        filename=f"{env['camp_a']}_adult.jpg",
        data=b"ADULT_FORM_BYTES",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=env["private_dir"]
    )

    rec = database.insert_record({
        "camp_id": env["camp_a"],
        "community_id": env["comm_alpha"],
        "person_name": "John Adult",
        "age": "45",
        "gender": "Male",
        "blood_pressure": "120/80",
        "target_group": "adult",
        "page1_preview": f"/api/files/{f_id}"
    }, db_path=db_file)

    rec_id = rec["id"]
    # Check adult_records table
    adult_rec = database.get_adult_record_by_id(rec_id, db_path=db_file)
    assert adult_rec is not None
    assert adult_rec["id"] == rec_id

    # Check files.record_id
    f_after = database.get_file_by_id(f_id, db_path=db_file)
    assert f_after["record_id"] == rec_id


def test_paediatric_record_linkage(linkage_env):
    """Requirement 4: Paediatric record linkage properly updates paediatric_records and files.record_id."""
    env = linkage_env
    db_file = env["db_file"]

    f_id = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="scan_page1",
        filename=f"{env['camp_a']}_paed.jpg",
        data=b"PAEDIATRIC_FORM_BYTES",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=env["private_dir"]
    )

    rec = database.insert_record({
        "camp_id": env["camp_a"],
        "community_id": env["comm_alpha"],
        "person_name": "Baby Paediatric",
        "age": "6",
        "gender": "Female",
        "target_group": "paediatric",
        "is_paediatric": 1,
        "page1_preview": f"/api/files/{f_id}"
    }, db_path=db_file)

    rec_id = rec["id"]
    # Check paediatric_records table
    paed_rec = database.get_paediatric_record_by_id(rec_id, db_path=db_file)
    assert paed_rec is not None
    assert paed_rec["id"] == rec_id

    # Check files.record_id
    f_after = database.get_file_by_id(f_id, db_path=db_file)
    assert f_after["record_id"] == rec_id


def test_files_retain_original_camp_and_community(linkage_env):
    """Requirement 5: Files retain their original camp, filename, and community_id (org_id)."""
    env = linkage_env
    db_file = env["db_file"]

    orig_filename = f"{env['camp_a']}_p1_custom.jpg"
    orig_org = env["comm_alpha"]

    f_id = database.save_private_file(
        org_id=orig_org,
        record_id=None,
        file_type="scan_page1",
        filename=orig_filename,
        data=b"PRESERVED_METADATA",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=env["private_dir"]
    )

    rec = env["record_service"].create_record({
        "camp_id": env["camp_a"],
        "person_name": "Metadata Check Patient",
        "page1_preview": f"/api/files/{f_id}"
    })

    f_after = database.get_file_by_id(f_id, db_path=db_file)
    assert f_after["record_id"] == rec["id"]
    assert f_after["org_id"] == orig_org
    assert f_after["original_filename"] == orig_filename
    assert f_after["file_type"] == "scan_page1"


def test_file_belonging_to_another_camp_cannot_be_linked(linkage_env):
    """Requirement 6: A file belonging to another camp cannot be linked."""
    env = linkage_env
    db_file = env["db_file"]

    # File uploaded specifically for Camp B
    f_camp_b = database.save_private_file(
        org_id=env["comm_beta"],
        record_id=None,
        file_type="scan_page1",
        filename=f"{env['camp_b']}_p1.jpg",
        data=b"CAMP_B_SCAN",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=env["private_dir"]
    )

    # Attempt to create a record in Camp A linking Camp B's file
    rec_a = env["record_service"].create_record({
        "camp_id": env["camp_a"],
        "person_name": "Camp A Attacker",
        "page1_preview": f"/api/files/{f_camp_b}"
    })

    # The file must NOT be linked to Camp A's record
    f_after = database.get_file_by_id(f_camp_b, db_path=db_file)
    assert f_after["record_id"] is None


def test_file_already_linked_cannot_be_overwritten(linkage_env):
    """Requirement 7: A file already linked to another record cannot be overwritten."""
    env = linkage_env
    db_file = env["db_file"]

    # 1. Create first record with file
    f_id = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="scan_page1",
        filename=f"{env['camp_a']}_p1.jpg",
        data=b"FIRST_RECORD_FILE",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=env["private_dir"]
    )

    rec1 = env["record_service"].create_record({
        "camp_id": env["camp_a"],
        "person_name": "First Patient",
        "page1_preview": f"/api/files/{f_id}"
    })
    rec1_id = rec1["id"]

    f_linked = database.get_file_by_id(f_id, db_path=db_file)
    assert f_linked["record_id"] == rec1_id

    # 2. Attempt to create second record referencing the same already-linked file
    rec2 = env["record_service"].create_record({
        "camp_id": env["camp_a"],
        "person_name": "Second Patient Hijacker",
        "page1_preview": f"/api/files/{f_id}"
    })
    rec2_id = rec2["id"]
    assert rec2_id != rec1_id

    # 3. Verify file's record_id was NOT overwritten
    f_still = database.get_file_by_id(f_id, db_path=db_file)
    assert f_still["record_id"] == rec1_id


def test_failed_record_creation_leaves_no_incorrect_relationship(linkage_env):
    """Requirement 8: Failed record creation rolls back atomically, leaving no file relationship."""
    env = linkage_env
    db_file = env["db_file"]

    f_id = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="scan_page1",
        filename=f"{env['camp_a']}_fail_test.jpg",
        data=b"ATOMICITY_TEST",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=env["private_dir"]
    )

    # Simulate database insertion failure by attempting insert with illegal NULL/empty camp_id in RecordService
    with pytest.raises(ValueError):
        env["record_service"].create_record({
            "camp_id": "",  # Missing camp_id raises ValueError
            "person_name": "Doomed Patient",
            "page1_preview": f"/api/files/{f_id}"
        })

    # Verify file remains unlinked
    f_after = database.get_file_by_id(f_id, db_path=db_file)
    assert f_after["record_id"] is None


def test_task9a_authorization_still_enforces_rules_with_linked_files(linkage_env):
    """Requirement 9: Task 9A authorization works seamlessly with newly linked files."""
    from backend.app import create_app
    from backend.services.auth_service import create_access_token

    env = linkage_env
    db_file = env["db_file"]

    # Create Worker Alice and Worker Bob
    database.get_or_create_doctor(
        email="alice@clinic.org",
        full_name="Alice Worker",
        auth_id="worker_alice",
        community_id=env["comm_alpha"],
        community_role="field_worker",
        db_path=db_file
    )
    database.get_or_create_doctor(
        email="bob@clinic.org",
        full_name="Bob Worker",
        auth_id="worker_bob",
        community_id=env["comm_alpha"],
        community_role="field_worker",
        db_path=db_file
    )

    tok_alice = create_access_token(user_id="worker_alice", role="field_worker", org_id=env["comm_alpha"], email="alice@clinic.org")
    tok_bob = create_access_token(user_id="worker_bob", role="field_worker", org_id=env["comm_alpha"], email="bob@clinic.org")

    app = create_app(db_path=db_file)
    app.config["TESTING"] = True
    app.config["PRIVATE_UPLOAD_DIR"] = env["private_dir"]
    client = app.test_client()

    # Upload file
    f_id = database.save_private_file(
        org_id=env["comm_alpha"],
        record_id=None,
        file_type="scan_page1",
        filename=f"{env['camp_a']}_alice.jpg",
        data=b"ALICE_AUTHORIZED_BYTES",
        mime_type="image/jpeg",
        db_path=db_file,
        private_dir=env["private_dir"]
    )

    # Alice creates record
    rec = env["record_service"].create_record({
        "camp_id": env["camp_a"],
        "community_id": env["comm_alpha"],
        "scanned_by": "worker_alice",
        "person_name": "Alice Patient",
        "page1_preview": f"/api/files/{f_id}"
    })
    rec_id = rec["id"]

    # Verify file is linked
    f_linked = database.get_file_by_id(f_id, db_path=db_file)
    assert f_linked["record_id"] == rec_id

    # 1. Alice (owner) accesses file -> 200 OK
    res_alice = client.get(f"/api/files/{f_id}", headers={"Authorization": f"Bearer {tok_alice}"})
    assert res_alice.status_code == 200
    assert res_alice.data == b"ALICE_AUTHORIZED_BYTES"

    # 2. Bob (other worker) accesses file -> 403 Forbidden
    res_bob = client.get(f"/api/files/{f_id}", headers={"Authorization": f"Bearer {tok_bob}"})
    assert res_bob.status_code == 403
    assert res_bob.get_json()["error"] == "forbidden"
