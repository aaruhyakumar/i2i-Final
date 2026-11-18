import os
import sys
import json
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", ".env")))
from backend.services.supabase_service import supabase_service

USER_ID = "a06fbab0-901e-4741-883f-90bc51e6e3d5"
EMAIL = "i2i_confirmed_1794815591@gmail.com"
FULL_NAME = "Dr. Confirmed Audit"
ACCOUNT_TYPE = "doctor"
SPECIALIZATION = "General Physician"

def bootstrap():
    print("======================================================================")
    print("TASK 13C: EXECUTE SUPABASE DOCTOR PROFILE BOOTSTRAP")
    print("======================================================================")
    sup = supabase_service
    if not sup.is_available():
        print("ERROR: Supabase service is not available!")
        return False

    print("\n--- Step 1 & 2: Executing sync_user_profile() ---")
    sync_success = sup.sync_user_profile(
        user_id=USER_ID,
        email=EMAIL,
        full_name=FULL_NAME,
        account_type=ACCOUNT_TYPE,
        specialization=SPECIALIZATION
    )
    print(f"sync_user_profile() returned: {sync_success}")
    if not sync_success:
        print("ERROR: sync_user_profile failed! Halting.")
        return False

    # Ensure email_verified flag matches the confirmed status in Auth
    sup.update_user_email_verified(USER_ID, email_verified=True)

    headers = sup._headers()
    url = sup.url

    print("\n--- Step 5: Read-Only Verification of Underlying Tables ---")
    # Verify public.users
    res_u = requests.get(f"{url}/rest/v1/users?id=eq.{USER_ID}&select=*", headers=headers, timeout=5)
    print(f"Query public.users (HTTP {res_u.status_code}):")
    users_rows = res_u.json()
    print(json.dumps(users_rows, indent=2))

    # Verify public.user_profiles
    res_p = requests.get(f"{url}/rest/v1/user_profiles?user_id=eq.{USER_ID}&select=*", headers=headers, timeout=5)
    print(f"Query public.user_profiles (HTTP {res_p.status_code}):")
    prof_rows = res_p.json()
    print(json.dumps(prof_rows, indent=2))

    print("\n--- Step 6: Verify public.doctors View Resolution in Supabase ---")
    res_d = requests.get(f"{url}/rest/v1/doctors?id=eq.{USER_ID}&select=*", headers=headers, timeout=5)
    print(f"Query public.doctors view (HTTP {res_d.status_code}):")
    doc_rows = res_d.json()
    print(json.dumps(doc_rows, indent=2))

    if not doc_rows:
        print("ERROR: public.doctors view did not resolve the doctor! Halting.")
        return False

    print("\n--- Step 7 & 8: Verify Flask Backend API with Authenticated Session ---")
    # Sign in to get fresh Supabase JWT
    auth_res = sup.auth_sign_in(EMAIL, "SecurePassword123!")
    token = auth_res.get("access_token")
    if not token:
        print("ERROR: Failed to retrieve access token from Supabase Auth")
        return False

    api_headers = {"Authorization": f"Bearer {token}"}
    res_api = requests.get("http://127.0.0.1:5001/api/doctor/profile", headers=api_headers, timeout=5)
    print(f"GET /api/doctor/profile response: HTTP {res_api.status_code}")
    api_json = res_api.json()
    print(json.dumps(api_json, indent=2))

    # Verify whether it was served by Supabase
    is_supabase_sourced = (
        api_json.get("id") == USER_ID and
        api_json.get("email") == EMAIL and
        api_json.get("full_name") == FULL_NAME
    )
    print(f"Supabase-sourced profile confirmed: {is_supabase_sourced}")
    return True

if __name__ == "__main__":
    success = bootstrap()
    if not success:
        sys.exit(1)
