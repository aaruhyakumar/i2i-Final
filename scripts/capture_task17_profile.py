import os
import sys
import time
import json
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

print("Navigating to records screen...")
eval_in_webview("window.app.go('records')")
time.sleep(1.5)

print("Opening review for rec_20261117134632_f32f41...")
eval_in_webview("window.app.openReview('rec_20261117134632_f32f41')")
time.sleep(1.5)

# Verify on review screen
s_res = eval_in_webview("window.app.state.currentScreen")
print("Current screen:", s_res.get('result', {}).get('result', {}).get('value'))

# Capture Personal tab
eval_in_webview("window.app.switchReviewTab('personal')")
time.sleep(0.5)
take_screenshot("task17_phone_personal_tab.png")

# Capture Health tab
eval_in_webview("window.app.switchReviewTab('health')")
time.sleep(0.5)
take_screenshot("task17_phone_health_tab.png")

# Read input fields on review screen live
fields_js = """
(() => {
    const ids = [
        'rev-name', 'rev-age', 'rev-gender', 'rev-blood-group', 'rev-dob',
        'rev-doc-id', 'rev-adm-id', 'rev-school', 'rev-mobile', 'rev-address',
        'rev-height', 'rev-weight', 'rev-bp', 'rev-pulse', 'rev-rbs', 'rev-waist', 'rev-atr',
        'rev-allergies', 'rev-surgery', 'rev-medications', 'rev-abdominal', 'rev-ent',
        'rev-fam-diabetes', 'rev-fam-hypertension', 'rev-fam-heart', 'rev-fam-thyroid', 'rev-fam-asthma',
        'rev-pallor', 'rev-icterus', 'rev-cyanosis', 'rev-edema', 'rev-clubbing', 'rev-lymph'
    ];
    const res = {};
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (el) res[id] = el.value;
    });
    return JSON.stringify(res);
})()
"""
f_res = eval_in_webview(fields_js)
vals = json.loads(f_res.get('result', {}).get('result', {}).get('value') or '{}')
print("\n--- LIVE REVIEW FIELDS IN PHONE UI ---")
for k, v in sorted(vals.items()):
    print(f"  {k}: {repr(v)}")
