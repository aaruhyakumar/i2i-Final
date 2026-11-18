import os
import sys
import json
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from backend.services.supabase_service import SupabaseService

sb = SupabaseService()
rec_id = "rec_20261117134632_f32f41"
headers = sb._headers()
url = sb.url

print("--- 1. CHECKING public.records ---")
r1 = requests.get(f"{url}/rest/v1/records?id=eq.{rec_id}", headers=headers, timeout=5).json()
if r1:
    rec = r1[0]
    print("original_ai_extraction in records:", rec.get("original_ai_extraction"))
    print("structured_record in records:", rec.get("structured_record"))
    print("extraction_notes in records:", rec.get("extraction_notes"))

print("\n--- 2. CHECKING public.paediatric_records ---")
r2 = requests.get(f"{url}/rest/v1/paediatric_records?id=eq.{rec_id}", headers=headers, timeout=5).json()
if r2:
    prec = r2[0]
    print("original_ai_extraction in paediatric_records:", prec.get("original_ai_extraction"))
    print("structured_record in paediatric_records:", prec.get("structured_record"))
    print("dob:", prec.get("dob"))
    print("blood_group:", prec.get("blood_group"))
    print("vision_distant_left:", prec.get("vision_distant_left"))
    print("vision_distant_right:", prec.get("vision_distant_right"))
    print("otoscopy_findings:", prec.get("otoscopy_findings"))

print("\n--- 3. CHECKING task14b_extraction_data.json ON LOCAL DISK ---")
artifact_path = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47\task14b_extraction_data.json"
if os.path.exists(artifact_path):
    with open(artifact_path, "r", encoding="utf-8") as f:
        art = json.load(f)
    print("Artifact has telemetry:", "telemetry" in art)
    tel = art.get("telemetry", {}).get("data", {})
    print("Extracted fields count in task14b_extraction_data.json:", len(tel.get("fields", {})))
