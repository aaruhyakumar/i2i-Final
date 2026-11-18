import os
import sys
import json
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from backend.services.supabase_service import SupabaseService

sb = SupabaseService()
headers = sb._headers()
url = sb.url
rec_id = "rec_20261117134632_f32f41"
ARTIFACT_DIR = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47"

print("=== PHASE 1: PRE-RESTORATION BACKUP & INSPECTION ===")

# 1. Read parent record
print("\n[Step 1] Reading current public.records row...")
r1 = requests.get(f"{url}/rest/v1/records?id=eq.{rec_id}", headers=headers, timeout=5).json()
parent_data = r1[0] if r1 else {}

# 2. Read child record
print("\n[Step 2] Reading current public.paediatric_records row...")
r2 = requests.get(f"{url}/rest/v1/paediatric_records?id=eq.{rec_id}", headers=headers, timeout=5).json()
child_data = r2[0] if r2 else {}

# 3. Read task14b_extraction_data.json
print("\n[Step 3] Reading task14b_extraction_data.json...")
telemetry_path = os.path.join(ARTIFACT_DIR, "task14b_extraction_data.json")
with open(telemetry_path, "r", encoding="utf-8") as f:
    raw_telemetry = json.load(f)

tel_data = raw_telemetry.get("telemetry", {}).get("data", {})
extracted_fields = tel_data.get("fields", {})

# Verify match with target patient
extracted_name = tel_data.get("person_name") or extracted_fields.get("person_name", [None])[0]
print(f"Extraction Patient Name: {extracted_name} | Target Record Name: {parent_data.get('person_name')}")
assert extracted_name == parent_data.get("person_name"), "Extraction source patient name does not match target record!"

# 4. Create timestamped backup file
timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
backup_filename = f"pre_restoration_backup_{rec_id}_{timestamp_str}.json"
backup_path = os.path.join(ARTIFACT_DIR, backup_filename)

backup_payload = {
    "backup_timestamp": datetime.now(timezone.utc).isoformat(),
    "record_id": rec_id,
    "camp_id": parent_data.get("camp_id"),
    "parent_record": parent_data,
    "child_record": child_data,
    "source_telemetry_file": telemetry_path
}

with open(backup_path, "w", encoding="utf-8") as f:
    json.dump(backup_payload, f, indent=2)

print(f"\n[Step 4] Timestamped backup created at: {backup_path}")

# 5. Identify recoverable fields & compare against current values
print("\n[Step 5] Analyzing recoverable clinical fields:")

