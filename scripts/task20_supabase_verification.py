import os
import sys
import json
from dotenv import load_dotenv
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("backend/.env")

from backend.services.supabase_service import SupabaseService
from backend.services.record_service import RecordService

RECORD_ID = "rec_20261117134632_f32f41"
CAMP_ID = "d95c06fa-5c10-4991-8afb-6071bef5b3fe"
COMMUNITY_ID = "8bbf2a9d-cef9-4fda-a500-c163c0baed4d"
DOCTOR_ID = "a06fbab0-901e-4741-883f-90bc51e6e3d5"

def main():
    print("=== TASK 20: SUPABASE READ-ONLY VERIFICATION ===")

    sb = SupabaseService()
    assert sb.is_configured() is True, "Supabase must be configured"
    assert sb.is_available() is True, "Supabase circuit breaker must be healthy and closed"
    print(f"[*] Supabase URL: {sb.url}")
    print(f"[*] Supabase Primary Mode Active: True (Circuit Breaker closed)")

    headers = sb._headers()

    # 1. Doctor & Membership
    print("\n[1] Doctor Identity & Community Membership")
    u_res = requests.get(f"{sb.url}/rest/v1/users?id=eq.{DOCTOR_ID}", headers=headers)
    assert u_res.status_code == 200
    user = u_res.json()[0]
    print(f"  User ID: {user['id']}")
    print(f"  Full Name: {user['full_name']}")
    print(f"  Email: {user['email']}")
    print(f"  Verified: {user['email_verified']}")
    assert user['full_name'] == "Dr. Confirmed Audit"
    assert user['email_verified'] is True

    m_res = requests.get(f"{sb.url}/rest/v1/community_memberships?user_id=eq.{DOCTOR_ID}", headers=headers)
    assert m_res.status_code == 200
    mem = m_res.json()[0]
    print(f"  Community ID: {mem['community_id']}")
    print(f"  Role: {mem['role_id']}")
    print(f"  Status: {mem['status']}")
    assert mem['community_id'] == COMMUNITY_ID
    assert mem['role_id'] == "community_admin"
    assert mem['status'] == "active"

    # 2. Camp Ownership
    print("\n[2] Camp Ownership & Community Association")
    c_res = requests.get(f"{sb.url}/rest/v1/camps?id=eq.{CAMP_ID}", headers=headers)
    assert c_res.status_code == 200
    camp = c_res.json()[0]
    print(f"  Camp Name: {camp['name']}")
    print(f"  Created By: {camp['created_by']}")
    print(f"  Community ID: {camp['community_id']}")
    assert camp['name'] == "Supabase Outreach Camp 2026"
    assert camp['created_by'] == DOCTOR_ID
    assert camp['community_id'] == COMMUNITY_ID

    # 3. Exactly One Existing Patient Record
    print("\n[3] Exactly One Patient Record in Camp")
    r_list = requests.get(f"{sb.url}/rest/v1/records?camp_id=eq.{CAMP_ID}&select=id", headers=headers).json()
    p_list = requests.get(f"{sb.url}/rest/v1/paediatric_records?camp_id=eq.{CAMP_ID}&select=id", headers=headers).json()
    print(f"  Total records in public.records: {len(r_list)} (ID: {[r['id'] for r in r_list]})")
    print(f"  Total records in public.paediatric_records: {len(p_list)} (ID: {[r['id'] for r in p_list]})")
    assert len(r_list) == 1, f"Expected exactly 1 parent record, got {len(r_list)}"
    assert len(p_list) == 1, f"Expected exactly 1 child record, got {len(p_list)}"
    assert r_list[0]['id'] == RECORD_ID
    assert p_list[0]['id'] == RECORD_ID

    # 4. Consistency & Restored Values
    print("\n[4] Parent & Child Record Consistency & Restored Clinical Values")
    parent = requests.get(f"{sb.url}/rest/v1/records?id=eq.{RECORD_ID}&limit=1", headers=headers).json()[0]
    child = requests.get(f"{sb.url}/rest/v1/paediatric_records?id=eq.{RECORD_ID}&limit=1", headers=headers).json()[0]

    assert parent['person_name'] == child['person_name'] == "B. SADHANA"
    assert parent['gender'] == child['gender'] == "Female"
    assert parent['height'] == child['height'] == "154.4"
    assert parent['weight'] == child['weight'] == "35.7"
    assert parent['consent_given'] == child['consent_given'] == 1

    print("  Person Name:", child['person_name'])
    print("  Gender:", child['gender'])
    print("  DOB:", child['dob'])
    print("  Blood Group:", child['blood_group'])
    print("  Vision Left (OS):", child['vision_distant_left'])
    print("  Vision Right (OD):", child['vision_distant_right'])
    print("  Otoscopy Findings:", child['otoscopy_findings'])
    print("  Spinal Caliper ATR:", child['spinal_caliper_atr'])
    print("  Parent / Guardian:", child['parent_guardian_name'])
    print("  Consent Signature:", child['consent_signature'])
    print("  Class & Section:", child['class_and_section'])
    print("  Address:", child['address'])
    print("  Emergency Phone:", child['emergency_contact_phone'])
    print("  Known Allergies:", child['known_allergies'])
    print("  Immunization Status:", child['immunization_status'])

    assert child['dob'] == "08/07/2013"
    assert child['blood_group'] == "O+ve"
    assert child['vision_distant_left'] == "6/6"
    assert child['vision_distant_right'] == "6/6"
    assert child['otoscopy_findings'] == "B/L Wax"
    assert float(child['spinal_caliper_atr']) == 5.0
    assert child['parent_guardian_name'] == "B. Radhika"
    assert child['consent_signature'] == "B. Radhika"
    assert child['class_and_section'] == "VII-C"
    assert child['address'] == "H/335 MMDA colony Maduravoyal Chennai - 95"
    assert child['emergency_contact_phone'] == "9851753689"
    assert child['known_allergies'] == "No"
    assert child['immunization_status'] == "Yes"

    # 5. Scan File References
    print("\n[5] Scan-File References & Previews")
    print(f"  Page 1 Preview: {child['page1_preview']}")
    print(f"  Page 2 Preview: {child['page2_preview']}")
    assert child['page1_preview'] == "/api/files/file_3f7758d48ff63f7f97755156"
    assert child['page2_preview'] == "/api/files/file_a641cfba6a071cfd2efd8ed5"

    # 6. Source is Supabase (Direct Service Call Verification)
    print("\n[6] Database Mode & Service Verification")
    rec_fetched = sb.get_record_by_id(RECORD_ID)
    assert rec_fetched is not None
    assert rec_fetched['id'] == RECORD_ID
    assert rec_fetched['vision_distant_left'] == "6/6"
    assert rec_fetched['otoscopy_findings'] == "B/L Wax"
    print("  Fetched via SupabaseService.get_record_by_id: SUCCESS")

    camps_fetched = sb.list_camps_with_stats(community_id=COMMUNITY_ID)
    assert any(c['id'] == CAMP_ID for c in camps_fetched)
    print("  Fetched via SupabaseService.list_camps_with_stats: SUCCESS")

    print("\n>>> ALL SUPABASE READ-ONLY VERIFICATION ASSERTIONS PASSED! <<<")

if __name__ == "__main__":
    main()
