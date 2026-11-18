import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from dotenv import load_dotenv
load_dotenv()
from backend.services.supabase_service import supabase_service
import requests

def main():
    print("Starting Task 13E Part A: Supabase Community Bootstrap...")
    
    # Check current status
    headers = supabase_service._headers()
    comm_check = requests.get(f"{supabase_service.url}/rest/v1/communities?code=eq.CAMP-2026", headers=headers).json()
    if comm_check:
        print(f"Community CAMP-2026 already exists: {comm_check}")
        return

    res = supabase_service.create_community_atomic(
        name="Primary Healthcare Outreach",
        code="CAMP-2026",
        creator_id="a06fbab0-901e-4741-883f-90bc51e6e3d5",
        creator_name="Dr. Confirmed Audit",
        creator_email="i2i_confirmed_1794815591@gmail.com",
        user_role="doctor",
        email_verified=True
    )
    print("create_community_atomic response:", json.dumps(res, indent=2))
    
    if not res.get("success"):
        print("FAILED to create community atomically!")
        return

    comm_id = res["community"]["id"]
    print(f"Created Community ID: {comm_id}")
    
    # Read back and verify from Supabase
    c_res = requests.get(f"{supabase_service.url}/rest/v1/communities?id=eq.{comm_id}", headers=headers).json()
    print("Readback Communities:", json.dumps(c_res, indent=2))
    
    m_res = requests.get(f"{supabase_service.url}/rest/v1/community_memberships?community_id=eq.{comm_id}", headers=headers).json()
    print("Readback Memberships:", json.dumps(m_res, indent=2))
    
    # Verify through get_user_community_memberships
    user_memberships = supabase_service.get_user_community_memberships("a06fbab0-901e-4741-883f-90bc51e6e3d5")
    print("get_user_community_memberships:", json.dumps(user_memberships, indent=2))

if __name__ == "__main__":
    main()
