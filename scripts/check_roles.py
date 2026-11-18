import os, sys, json, requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", ".env")))
from backend.services.supabase_service import supabase_service

sup = supabase_service
headers = sup._headers()
res = requests.get(f"{sup.url}/rest/v1/roles?select=*&limit=10", headers=headers)
print("Roles in Supabase (status %d):" % res.status_code)
if res.status_code == 200:
    for r in res.json():
        print("Role:", r.get("id"), r.get("name"))
else:
    print(res.text)
