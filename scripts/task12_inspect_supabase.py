import os
import sys
import json
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", ".env")))
from backend.services.supabase_service import supabase_service

def inspect():
    sup = supabase_service
    headers = sup._headers()
    url = sup.url

    print("=== SUPABASE IDENTITY & MEMBERSHIP INSPECTION ===")
    print(f"Supabase URL: {url}")
    print(f"Supabase available: {sup.is_available()}")

    # 1. Auth Admin Users
    res_auth = requests.get(f"{url}/auth/v1/admin/users", headers=headers, timeout=5)
    print(f"\n--- Auth Users (status {res_auth.status_code}) ---")
    auth_users = []
    if res_auth.status_code == 200:
        auth_users = res_auth.json().get("users", [])
        for u in auth_users:
            print(f"ID: {u.get('id')}")
            print(f"  Email: {u.get('email')}")
            print(f"  Confirmed At: {u.get('email_confirmed_at')}")
            print(f"  Last Sign In: {u.get('last_sign_in_at')}")
            print(f"  User Metadata: {u.get('user_metadata')}")
            print(f"  App Metadata: {u.get('app_metadata')}")
            print(f"  Banned: {u.get('banned_until')}")
            print()

    # 2. Public Doctors View / Table
    res_doc = requests.get(f"{url}/rest/v1/doctors?select=*&limit=50", headers=headers, timeout=5)
    print(f"\n--- Public Doctors View / Table (status {res_doc.status_code}) ---")
    if res_doc.status_code == 200:
        docs = res_doc.json()
        print(f"Total doctors found: {len(docs)}")
        print(json.dumps(docs, indent=2))
    else:
        print(res_doc.text)

    # 3. Public Users Table
    res_u = requests.get(f"{url}/rest/v1/users?select=*&limit=50", headers=headers, timeout=5)
    print(f"\n--- Public Users Table (status {res_u.status_code}) ---")
    if res_u.status_code == 200:
        users = res_u.json()
        print(f"Total users found: {len(users)}")
        print(json.dumps(users, indent=2))
    else:
        print(res_u.text)

    # 4. Public User Profiles Table (if exists)
    res_prof = requests.get(f"{url}/rest/v1/user_profiles?select=*&limit=50", headers=headers, timeout=5)
    print(f"\n--- Public User Profiles Table (status {res_prof.status_code}) ---")
    if res_prof.status_code == 200:
        profs = res_prof.json()
        print(f"Total profiles found: {len(profs)}")
        print(json.dumps(profs, indent=2))
    else:
        print(res_prof.text)

    # 5. Communities
    res_comm = requests.get(f"{url}/rest/v1/communities?select=*&limit=50", headers=headers, timeout=5)
    print(f"\n--- Public Communities Table (status {res_comm.status_code}) ---")
    if res_comm.status_code == 200:
        comms = res_comm.json()
        print(f"Total communities found: {len(comms)}")
        print(json.dumps(comms, indent=2))
    else:
        print(res_comm.text)

    # 6. Camps
    res_camps = requests.get(f"{url}/rest/v1/camps?select=*&limit=50", headers=headers, timeout=5)
    print(f"\n--- Public Camps Table (status {res_camps.status_code}) ---")
    if res_camps.status_code == 200:
        camps = res_camps.json()
        print(f"Total camps found: {len(camps)}")
        print(json.dumps(camps, indent=2))
    else:
        print(res_camps.text)

if __name__ == "__main__":
    inspect()
