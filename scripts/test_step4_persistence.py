import time
import os
import sys
import subprocess
import re

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

ADB = os.path.expandvars(r"$LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe")
ARTIFACT_DIR = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47"

def reforward():
    out = subprocess.check_output([ADB, "shell", "cat", "/proc/net/unix"], encoding="utf-8", errors="replace")
    m = re.findall(r"@webview_devtools_remote_(\d+)", out)
    pid = m[-1]
    subprocess.run([ADB, "forward", "tcp:9222", f"localabstract:webview_devtools_remote_{pid}"], check=True)
    time.sleep(1)
    ws_url, _ = get_page_ws_url()
    return DevToolsClient(ws_url)

def test_step4():
    print("Testing Step 4 (Session persistence across restart)...")
    ws_url, _ = get_page_ws_url()
    client = DevToolsClient(ws_url)
    
    # Ensure logged in first
    client.evaluate("window.app.loginDemo();")
    time.sleep(2)
    token = client.evaluate("localStorage.getItem('auth_token')")
    print(f"Logged in, token present: {bool(token)} (len={len(token) if token else 0})")
    
    # Force stop
    print("Force stopping app...")
    subprocess.run([ADB, "shell", "am", "force-stop", "com.ink2insight.scanner"], check=True)
    time.sleep(2)
    
    # Relaunch
    print("Relaunching app...")
    subprocess.run([ADB, "shell", "am", "start", "-n", "com.ink2insight.scanner/.MainActivity"], check=True)
    time.sleep(4)
    
    client2 = reforward()
    # Check intro / skip
    print("Dismissing intro if playing...")
    client2.evaluate("""
        if (window.Intro && window.Intro.skip) {
            window.Intro.skip();
        } else {
            const btn = document.querySelector('.intro-skip') || document.querySelector('button');
            if (btn) btn.click();
        }
    """)
    # Also tap skip button on physical screen
    subprocess.run([ADB, "shell", "input", "tap", "900", "180"])
    time.sleep(3)
    
    screen = client2.evaluate("window.app.state.currentScreen")
    preserved_token = client2.evaluate("localStorage.getItem('auth_token')")
    doctor_name = client2.evaluate("window.app.state.doctorName")
    
    print(f"Final screen after restart: {screen}")
    print(f"Token preserved: {preserved_token == token}")
    print(f"Doctor Name in state: {doctor_name}")
    
    # Capture final persisted screenshot
    remote = "/sdcard/task11b_step4_session_persisted.png"
    local = os.path.join(ARTIFACT_DIR, "task11b_step4_session_persisted.png")
    subprocess.run([ADB, "shell", "screencap", "-p", remote], check=True)
    subprocess.run([ADB, "pull", remote, local], check=True)
    print(f"Captured: {local}")

if __name__ == "__main__":
    test_step4()
