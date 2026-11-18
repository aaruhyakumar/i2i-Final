import os
import sys
import json
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from dotenv import load_dotenv
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", ".env")))
from backend.services.supabase_service import supabase_service
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

def main():
    print("=== TASK 11B READ-ONLY INSPECTION ===")
    
    # 1. Device state
    ws_url, _ = get_page_ws_url()
    client = DevToolsClient(ws_url)
    screen = client.evaluate("window.app.state.currentScreen")
    doc_name = client.evaluate("window.app.state.doctorName")
    doc_spec = client.evaluate("window.app.state.doctorSpecialization")
    token = client.evaluate("localStorage.getItem('auth_token')")
    ls_name = client.evaluate("localStorage.getItem('doctor_name')")
    ls_email = client.evaluate("localStorage.getItem('doctor_email')")
    ls_role = client.evaluate("localStorage.getItem('user_role')")
    
    print(f"Device Screen: {screen}")
    print(f"Device Doctor Name: {doc_name}")
    print(f"Device Doctor Spec: {doc_spec}")
    print(f"LocalStorage doctor_name: {ls_name}")
    print(f"LocalStorage doctor_email: {ls_email}")
    print(f"LocalStorage user_role: {ls_role}")
    print(f"Token length: {len(token) if token else 0}")
    
    # Decode token payload (read-only)
    from backend.services.auth_service import safe_decode_token
    if token:
        payload, err = safe_decode_token(token)
        print("Decoded token payload:", payload)
        print("Token error if any:", err)
    else:
        payload = None

    # 2. Supabase read-only inspection
    sup = supabase_service
    print("Supabase available:", sup.is_available())
    if sup.is_available():
        headers = sup._headers()
        # Query doctors view
        res = requests.get(f"{sup.url}/rest/v1/doctors?select=*&limit=10", headers=headers, timeout=5)
        print("Supabase doctors records (status %d):" % res.status_code)
        doctors = res.json()
        print(json.dumps(doctors, indent=2))
        
        # Query users table
        res_u = requests.get(f"{sup.url}/rest/v1/users?select=*&limit=10", headers=headers, timeout=5)
        print("Supabase users records (status %d):" % res_u.status_code)
        if res_u.status_code == 200:
            print(json.dumps(res_u.json(), indent=2))
        else:
            print(res_u.text)
            
        # Query auth admin users
        res_auth = requests.get(f"{sup.url}/auth/v1/admin/users", headers=headers, timeout=5)
        print("Supabase auth users (status %d):" % res_auth.status_code)
        if res_auth.status_code == 200:
            for u in res_auth.json().get('users', []):
                print(f"User: id={u.get('id')}, email={u.get('email')}, metadata={u.get('user_metadata')}")

if __name__ == "__main__":
    main()
