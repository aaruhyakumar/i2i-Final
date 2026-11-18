import os
import sys
import json
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from backend.services.supabase_service import SupabaseService

sb = SupabaseService()
rec_id = "rec_20261117134632_f32f41"
camp_id = "d95c06fa-5c10-4991-8afb-6071bef5b3fe"

print("--- 1. TESTING get_record_by_id ---")
rec = sb.get_record_by_id(rec_id)
print("Found rec:", bool(rec))
if rec:
    print("  id:", rec.get("id"))
    print("  person_name:", rec.get("person_name"))
    print("  gender:", rec.get("gender"))
    print("  height:", rec.get("height"))
    print("  weight:", rec.get("weight"))
    print("  dob:", repr(rec.get("dob")))
    print("  blood_group:", repr(rec.get("blood_group")))
    print("  school_name:", repr(rec.get("school_name")))

print("\n--- 2. TESTING list_records ---")
recs = sb.list_records(camp_id=camp_id)
print("Found records count:", len(recs) if recs else 0)
if recs:
    r = recs[0]
    print("  id:", r.get("id"))
    print("  person_name:", r.get("person_name"))
    print("  dob:", repr(r.get("dob")))
    print("  blood_group:", repr(r.get("blood_group")))
