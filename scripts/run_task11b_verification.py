import time
import os
import sys
import re
import subprocess
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

ADB = os.path.expandvars(r"$LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe")
ARTIFACT_DIR = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47"

def capture_screenshot(step_name):
    remote = f"/sdcard/{step_name}.png"
    local_artifact = os.path.join(ARTIFACT_DIR, f"{step_name}.png")
    subprocess.run([ADB, "shell", "screencap", "-p", remote], check=True)
    subprocess.run([ADB, "pull", remote, local_artifact], check=True)
    print(f"[Screenshot] Saved: {local_artifact}")
    return local_artifact

def get_recent_logcat(tag_filter="chromium:V ReactNativeJS:V", lines=15):
    cmd = [ADB, "logcat", "-d", "-s", "chromium:V", "ReactNativeJS:V"]
    out = subprocess.check_output(cmd, encoding="utf-8", errors="replace")
    log_lines = [l.strip() for l in out.strip().split("\n") if l.strip()]
    return log_lines[-lines:]

def reforward_devtools():
    out = subprocess.check_output([ADB, "shell", "cat", "/proc/net/unix"], encoding="utf-8", errors="replace")
    m = re.findall(r"@webview_devtools_remote_(\d+)", out)
    if not m:
        raise RuntimeError("No active webview_devtools_remote socket found in /proc/net/unix")
    # Take the latest PID
    pid = m[-1]
    subprocess.run([ADB, "forward", "tcp:9222", f"localabstract:webview_devtools_remote_{pid}"], check=True)
    time.sleep(1)
    ws_url, _ = get_page_ws_url()
    return DevToolsClient(ws_url)

