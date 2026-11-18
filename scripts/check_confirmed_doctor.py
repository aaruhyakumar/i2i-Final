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

user_id = "a06fbab0-901e-4741-883f-90bc51e6e3d5"
email = "i2i_confirmed_1794815591@gmail.com"

# Check doctors view
res_doc = requests.get(f"{url}/rest/v1/doctors?id=eq.{user_id}", headers=headers).json()
print("Doctor by ID:", res_doc)
res_doc_email = requests.get(f"{url}/rest/v1/doctors?email=eq.{email}", headers=headers).json()
print("Doctor by email:", res_doc_email)

# Check user_profiles
res_prof = requests.get(f"{url}/rest/v1/user_profiles?user_id=eq.{user_id}", headers=headers).json()
print("user_profiles:", res_prof)

# Check users table
res_u = requests.get(f"{url}/rest/v1/users?id=eq.{user_id}", headers=headers).json()
print("users table:", res_u)

# Check community_memberships
res_mem = requests.get(f"{url}/rest/v1/community_memberships?select=*&limit=50", headers=headers)
print(f"community_memberships (status {res_mem.status_code}):", res_mem.text)
