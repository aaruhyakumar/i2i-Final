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

# Test batch query syntax: in.(id1,id2)
res = requests.get(f"{url}/rest/v1/paediatric_records?id=in.({rec_id})", headers=headers, timeout=5)
print("Status code:", res.status_code)
data = res.json()
print("Found count:", len(data))
if data:
    pr = data[0]
    print("dob:", pr.get("dob"))
    print("blood_group:", pr.get("blood_group"))
    print("height:", pr.get("height"))
    print("weight:", pr.get("weight"))