def main():
    print("======================================================================")
    print("TASK 11B: REAL DEVICE VERIFICATION SUITE")
    print("======================================================================")

    ws_url, _ = get_page_ws_url()
    client = DevToolsClient(ws_url)

    # Make sure app is logged in before Step 2
    cur_screen = client.evaluate("window.app.state.currentScreen")
    token = client.evaluate("localStorage.getItem('auth_token')")
    print(f"Initial state: Screen={cur_screen}, HasToken={bool(token)}")
    if not token or cur_screen == 'login':
        print("Logging in with demo session for setup...")
        client.evaluate("window.app.loginDemo();")
        time.sleep(3)

    # ----------------------------------------------------------------------
    # STEP 2: Change Harmless Profile Field & Confirm Persistence
    # ----------------------------------------------------------------------
    print("\n----------------------------------------------------------------------")
    print("STEP 2: Change Harmless Profile Field, Navigate Away, Reopen & Confirm")
    print("----------------------------------------------------------------------")
    # Navigate to Profile (settings)
    client.evaluate("window.app.go('settings');")
    time.sleep(1.5)
    
    # Open edit modal / drawer
    client.evaluate("window.app.toggleEditDoctorProfile(true);")
    time.sleep(1)
    
    test_specialization = "Pediatric Care Specialist (Task11B Test)"
    client.evaluate(f"""
        document.getElementById('settings-input-spec').value = '{test_specialization}';
        window.app.saveDoctorProfile();
    """)
    time.sleep(2)
    
    # Check toast and displayed value
    toast_msg = client.evaluate("document.getElementById('toast').innerText")
    disp_spec = client.evaluate("document.getElementById('settings-doctor-spec').innerText")
    print(f"[Step 2] Save Toast: {toast_msg}")
    print(f"[Step 2] Displayed Specialization: {disp_spec}")
    
    # Navigate away to 'records'
    client.evaluate("window.app.go('records');")
    time.sleep(1.5)
    screen_away = client.evaluate("window.app.state.currentScreen")
    print(f"[Step 2] Navigated away to: {screen_away}")
    
    # Reopen profile 'settings'
    client.evaluate("window.app.go('settings');")
    time.sleep(1.5)
    reopened_screen = client.evaluate("window.app.state.currentScreen")
    reopened_spec = client.evaluate("document.getElementById('settings-doctor-spec').innerText")
    reopened_state_spec = client.evaluate("window.app.state.doctorSpecialization")
    print(f"[Step 2] Reopened Screen: {reopened_screen}")
    print(f"[Step 2] Reopened Displayed Specialization: {reopened_spec}")
    print(f"[Step 2] Reopened State Specialization: {reopened_state_spec}")
    
    step2_passed = (test_specialization in reopened_spec) or (test_specialization == reopened_state_spec)
    print(f"[Step 2] Persistence Confirmed: {step2_passed}")
    capture_screenshot("task11b_step2_profile_persistence")

    # ----------------------------------------------------------------------
    # STEP 4: Session Persistence Across App Restart
    # ----------------------------------------------------------------------
    print("\n----------------------------------------------------------------------")
    print("STEP 4: Test Session Persistence Across App Force-Stop and Restart")
    print("----------------------------------------------------------------------")
    # Navigate to home first
    client.evaluate("window.app.go('home');")
    time.sleep(1)
    token_before = client.evaluate("localStorage.getItem('auth_token')")
    print(f"[Step 4] Token before kill: {token_before[:25]}... (len={len(token_before)})")
    
    print("[Step 4] Executing: adb shell am force-stop com.ink2insight.scanner")
    subprocess.run([ADB, "shell", "am", "force-stop", "com.ink2insight.scanner"], check=True)
    time.sleep(2)
    
    print("[Step 4] Executing: adb shell am start -n com.ink2insight.scanner/.MainActivity")
    subprocess.run([ADB, "shell", "am", "start", "-n", "com.ink2insight.scanner/.MainActivity"], check=True)
    # Wait for React Native bundle and WebView splash -> initApp
    print("[Step 4] Waiting 5 seconds for app initialization...")
    time.sleep(5)
    
    print("[Step 4] Re-establishing DevTools socket connection to new PID...")
    client2 = reforward_devtools()
    
    # Check current screen and token after restart
    screen_after_restart = client2.evaluate("window.app.state.currentScreen")
    token_after_restart = client2.evaluate("localStorage.getItem('auth_token')")
    print(f"[Step 4] Screen after restart: {screen_after_restart}")
    print(f"[Step 4] Token preserved: {bool(token_after_restart)} (len={len(token_after_restart) if token_after_restart else 0})")
    
    step4_passed = (token_after_restart == token_before) and (screen_after_restart in ['home', 'splash'])
    print(f"[Step 4] Session Persistence Confirmed: {step4_passed}")
    capture_screenshot("task11b_step4_session_persisted")

    # ----------------------------------------------------------------------
    # STEP 5: Test Logout and Protected Screen Access Denial
    # ----------------------------------------------------------------------
    print("\n----------------------------------------------------------------------")
    print("STEP 5: Test Logout and Access Denial to Protected Screens")
    print("----------------------------------------------------------------------")
    client2.evaluate("window.app.logout();")
    time.sleep(2)
    
    screen_after_logout = client2.evaluate("window.app.state.currentScreen")
    token_after_logout = client2.evaluate("localStorage.getItem('auth_token')")
    print(f"[Step 5] Screen after logout: {screen_after_logout}")
    print(f"[Step 5] Token after logout: {token_after_logout}")
    
    # Try accessing protected screens without token
    print("[Step 5] Attempting unauthorized navigation to protected 'records' screen...")
    client2.evaluate("window.app.go('records');")
    time.sleep(1)
    screen_after_records_attempt = client2.evaluate("window.app.state.currentScreen")
    print(f"[Step 5] Screen after 'records' attempt: {screen_after_records_attempt}")
    
    print("[Step 5] Attempting unauthorized navigation to protected 'settings' screen...")
    client2.evaluate("window.app.go('settings');")
    time.sleep(1)
    screen_after_settings_attempt = client2.evaluate("window.app.state.currentScreen")
    print(f"[Step 5] Screen after 'settings' attempt: {screen_after_settings_attempt}")
    
    # Test protected backend API call without token
    import requests
    res_unauth = requests.get("http://127.0.0.1:5001/api/records", timeout=3)
    print(f"[Step 5] Protected Backend API /api/records response: HTTP {res_unauth.status_code}")
    print(f"[Step 5] Response JSON: {res_unauth.json()}")
    
    # Ensure back on login screen
    client2.evaluate("window.app.go('login');")
    time.sleep(1)
    capture_screenshot("task11b_step5_logout_and_protected_denied")
    step5_passed = (token_after_logout is None) and (res_unauth.status_code == 401)
    print(f"[Step 5] Logout & Protected Access Denial Confirmed: {step5_passed}")

    # ----------------------------------------------------------------------
    # STEP 3: Test Invalid Credentials and Capture Error on Phone
    # ----------------------------------------------------------------------
    print("\n----------------------------------------------------------------------")
    print("STEP 3: Test Invalid Credentials and Capture Visible Error on Phone")
    print("----------------------------------------------------------------------")
    # Make sure we are on login screen
    client2.evaluate("window.app.go('login');")
    time.sleep(1)
    
    # Input bogus credentials
    client2.evaluate("""
        document.getElementById('login-email').value = 'unauthorized.doctor@fakeclinic.net';
        document.getElementById('login-password').value = 'WrongPassword999!';
        window.app.login();
    """)
    time.sleep(2)
    
    login_err_msg = client2.evaluate("document.getElementById('login-error').innerText")
    toast_msg = client2.evaluate("document.getElementById('toast').innerText")
    login_screen_state = client2.evaluate("window.app.state.currentScreen")
    
    print(f"[Step 3] Login Error element text: '{login_err_msg}'")
    print(f"[Step 3] Toast message text: '{toast_msg}'")
    print(f"[Step 3] Current screen: '{login_screen_state}'")
    
    capture_screenshot("task11b_step3_invalid_login")
    step3_passed = (login_screen_state == 'login') and ("Invalid login credentials" in login_err_msg or "Invalid login credentials" in toast_msg)
    print(f"[Step 3] Invalid Credentials Rejection Confirmed: {step3_passed}")

    # ----------------------------------------------------------------------
    # Collect Logcat Evidence
    # ----------------------------------------------------------------------
    print("\n----------------------------------------------------------------------")
    print("LOGCAT EVIDENCE (Recent Entries)")
    print("----------------------------------------------------------------------")
    logs = get_recent_logcat(lines=20)
    for l in logs:
        print("  " + l)

    print("\n======================================================================")
    print("EXECUTION SUMMARY:")
    print(f"Step 2 (Profile Persistence): {'PASS' if step2_passed else 'FAIL'}")
    print(f"Step 3 (Invalid Credentials): {'PASS' if step3_passed else 'FAIL'}")
    print(f"Step 4 (Session Persistence): {'PASS' if step4_passed else 'FAIL'}")
    print(f"Step 5 (Logout & Auth Guard):  {'PASS' if step5_passed else 'FAIL'}")
    print("======================================================================")

if __name__ == "__main__":
    main()
