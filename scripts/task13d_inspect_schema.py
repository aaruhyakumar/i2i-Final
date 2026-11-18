import os
import sys
import json
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", ".env")))
from backend.services.supabase_service import supabase_service

sup = supabase_service
headers = sup._headers()
url = sup.url

print("=== SUPABASE CAMPS & COMMUNITIES SCHEMA INSPECTION ===")

# Query OpenAPI spec for camps and communities properties
res = requests.get(f"{url}/rest/v1/?apikey={sup.key}", headers=headers)
spec = res.json()
defs = spec.get("definitions", {})

for t in ["communities", "community_memberships", "camps"]:
    print(f"\n--- Table: {t} ---")
    data = defs.get(t, {})
    print("Required:", data.get("required"))
    for col, meta in data.get("properties", {}).items():
        desc = meta.get("description", "")
        fk = ""
        if "Foreign Key" in desc:
            fk = f" [FK -> {desc.split('Foreign Key to ')[-1].split('.')[0]}]"
        print(f"  {col:20} : {meta.get('type'):10} format={meta.get('format', ''):15} {fk}")

# Check actual existing rows in communities, community_memberships, and camps
print("\n--- Current Data in Supabase ---")
for t in ["communities", "community_memberships", "camps"]:
    r = requests.get(f"{url}/rest/v1/{t}?select=*&limit=10", headers=headers)
    rows = r.json() if r.status_code == 200 else r.text
    print(f"Rows in {t} (status {r.status_code}): {len(rows) if isinstance(rows, list) else rows}")
    if isinstance(rows, list) and rows:
        print(json.dumps(rows, indent=2))
