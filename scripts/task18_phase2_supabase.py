import os
import sys
import json
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from backend.services.supabase_service import SupabaseService

sb = SupabaseService()
headers = sb._headers()
url = sb.url
rec_id = "rec_20261117134632_f32f41"
camp_id = "d95c06fa-5c10-4991-8afb-6071bef5b3fe"

print("=== PHASE 2: READ-ONLY SUPABASE VERIFICATION ===")

# 1. Direct REST read from public.records
print("\n[Query 1] Direct read from public.records:")
r1 = requests.get(f"{url}/rest/v1/records?id=eq.{rec_id}", headers=headers, timeout=5).json()
rec_parent = r1[0] if r1 else {}

# 2. Direct REST read from public.paediatric_records
print("\n[Query 2] Direct read from public.paediatric_records:")
r2 = requests.get(f"{url}/rest/v1/paediatric_records?id=eq.{rec_id}", headers=headers, timeout=5).json()
rec_child = r2[0] if r2 else {}

# 3. Service individual record retrieval
print("\n[Query 3] SupabaseService.get_record_by_id():")
single_rec = sb.get_record_by_id(rec_id) or {}

# 4. Service camp list retrieval
print("\n[Query 4] SupabaseService.list_records(camp_id):")
camp_records = sb.list_records(camp_id=camp_id, limit=100) or []
matching_list_rec = next((r for r in camp_records if r["id"] == rec_id), {})

# Field reconciliation matrix
fields_to_check = [
    ("Patient Name", "person_name"),
    ("Gender", "gender"),
    ("DOB", "dob"),
    ("Blood Group", "blood_group"),
    ("Height", "height"),
    ("Weight", "weight"),
    ("Waist Circumference", "waist_circumference"),
    ("Vision (Left)", "vision_distant_left"),
    ("Vision (Right)", "vision_distant_right"),
    ("Otoscopy Findings", "otoscopy_findings"),
    ("Spinal Caliper ATR", "spinal_caliper_atr"),
    ("Parent / Guardian", "parent_guardian_name"),
    ("Class and Section", "class_and_section"),
    ("Address", "address"),
    ("Mobile Number", "mobile_number")
]

print("\n--- 14-FIELD RECONCILIATION AUDIT ---")
print(f"{'Field':<22} | {'Parent table':<15} | {'Child table':<15} | {'Single Get':<15} | {'List Get':<15} | Consistent?")
print("-" * 95)

all_consistent = True
for label, key in fields_to_check:
    val_p = str(rec_parent.get(key, ""))
    val_c = str(rec_child.get(key, ""))
    val_s = str(single_rec.get(key, ""))
    val_l = str(matching_list_rec.get(key, ""))
    
    # Check consistency between single_rec and matching_list_rec
    consistent = (val_s == val_l)
    if not consistent:
        all_consistent = False
    
    val_p_str = (val_p[:12] + "..") if len(val_p) > 14 else (val_p or "<none>")
    val_c_str = (val_c[:12] + "..") if len(val_c) > 14 else (val_c or "<none>")
    val_s_str = (val_s[:12] + "..") if len(val_s) > 14 else (val_s or "<none>")
    val_l_str = (val_l[:12] + "..") if len(val_l) > 14 else (val_l or "<none>")
    
    status_str = "YES" if consistent else "MISMATCH"
    print(f"{label:<22} | {val_p_str:<15} | {val_c_str:<15} | {val_s_str:<15} | {val_l_str:<15} | {status_str}")

print("\nConsistency Result (Single Get vs List Get):", "PASS (100% Consistent)" if all_consistent else "FAIL")
assert all_consistent, "Discrepancy detected between single and list retrieval!"
