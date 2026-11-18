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

# Fetch OpenAPI spec from PostgREST root
res = requests.get(f"{sup.url}/rest/v1/?apikey={sup.key}", headers=headers)
spec = res.json()

print("OpenAPI Title:", spec.get("info", {}).get("title"))

definitions = spec.get("definitions", {})
print("\nAvailable Tables / Views in PostgREST:")
for name in sorted(definitions.keys()):
    print(f" - {name}")

for table in ["doctors", "users", "user_profiles", "community_memberships", "communities", "camps"]:
    if table in definitions:
        print(f"\n================ TABLE: {table} ================")
        props = definitions[table].get("properties", {})
        req = definitions[table].get("required", [])
        print(f"Required fields: {req}")
        for p, d in props.items():
            t = d.get("type")
            fmt = d.get("format", "")
            desc = d.get("description", "")
            print(f"  {p:25} : type={t} format={fmt} desc={desc}")
