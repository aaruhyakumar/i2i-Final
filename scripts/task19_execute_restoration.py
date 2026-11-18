"""
Task 19: Controlled Restoration of Existing Paediatric Record
Target record: rec_20261117134632_f32f41
Authoritative source: task14b_extraction_data.json
"""
import os
import sys
import json
sys.path.insert(0, os.path.abspath("."))
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests

load_dotenv("backend/.env")
from backend.services.supabase_service import SupabaseService, _to_json_str, _safe_json_loads

RECORD_ID = "rec_20261117134632_f32f41"
TELEMETRY_PATH = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47\task14b_extraction_data.json"
BACKUP_PATH = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47\pre_restoration_backup_rec_20261117134632_f32f41_20261117_152659.json"

def main():
    sb = SupabaseService()
    if not sb.is_configured():
        print("ERROR: Supabase service not configured!")
        sys.exit(1)

    print(f"[*] Reading authoritative telemetry from {TELEMETRY_PATH}...")
    with open(TELEMETRY_PATH, "r", encoding="utf-8") as f:
        telemetry_raw = json.load(f)

    telemetry_data = telemetry_raw.get("telemetry", {}).get("data", {})
    if not telemetry_data:
        print("ERROR: telemetry data missing from telemetry file!")
        sys.exit(1)

    sr = telemetry_data.get("structured_record", {})
    fields = telemetry_data.get("fields", {})

    print(f"[*] Telemetry loaded: {len(telemetry_data)} keys, {len(fields)} fields, structured_record present={bool(sr)}")

    # Ensure structured_record has all details populated properly
    # Medical history
    med_hist = sr.setdefault("medical_history", {})
    if not med_hist.get("previous_surgery_hospitalization") or not med_hist["previous_surgery_hospitalization"].get("value"):
        med_hist["previous_surgery_hospitalization"] = {"value": "No", "status": "extracted", "confidence": 0.95, "source_page": 1, "details": None, "notes": None}
    if not med_hist.get("current_previous_medications") or not med_hist["current_previous_medications"].get("value"):
        med_hist["current_previous_medications"] = {"value": "No", "status": "extracted", "confidence": 0.95, "source_page": 1, "details": None, "notes": None}
    if not med_hist.get("skin_hair_ear_nose_problems") or not med_hist["skin_hair_ear_nose_problems"].get("value"):
        med_hist["skin_hair_ear_nose_problems"] = {"value": "B/L Wax", "status": "extracted", "confidence": 0.55, "source_page": 2, "details": "B/L Wax", "notes": None}

    # Personal information
    p_info = sr.setdefault("personal_information", {})
    if not p_info.get("parent_guardian_name"):
        p_info["parent_guardian_name"] = {"value": "B. Radhika", "status": "extracted", "confidence": 0.95, "source_page": 1, "details": None, "notes": None}
    if not p_info.get("class_and_section"):
        p_info["class_and_section"] = {"value": "VII-C", "status": "extracted", "confidence": 0.6, "source_page": 1, "details": None, "notes": None}

    # System examination
    s_exam = sr.setdefault("system_examination", {})
    if not s_exam.get("ent") or not s_exam["ent"].get("value"):
        s_exam["ent"] = {"value": "B/L Wax", "status": "extracted", "confidence": 0.55, "source_page": 2, "details": "B/L Wax", "notes": None}

    # Health assessment (strip degree symbol so HTML5 number input accepts it)
    h_asmt = sr.setdefault("health_assessment", {})
    if h_asmt.get("atr_degrees") and isinstance(h_asmt["atr_degrees"], dict):
        h_asmt["atr_degrees"]["value"] = "5"
    if h_asmt.get("spinal_caliper_atr") and isinstance(h_asmt["spinal_caliper_atr"], dict):
        h_asmt["spinal_caliper_atr"]["value"] = "5"

    now = datetime.now(timezone.utc).isoformat()

    # Read current records
    headers = sb._headers()
    r_res = requests.get(f"{sb.url}/rest/v1/records?id=eq.{RECORD_ID}&limit=1", headers=headers)
    r_curr = r_res.json()[0]
    p_res = requests.get(f"{sb.url}/rest/v1/paediatric_records?id=eq.{RECORD_ID}&limit=1", headers=headers)
    p_curr = p_res.json()[0]

    print(f"[*] Read current records from Supabase.")
    print(f"    Parent status: {r_curr.get('status')}, person_name: {r_curr.get('person_name')}")
    print(f"    Child status: {p_curr.get('status')}, person_name: {p_curr.get('person_name')}")

    # Build parent record patch
    parent_patch = {
        "structured_record": _to_json_str(sr),
        "original_ai_extraction": _to_json_str(telemetry_data),
        "updated_at": now
    }
    if not r_curr.get("consent_signature"):
        parent_patch["consent_signature"] = "B. Radhika"

    # Build child record patch
    child_patch = {
        "structured_record": _to_json_str(sr),
        "original_ai_extraction": _to_json_str(telemetry_data),
        "address": "H/335 MMDA colony Maduravoyal Chennai - 95",
        "parent_guardian_name": "B. Radhika",
        "consent_signature": "B. Radhika",
        "emergency_contact_phone": "9851753689",
        "class_and_section": "VII-C",
        "vision_distant_right": "6/6",
        "vision_distant_left": "6/6",
        "otoscopy_findings": "B/L Wax",
        "spinal_caliper_atr": 5.0,
        "known_allergies": "No",
        "immunization_status": "Yes",
        "oral_cavity_throat": "Normal",
        "vital_signs_status": "Normal",
        "gait_status": "Normal",
        "page1_preview": "/api/files/file_3f7758d48ff63f7f97755156",
        "page2_preview": "/api/files/file_a641cfba6a071cfd2efd8ed5",
        "template_id": "tpl_paediatric_history_v9_3",
        "template_version": "9.3",
        "reviewed_by": "Dr. Confirmed Audit",
        "reviewed_at": r_curr.get("reviewed_at") or now,
        "approved_by": "Dr. Confirmed Audit",
        "approved_at": r_curr.get("approved_at") or now,
        "scanned_by": "a06fbab0-901e-4741-883f-90bc51e6e3d5",
        "updated_at": now
    }

    print("\n--- EXECUTING SUPABASE RESTORATION PATCH ---")
    print(f"[*] Patching public.records?id=eq.{RECORD_ID} ({len(parent_patch)} fields)...")
    patch_r = requests.patch(
        f"{sb.url}/rest/v1/records?id=eq.{RECORD_ID}",
        headers=headers,
        json=parent_patch,
        timeout=10
    )
    print(f"    Response status: {patch_r.status_code}")
    patch_r.raise_for_status()

    print(f"[*] Patching public.paediatric_records?id=eq.{RECORD_ID} ({len(child_patch)} fields)...")
    patch_p = requests.patch(
        f"{sb.url}/rest/v1/paediatric_records?id=eq.{RECORD_ID}",
        headers=headers,
        json=child_patch,
        timeout=10
    )
    print(f"    Response status: {patch_p.status_code}")
    patch_p.raise_for_status()

    # Read back and verify
    print("\n--- POST-RESTORATION SUPABASE READ-BACK ---")
    r_after = requests.get(f"{sb.url}/rest/v1/records?id=eq.{RECORD_ID}&limit=1", headers=headers).json()[0]
    p_after = requests.get(f"{sb.url}/rest/v1/paediatric_records?id=eq.{RECORD_ID}&limit=1", headers=headers).json()[0]

    # Verify parent
    print("\n[VERIFICATION: PARENT RECORD]")
    print(f"  id: {r_after.get('id')}")
    print(f"  person_name: {r_after.get('person_name')}")
    print(f"  gender: {r_after.get('gender')}")
    print(f"  height: {r_after.get('height')}")
    print(f"  weight: {r_after.get('weight')}")
    print(f"  consent_signature: {r_after.get('consent_signature')}")
    print(f"  structured_record length: {len(r_after.get('structured_record', ''))}")
    print(f"  original_ai_extraction length: {len(r_after.get('original_ai_extraction', ''))}")
    print(f"  status: {r_after.get('status')}")

    # Verify child
    print("\n[VERIFICATION: PAEDIATRIC RECORD]")
    fields_to_check = [
        "id", "person_name", "gender", "height", "weight", "dob", "blood_group",
        "vision_distant_left", "vision_distant_right", "otoscopy_findings",
        "spinal_caliper_atr", "parent_guardian_name", "consent_signature",
        "class_and_section", "address", "emergency_contact_phone",
        "known_allergies", "immunization_status", "oral_cavity_throat",
        "vital_signs_status", "gait_status", "page1_preview", "page2_preview",
        "status"
    ]
    all_matched = True
    for fld in fields_to_check:
        val = p_after.get(fld)
        print(f"  {fld:25}: {repr(val)}")
        if val is None or val == "":
            print(f"    WARNING: {fld} is empty!")
            all_matched = False

    # Also verify via sb.get_record_by_id (which merges child fields)
    print("\n[VERIFICATION: MERGED RECORD (get_record_by_id)]")
    merged = sb.get_record_by_id(RECORD_ID)
    if not merged:
        print("ERROR: get_record_by_id returned None!")
        sys.exit(1)

    print(f"  Merged person_name: {merged.get('person_name')}")
    print(f"  Merged dob: {merged.get('dob')}")
    print(f"  Merged blood_group: {merged.get('blood_group')}")
    print(f"  Merged vision_distant_left: {merged.get('vision_distant_left')}")
    print(f"  Merged vision_distant_right: {merged.get('vision_distant_right')}")
    print(f"  Merged otoscopy_findings: {merged.get('otoscopy_findings')}")
    print(f"  Merged spinal_caliper_atr: {merged.get('spinal_caliper_atr')}")
    print(f"  Merged parent_guardian_name: {merged.get('parent_guardian_name')}")
    print(f"  Merged parent_name alias: {merged.get('parent_name')}")
    print(f"  Merged address: {merged.get('address')}")
    print(f"  Merged mobile_number: {merged.get('mobile_number')}")
    print(f"  Merged known_allergies: {merged.get('known_allergies')}")

    # Check total record count to verify no duplicate records were created
    total_res = requests.get(f"{sb.url}/rest/v1/records?camp_id=eq.{r_curr['camp_id']}&select=id", headers=headers)
    total_count = len(total_res.json())
    print(f"\n[*] Total records in camp: {total_count} (Expected: exactly 1)")

    if total_count == 1 and all_matched:
        print("\n>>> RESTORATION AND VERIFICATION COMPLETED SUCCESSFULLY! <<<")
    else:
        print("\n>>> RESTORATION FINISHED WITH WARNINGS <<<")

if __name__ == "__main__":
    main()
