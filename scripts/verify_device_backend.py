import json
import requests
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

def verify():
    ws_url, _ = get_page_ws_url()
    c = DevToolsClient(ws_url)
    tok = c.evaluate("localStorage.getItem('auth_token')")
    print(f"Device Auth Token: {tok[:20]}...{tok[-10:] if tok else ''}")
    
    # 1. Check /api/stats
    res = requests.get("http://127.0.0.1:5001/api/stats", headers={"Authorization": f"Bearer {tok}"})
    print(f"\n/api/stats Status: {res.status_code}")
    data = res.json()
    print(f"Supabase Configured: {data.get('supabase_configured')}")
    print(f"Supabase Available:  {data.get('supabase_available')}")
    print(f"Supabase Tables Ready: {data.get('supabase_tables_ready')}")
    print(f"Supabase URL: {data.get('supabase_url')}")
    print(f"Mode Label: {data.get('mode_label')}")
    
    # 2. Check /api/doctor/profile
    res_prof = requests.get("http://127.0.0.1:5001/api/doctor/profile", headers={"Authorization": f"Bearer {tok}"})
    print(f"\n/api/doctor/profile Status: {res_prof.status_code}")
    prof_data = res_prof.json()
    print("Doctor Profile Payload:", json.dumps(prof_data, indent=2))
    
    # 3. Check /api/camps
    res_camps = requests.get("http://127.0.0.1:5001/api/camps", headers={"Authorization": f"Bearer {tok}"})
    print(f"\n/api/camps Status: {res_camps.status_code}")
    camps = res_camps.json()
    print(f"Loaded {len(camps)} camps from backend for device")

if __name__ == "__main__":
    verify()
