import os
import sys
import time
import json
import subprocess
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from backend.services.supabase_service import SupabaseService
from scripts.cdp_client import eval_in_webview

ADB = r"C:\Users\ADMIN\AppData\Local\Android\Sdk\platform-tools\adb.exe"
ARTIFACT_DIR = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47"

def take_screenshot(filename: str):
    dev_path = f"/sdcard/{filename}"
    local_path = os.path.join(ARTIFACT_DIR, filename)
    subprocess.run([ADB, "shell", "screencap", "-p", dev_path], check=True)
    subprocess.run([ADB, "pull", dev_path, local_path], check=True)
    print(f"[Screenshot] Captured: {local_path}")
    return local_path

print("=== PHASE 4: COLD RESTART PERSISTENCE VERIFICATION ===")

# 1. Force stop the application
print("\n[Step 1] Force stopping com.ink2insight.scanner...")
subprocess.run([ADB, "shell", "am", "force-stop", "com.ink2insight.scanner"], check=True)
time.sleep(2)
print("Application force-stopped successfully.")

# 2. Relaunch application on Vivo phone
print("\n[Step 2] Relaunching application on Vivo I2219...")
subprocess.run([ADB, "shell", "monkey", "-p", "com.ink2insight.scanner", "-c", "android.intent.category.LAUNCHER", "1"], check=True)
time.sleep(4)

# 3. Detect new webview socket and forward port 9222
print("\n[Step 3] Reconnecting CDP port forward to new process socket...")
unix_out = subprocess.check_output([ADB, "shell", "cat", "/proc/net/unix"]).decode('utf-8', errors='ignore')
socket_name = None
for line in unix_out.splitlines():
    if "webview_devtools_remote" in line:
        parts = line.strip().split()
        socket_name = parts[-1].lstrip('@')
        break

assert socket_name, "No webview_devtools_remote socket found after relaunch!"
print(f"Found socket: {socket_name}")
subprocess.run([ADB, "forward", "tcp:9222", f"localabstract:{socket_name}"], check=True)
subprocess.run([ADB, "reverse", "tcp:5001", "tcp:5001"], check=True)
time.sleep(1)

# 4. Check initial screen after restart
s_res = eval_in_webview("JSON.stringify({ screen: window.app?.state?.currentScreen, camp: window.app?.state?.selectedCampId, doctor: window.app?.state?.doctorName })")
print("Relaunched App State:", s_res.get('result', {}).get('result', {}).get('value'))

# 5. Open review for existing synthetic patient
print("\n[Step 4] Opening review for B. SADHANA (rec_20261117134632_f32f41)...")
eval_in_webview("window.app.openReview('rec_20261117134632_f32f41')")
time.sleep(2)

# 6. Read form values
read_js = """
JSON.stringify({
    screen: window.app.state.currentScreen,
    name: document.getElementById('rev-name')?.value,
    gender: document.getElementById('rev-gender')?.value,
    dob: document.getElementById('rev-dob')?.value,
    blood_group: document.getElementById('rev-blood-group')?.value,
    height: document.getElementById('rev-height')?.value,
    weight: document.getElementById('rev-weight')?.value,
    bp: document.getElementById('rev-bp')?.value,
    pulse: document.getElementById('rev-pulse')?.value
})
"""
restarted_vals = json.loads(eval_in_webview(read_js)['result']['result']['value'])
print("\n--- UI VALUES AFTER COLD RESTART ---")
for k, v in restarted_vals.items():
    print(f"  {k}: {repr(v)}")

take_screenshot("task18_step9_cold_restart_personal.png")

# 7. Compare with direct Supabase read-back
print("\n[Step 5] Comparing with Supabase read-back...")
sb = SupabaseService()
sb_rec = sb.get_record_by_id("rec_20261117134632_f32f41")
print("Supabase person_name:", sb_rec.get("person_name"), "| UI:", restarted_vals["name"])
print("Supabase gender:", sb_rec.get("gender"), "| UI:", restarted_vals["gender"])
print("Supabase dob:", sb_rec.get("dob"), "| UI:", restarted_vals["dob"])
print("Supabase blood_group:", sb_rec.get("blood_group"), "| UI:", restarted_vals["blood_group"])
print("Supabase height:", sb_rec.get("height"), "| UI:", restarted_vals["height"])
print("Supabase weight:", sb_rec.get("weight"), "| UI:", restarted_vals["weight"])

assert restarted_vals["name"] == sb_rec.get("person_name")
assert restarted_vals["gender"] == sb_rec.get("gender")
assert restarted_vals["dob"] == sb_rec.get("dob")
assert restarted_vals["blood_group"] == sb_rec.get("blood_group")
assert restarted_vals["height"] == sb_rec.get("height")
assert restarted_vals["weight"] == sb_rec.get("weight")

print("\n[PHASE 4 COMPLETE] Cold restart persistence 100% verified!")
