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

print("--- FETCHING FROM public.records ---")
r1 = requests.get(f"{url}/rest/v1/records?id=eq.{rec_id}", headers=headers, timeout=5)
data1 = r1.json()
print("records count:", len(data1))
if data1:
    r = data1[0]
    print("records row keys:", sorted(list(r.keys())))
    print("id:", r.get("id"))
    print("person_name:", repr(r.get("person_name")))
    print("age:", repr(r.get("age")))
    print("gender:", repr(r.get("gender")))
    print("height:", repr(r.get("height")))
    print("weight:", repr(r.get("weight")))
    print("blood_pressure:", repr(r.get("blood_pressure")))
    print("is_paediatric:", repr(r.get("is_paediatric")))
    print("status:", repr(r.get("status")))
    print("doctor_name:", repr(r.get("doctor_name")))
    print("camp_id:", repr(r.get("camp_id")))
    sr = r.get("structured_record")
    print("structured_record type:", type(sr))
    print("structured_record content:", repr(sr))

print("\n--- FETCHING FROM public.paediatric_records ---")
r2 = requests.get(f"{url}/rest/v1/paediatric_records?id=eq.{rec_id}", headers=headers, timeout=5)
data2 = r2.json()
print("paediatric_records count:", len(data2))
if data2:
    pr = data2[0]
    print("paediatric_records row keys:", sorted(list(pr.keys())))
    for k in sorted(pr.keys()):
        print(f"  {k}: {repr(pr[k])}")
