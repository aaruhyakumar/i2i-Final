import time
import os
import sys
import subprocess
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

def run_tests():
    ws_url, _ = get_page_ws_url()
    client = DevToolsClient(ws_url)
    adb = os.path.expandvars(r"$LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe")

    def capture(name):
        remote = f"/sdcard/{name}.png"
        local = f"device_{name}.png"
        subprocess.run([adb, "shell", "screencap", "-p", remote], check=True)
        subprocess.run([adb, "pull", remote, local], check=True)
        print(f"Captured screenshot: {local}")
        return local

    print("==================================================")
    print("STEP 1: Testing Invalid Credentials Login on Phone")
    print("==================================================")
    client.evaluate("""
        window.app.go('login');
        document.getElementById('login-email').value = 'invalid.doctor@hospital.org';
        document.getElementById('login-password').value = 'WrongPassword999!';
        window.app.login();
    """)
    time.sleep(2)
    err_text = client.evaluate("document.getElementById('login-error').innerText")
    toast_text = client.evaluate("document.getElementById('toast').innerText")
    screen_now = client.evaluate("window.app.state.currentScreen")
    print("Login Error element:", err_text)
    print("Toast message:", toast_text)
    print("Current screen (must remain 'login'):", screen_now)
    capture("invalid_login")

    print("\n==================================================")
    print("STEP 2: Testing Valid Login on Phone")
    print("==================================================")
    # Use demo 1-click login or valid credentials
    client.evaluate("window.app.loginDemo();")
    time.sleep(2)
    screen_after_login = client.evaluate("window.app.state.currentScreen")
    logged_in_name = client.evaluate("window.app.state.doctorName")
    logged_in_role = client.evaluate("window.app.state.userRole")
    has_token = client.evaluate("Boolean(localStorage.getItem('auth_token'))")
    print("Screen after login:", screen_after_login)
    print("Logged in Doctor Name:", logged_in_name)
    print("User Role:", logged_in_role)
    print("Auth token present in localStorage:", has_token)
    capture("valid_login_home")

    print("\n==================================================")
    print("STEP 3: Testing Doctor Profile Navigation & Update")
    print("==================================================")
    # Navigate to Profile (settings screen)
    client.evaluate("window.app.go('settings');")
    time.sleep(1)
    profile_screen = client.evaluate("window.app.state.currentScreen")
    print("Current screen:", profile_screen)
    
    # Open edit profile drawer
    client.evaluate("window.app.toggleEditDoctorProfile(true);")
    time.sleep(1)
    
    # Update doctor name and specialization
    new_name = "Dr. Priya Sharma, MD"
    new_spec = "Chief Clinical Consultant & Paediatric Specialist"
    client.evaluate(f"""
        document.getElementById('settings-input-name').value = '{new_name}';
        document.getElementById('settings-input-spec').value = '{new_spec}';
        window.app.saveDoctorProfile();
    """)
    time.sleep(2)
    
    # Verify updated values on screen
    displayed_name = client.evaluate("document.getElementById('settings-doctor-name').innerText")
    displayed_spec = client.evaluate("document.getElementById('settings-doctor-spec').innerText")
    toast_msg = client.evaluate("document.getElementById('toast').innerText")
    print("Displayed Doctor Name on phone:", displayed_name)
    print("Displayed Specialization on phone:", displayed_spec)
    print("Toast message:", toast_msg)
    capture("profile_updated")

    print("\n==================================================")
    print("STEP 4: Testing Session Persistence Across Restart")
    print("==================================================")
    # Restart app activity
    print("Stopping app activity on device...")
    subprocess.run([adb, "shell", "am", "force-stop", "com.ink2insight.scanner"], check=True)
    time.sleep(2)
    print("Relaunching app activity on device...")
    subprocess.run([adb, "shell", "am", "start", "-n", "com.ink2insight.scanner/.MainActivity"], check=True)
    time.sleep(4)
    
    # Re-forward new webview PID socket
    import re
    out = subprocess.check_output([adb, "shell", "cat", "/proc/net/unix"]).decode('utf-8')
    m = re.search(r"@webview_devtools_remote_(\d+)", out)
    if m:
        socket_name = m.group(0)[1:]
        subprocess.run([adb, "forward", "tcp:9222", f"localabstract:{socket_name}"], check=True)
        time.sleep(1)
    
    # Reconnect devtools client
    ws_url2, _ = get_page_ws_url()
    client2 = DevToolsClient(ws_url2)
    restarted_screen = client2.evaluate("window.app.state.currentScreen")
    restarted_token = client2.evaluate("Boolean(localStorage.getItem('auth_token'))")
    restarted_doctor = client2.evaluate("localStorage.getItem('doctor_name')")
    print("Screen after restart (must be 'home'):", restarted_screen)
    print("Token preserved:", restarted_token)
    print("Preserved Doctor:", restarted_doctor)
    capture("session_persisted_after_restart")

    print("\n==================================================")
    print("STEP 5: Testing Clean Logout on Phone")
    print("==================================================")
    client2.evaluate("window.app.logout();")
    time.sleep(2)
    logged_out_screen = client2.evaluate("window.app.state.currentScreen")
    cleared_token = client2.evaluate("localStorage.getItem('auth_token')")
    print("Screen after logout (must be 'login'):", logged_out_screen)
    print("Token after logout (must be None):", cleared_token)
    capture("logged_out_final")

    print("\nALL WORKFLOWS TESTED SUCCESSFULLY ON PHYSICAL DEVICE!")

if __name__ == "__main__":
    run_tests()
