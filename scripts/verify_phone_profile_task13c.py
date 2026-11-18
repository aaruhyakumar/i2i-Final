import sys, os, time
import subprocess
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

ADB = os.path.expandvars(r"$LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe")
ARTIFACT_DIR = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47"

# 1. Connect DevTools
ws_url, _ = get_page_ws_url()
client = DevToolsClient(ws_url)

# Close any open modal on device
client.evaluate("""
    const ov = document.getElementById('ov');
    if (ov) ov.classList.remove('on');
    if (window.app.closeNewCampModal) window.app.closeNewCampModal();
""")
time.sleep(1)

# Navigate to settings/profile and trigger renderSettings() to fetch fresh profile from backend
client.evaluate("""
    window.app.go('settings');
    if (window.app.renderSettings) window.app.renderSettings();
""")
time.sleep(2)

# Check rendered values
name = client.evaluate("document.getElementById('settings-doctor-name')?.innerText")
spec = client.evaluate("document.getElementById('settings-doctor-spec')?.innerText")
comm = client.evaluate("document.getElementById('settings-community-name')?.innerText || document.querySelector('.badge-primary')?.innerText")
screen = client.evaluate("window.app.state.currentScreen")

print(f"Device Screen: {screen}")
print(f"Doctor Name on phone: {name}")
print(f"Specialization on phone: {spec}")

# Capture clean screenshot
remote = "/sdcard/task13c_phone_profile_verified.png"
local = os.path.join(ARTIFACT_DIR, "task13c_phone_profile_verified.png")
subprocess.run([ADB, "shell", "screencap", "-p", remote], check=True)
subprocess.run([ADB, "pull", remote, local], check=True)
print(f"Saved: {local}")
