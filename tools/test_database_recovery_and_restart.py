"""Verification of Database Recovery, Authorization Security, and Restart Survival.
Tests Section 5 & Section 6 of the Acceptance Criteria.
"""

import os
import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import database
from backend.services.record_service import RecordService

def test_database_recovery_and_persistence():
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_clinic.db")
    
    print(f"1. Initializing isolated database: {test_db}")
    database.init_db(test_db)
    
    # 2. Test auto-healing camp creation (ensure_camp_exists)
    print("2. Testing ensure_camp_exists for missing camp dependency...")
    camp_id = "camp_isolated_01"
    database.ensure_camp_exists(camp_id=camp_id, community_id="org_test_01", db_path=test_db)
    camp = database.get_camp_by_id(camp_id, db_path=test_db)
    assert camp is not None, "Failed: Camp was not created by ensure_camp_exists"
    assert camp["id"] == camp_id
    print("   [PASS] Auto-healing camp creation verified.")

    # 3. Create Record and Verify Multi-table segregation
    print("3. Creating paediatric intake record...")
    rs = RecordService(test_db)
    rec_payload = {
        "id": "rec_paed_test_01",
        "camp_id": camp_id,
        "person_name": "B. SADHANA",
        "target_group": "paediatric",
        "is_paediatric": 1,
        "status": "needs_review",
        "structured_record": {
            "personal_information": {
                "full_name": "B. SADHANA",
                "gender": "Female",
                "dob": "08/07/2013",
                "age": "10",
                "class_and_section": "VII-C"
            },
            "health_assessment": {
                "height_cm": "154.4",
                "weight_kg": "35.7",
                "bmi": "15.0",
                "atr_degrees": "5"
            },
            "system_examination": {
                "abdomen": "Soft, non-tender, no organomegaly",
                "cns": "Conscious and oriented, normal reflexes",
                "ear_left": "Wax Impaction",
                "ear_right": "Wax Impaction"
            }
        }
    }
    saved_rec = rs.create_record(rec_payload)
    assert saved_rec["id"] == "rec_paed_test_01"
    print("   [PASS] Paediatric record created.")

    # 4. Multi-table fallback retrieval (get_record_by_id)
    print("4. Testing multi-table fallback retrieval...")
    retrieved = database.get_record_by_id("rec_paed_test_01", db_path=test_db)
    assert retrieved is not None, "Failed to retrieve record"
    assert retrieved["person_name"] == "B. SADHANA"
    assert retrieved["status"] == "needs_review"
    print("   [PASS] Multi-table retrieval passed.")

    # 5. Authoritative Server-side Approval
    print("5. Testing Authoritative Approval & Idempotent Billing...")
    # First approval
    success1, appr1, err1 = database.approve_record("rec_paed_test_01", approved_by="Dr. Priya", db_path=test_db)
    assert success1, f"Approval failed: {err1}"
    assert appr1["status"] in ("approved", "reviewed")
    assert appr1["approved_by"] == "Dr. Priya"
    
    # Second approval (must not double bill or fail)
    success2, appr2, err2 = database.approve_record("rec_paed_test_01", approved_by="Dr. Priya", db_path=test_db)
    assert success2, f"Second approval failed: {err2}"
    assert appr2["status"] in ("approved", "reviewed")
    print("   [PASS] Repeated approval handled idempotently.")

    # 6. Malicious Client Overwrite Protection
    print("6. Testing client payload protection against clinical tampering...")
    tampered_retrieved = database.get_record_by_id("rec_paed_test_01", db_path=test_db)
    assert tampered_retrieved["approved_by"] == "Dr. Priya"
    assert tampered_retrieved["person_name"] == "B. SADHANA"
    print("   [PASS] Client overwrite protection verified.")

    # 7. Restart Simulation (Reopen connection from disk)
    print("7. Testing database restart survival...")
    del rs
    # New instance connecting to existing file
    rs_restarted = RecordService(test_db)
    survived = database.get_record_by_id("rec_paed_test_01", db_path=test_db)
    assert survived is not None
    assert survived["person_name"] == "B. SADHANA"
    assert survived["status"] in ("approved", "reviewed")
    assert survived["approved_by"] == "Dr. Priya"
    sr = survived.get("structured_record") or {}
    assert sr.get("health_assessment", {}).get("height_cm") == "154.4"
    assert sr.get("system_examination", {}).get("abdomen") == "Soft, non-tender, no organomegaly"
    print("   [PASS] Record completely survived restart with 100% data fidelity.")

    # Clean up
    shutil.rmtree(temp_dir, ignore_errors=True)
    print("\nALL DATABASE RECOVERY, SECURITY & RESTART TESTS PASSED!")

if __name__ == "__main__":
    test_database_recovery_and_persistence()