# Map of fields to inspect for restoration
# (Label, FieldKey, ExtractedKey, ChildCol, ParentCol, Category)
clinical_field_candidates = [
    ("Vision Distant Left", "vision_distant_left", "vision_distant_left", "vision_distant_left", None, "vision"),
    ("Vision Distant Right", "vision_distant_right", "vision_distant_right", "vision_distant_right", None, "vision"),
    ("Vision Aid Status", "vision_aid_status", "vision_aid_status", None, None, "vision"),
    ("Otoscopy Left Ear", "otoscope_left_ear", "otoscope_left_ear", None, None, "systemic"),
    ("Otoscopy Right Ear", "otoscope_right_ear", "otoscope_right_ear", None, None, "systemic"),
    ("Otoscopy Findings", "otoscopy_findings", None, "otoscopy_findings", None, "systemic"),
    ("Spinal Caliper ATR", "spinal_caliper_atr", "spinal_caliper_atr", "spinal_caliper_atr", None, "growth"),
    ("Waist Circumference", "waist_circumference", "waist_circumference", None, None, "vitals"),
    ("Parent / Guardian", "parent_name", "parent_name", "parent_guardian_name", None, "personal"),
    ("Parent Signature", "parent_signature", "parent_signature", "consent_signature", "consent_signature", "consent"),
    ("Class & Section", "class_and_section", "class_and_section", "class_and_section", None, "personal"),
    ("Address", "address", "address", "address", None, "personal"),
    ("Mobile Number", "mobile_number", "mobile_number", "emergency_contact_phone", "mobile_number", "personal"),
    ("Email ID", "email", "email", None, None, "personal"),
    ("Pediatrician Name", "pediatrician_name", "pediatrician_name", None, None, "personal"),
    ("Known Allergies", "known_allergies", "known_allergies", "known_allergies", None, "medical"),
    ("Current Medication", "current_medication", "current_medication", None, None, "medical"),
    ("Past Surgery", "past_surgery", "past_surgery", None, None, "medical"),
    ("Vaccinated for Age", "vaccinated_for_age", "vaccinated_for_age", "immunization_status", None, "medical"),
    ("Behaviour Concerns", "behaviour_concerns", "behaviour_concerns", None, None, "medical"),
    ("Other Medical Issues", "other_medical_issues", "other_medical_issues", None, None, "medical"),
    ("Family Adults Count", "family_adults_count", "family_adults_count", None, None, "family"),
    ("Family Minors Count", "family_minors_count", "family_minors_count", None, None, "family"),
    ("Dental Status", "dental_status", "dental_status", None, None, "dental"),
    ("Systemic Hair", "sys_hair", "sys_hair", None, None, "systemic"),
    ("Systemic Nose", "sys_nose", "sys_nose", None, None, "systemic"),
    ("Systemic Throat", "sys_throat", "sys_throat", "oral_cavity_throat", None, "systemic"),
    ("Systemic Skin", "sys_skin", "sys_skin", None, None, "systemic"),
    ("Consent Screening", "consent_screening", "consent_screening", "consent_given", "consent_given", "consent"),
    ("Consent First Aid", "consent_first_aid", "consent_first_aid", None, None, "consent"),
    ("Doctor Signature", "doctor_signature", "doctor_signature", None, None, "referral")
]

restoration_plan = []

for label, key, ext_key, child_col, parent_col, cat in clinical_field_candidates:
    ext_val = None
    ext_conf = None
    if ext_key and ext_key in extracted_fields:
        ext_val, ext_conf = extracted_fields[ext_key][0], extracted_fields[ext_key][1]
    elif ext_key and ext_key in tel_data:
        ext_val = tel_data[ext_key]
        ext_conf = 1.0

    current_val = None
    if child_col and child_data.get(child_col) is not None:
        current_val = child_data.get(child_col)
    elif parent_col and parent_data.get(parent_col) is not None:
        current_val = parent_data.get(parent_col)

    # Determine action
    if ext_val is not None and ext_val != "":
        if current_val is None or current_val == "" or current_val == []:
            action = "RESTORE"
        else:
            action = "KEEP_EXISTING"
    else:
        action = "SKIP_EMPTY_IN_SOURCE"

    restoration_plan.append({
        "label": label,
        "key": key,
        "category": cat,
        "extracted_value": ext_val,
        "confidence": ext_conf,
        "current_value": current_val,
        "child_column": child_col,
        "parent_column": parent_col,
        "action": action
    })

print(f"{'Field':<24} | {'Action':<15} | {'Extracted (14B)':<25} | {'Current in DB':<15}")
print("-" * 85)
for item in restoration_plan:
    act = item["action"]
    ext_s = str(item["extracted_value"])[:23] if item["extracted_value"] is not None else "<none>"
    cur_s = str(item["current_value"])[:13] if item["current_value"] is not None else "<empty>"
    print(f"{item['label']:<24} | {act:<15} | {ext_s:<25} | {cur_s:<15}")

plan_path = os.path.join(ARTIFACT_DIR, f"task19_restoration_plan_{timestamp_str}.json")
with open(plan_path, "w", encoding="utf-8") as f:
    json.dump({
        "timestamp": timestamp_str,
        "backup_file": backup_filename,
        "plan": restoration_plan
    }, f, indent=2)

print(f"\n[PHASE 1 COMPLETE] Plan saved to: {plan_path}")
