import time
import os
import sys
import subprocess
import json
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

ADB = os.path.expandvars(r"$LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe")
ARTIFACT_DIR = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47"

def capture(name):
    remote = f"/sdcard/{name}.png"
    local = os.path.join(ARTIFACT_DIR, f"{name}.png")
    subprocess.run([ADB, "shell", "screencap", "-p", remote], check=True)
    subprocess.run([ADB, "pull", remote, local], check=True)
    print(f"[Screenshot] Saved: {local}")
    return local

def run():
    print("======================================================================")
    print("TASK 13A: SUPABASE-BACKED REAL DEVICE LOGIN")
    print("======================================================================")

    # 1. Connect DevTools
    ws_url, _ = get_page_ws_url()
    client = DevToolsClient(ws_url)

    # 2. Sign out of demo@clinic.camp
    print("\n--- Step 2: Sign out of demo account ---")
    client.evaluate("window.app.logout();")
    time.sleep(2)
    screen_logged_out = client.evaluate("window.app.state.currentScreen")
    token_cleared = client.evaluate("localStorage.getItem('auth_token')")
    print(f"Screen after logout: {screen_logged_out}")
    print(f"Token cleared: {token_cleared is None}")
    capture("task13a_step2_logged_out")

    # Ensure on login screen
    client.evaluate("window.app.go('login');")
    time.sleep(1)

    # 3. Sign in using confirmed test account
    print("\n--- Step 3: Sign in using confirmed test account credentials ---")
    confirmed_email = "i2i_confirmed_1794815591@gmail.com"
    confirmed_password = "SecurePassword123!"

    client.evaluate(f"""
        document.getElementById('login-email').value = '{confirmed_email}';
        document.getElementById('login-password').value = '{confirmed_password}';
        window.app.login();
    """)
    print("Submitted login form on device, waiting 4 seconds for Supabase authentication...")
    time.sleep(4)

    # 4. Verify login succeeds
    print("\n--- Step 4: Verify login success ---")
    screen_after_login = client.evaluate("window.app.state.currentScreen")
    auth_token = client.evaluate("localStorage.getItem('auth_token')")
    doctor_name = client.evaluate("window.app.state.doctorName")
    user_role = client.evaluate("window.app.state.userRole")
    
    print(f"Current Screen: {screen_after_login}")
    print(f"Auth Token present: {bool(auth_token)} (len={len(auth_token) if auth_token else 0})")
    print(f"Token prefix: {(auth_token or '')[:30]}")
    print(f"Doctor Name in state: {doctor_name}")
    print(f"User Role: {user_role}")
    capture("task13a_step4_login_success")

    # 5. Open doctor profile and camp listing screens
    print("\n--- Step 5: Open Doctor Profile & Camp Listing ---")
    # Open Profile (settings)
    client.evaluate("window.app.go('settings');")
    time.sleep(2)
    profile_screen = client.evaluate("window.app.state.currentScreen")
    profile_name = client.evaluate("document.getElementById('settings-doctor-name')?.innerText")
    profile_spec = client.evaluate("document.getElementById('settings-doctor-spec')?.innerText")
    print(f"Profile Screen: {profile_screen}")
    print(f"Displayed Doctor Name: {profile_name}")
    print(f"Displayed Specialization: {profile_spec}")
    capture("task13a_step5_profile_screen")

    # Open Camp Listing / Home
    client.evaluate("window.app.go('home');")
    time.sleep(2)
    home_screen = client.evaluate("window.app.state.currentScreen")
    active_camp_text = client.evaluate("document.querySelector('.hero-camp h2, .active-camp-title, .camp-name')?.innerText || document.querySelector('.card')?.innerText")
    print(f"Home Screen: {home_screen}")
    print(f"Active Camp Display: {active_camp_text}")
    capture("task13a_step5_camps_screen")

    # Open camp picker modal
    client.evaluate("if (window.app.openCampPicker) window.app.openCampPicker();")
    time.sleep(1.5)
    capture("task13a_step5_camp_picker_modal")
    # Dismiss picker
    client.evaluate("if (window.app.closeCampPicker) window.app.closeCampPicker();")
    time.sleep(1)

    # 6. Backend trace verification
    print("\n--- Step 6: Backend Log & Route Verification ---")
    # Make direct requests using the device's auth_token to observe backend handling
    headers = {"Authorization": f"Bearer {auth_token}"}
    
    res_prof = requests.get("http://127.0.0.1:5001/api/doctor/profile", headers=headers, timeout=5)
    print(f"Backend GET /api/doctor/profile: HTTP {res_prof.status_code}")
    prof_data = res_prof.json()
    print("Profile JSON:", json.dumps(prof_data, indent=2))
    
    res_camps = requests.get("http://127.0.0.1:5001/api/camps", headers=headers, timeout=5)
    print(f"Backend GET /api/camps: HTTP {res_camps.status_code}")
    camps_data = res_camps.json()
    print(f"Camps count: {len(camps_data)}")
    print("Camps JSON:", json.dumps(camps_data, indent=2))

    return {
        "screen_after_login": screen_after_login,
        "token_len": len(auth_token) if auth_token else 0,
        "prof_status": res_prof.status_code,
        "camps_status": res_camps.status_code,
        "camps_count": len(camps_data),
        "prof_data": prof_data,
        "camps_data": camps_data
    }

if __name__ == "__main__":
    run()
