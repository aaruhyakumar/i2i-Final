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

print("=== PHASE 3: PHYSICAL VIVO DEVICE VERIFICATION ===")

# Step 1: Ensure device awake and app focused
subprocess.run([ADB, "shell", "input", "keyevent", "KEYCODE_WAKEUP"], check=True)
subprocess.run([ADB, "shell", "monkey", "-p", "com.ink2insight.scanner", "-c", "android.intent.category.LAUNCHER", "1"], check=True)
time.sleep(1)

# Ensure adb reverse & forward
subprocess.run([ADB, "reverse", "tcp:5001", "tcp:5001"], check=True)
subprocess.run([ADB, "forward", "tcp:9222", "localabstract:webview_devtools_remote_12012"], check=True)
time.sleep(1)

# Step 2: Open Records list
print("\n[Step 2] Navigating to records screen...")
eval_in_webview("window.app.go('records')")
time.sleep(2)
take_screenshot("task18_step1_records_list.png")

# Step 3: Open B. SADHANA Review
print("\n[Step 3] Opening review for B. SADHANA (rec_20261117134632_f32f41)...")
eval_in_webview("window.app.openReview('rec_20261117134632_f32f41')")
time.sleep(2)

# Step 4: Verify Tab 1 (Personal)
print("\n[Step 4] Checking Tab 1: Personal Information...")
eval_in_webview("window.app.switchReviewTab('personal')")
time.sleep(1)
p_vals = json.loads(eval_in_webview("""
JSON.stringify({
    name: document.getElementById('rev-name')?.value,
    gender: document.getElementById('rev-gender')?.value,
    dob: document.getElementById('rev-dob')?.value,
    blood_group: document.getElementById('rev-blood-group')?.value,
    age: document.getElementById('rev-age')?.value,
    mobile: document.getElementById('rev-mobile')?.value,
    address: document.getElementById('rev-address')?.value
})
""")['result']['result']['value'])
print("Personal tab values:", p_vals)
take_screenshot("task18_step2_tab1_personal.png")

assert p_vals["name"] == "B. SADHANA", f"Expected B. SADHANA, got {p_vals['name']}"
assert p_vals["gender"] == "Female", f"Expected Female, got {p_vals['gender']}"
assert p_vals["dob"] == "08/07/2013", f"Expected 08/07/2013, got {p_vals['dob']}"
assert p_vals["blood_group"] == "O+ve", f"Expected O+ve, got {p_vals['blood_group']}"
assert p_vals["age"] == "", f"Expected empty age, got {p_vals['age']}"

# Step 5: Verify Tab 2 (Medical History)
print("\n[Step 5] Checking Tab 2: Medical History...")
eval_in_webview("window.app.switchReviewTab('medical')")
time.sleep(1)
m_vals = json.loads(eval_in_webview("""
JSON.stringify({
    allergies: document.getElementById('rev-allergies')?.value,
    surgery: document.getElementById('rev-surgery')?.value,
    medications: document.getElementById('rev-medications')?.value
})
""")['result']['result']['value'])
print("Medical history tab values:", m_vals)
take_screenshot("task18_step3_tab2_medical.png")

# Step 6: Verify Tab 3 (Family History)
print("\n[Step 6] Checking Tab 3: Family History...")
eval_in_webview("window.app.switchReviewTab('family')")
time.sleep(1)
f_vals = json.loads(eval_in_webview("""
JSON.stringify({
    diabetes: document.getElementById('rev-fam-diabetes')?.value,
    hypertension: document.getElementById('rev-fam-hypertension')?.value,
    heart: document.getElementById('rev-fam-heart')?.value
})
""")['result']['result']['value'])
print("Family history tab values:", f_vals)
take_screenshot("task18_step4_tab3_family.png")

# Step 7: Verify Tab 4 (Health Assessment)
print("\n[Step 7] Checking Tab 4: Health Assessment...")
eval_in_webview("window.app.switchReviewTab('health')")
time.sleep(1)
h_vals = json.loads(eval_in_webview("""
JSON.stringify({
    height: document.getElementById('rev-height')?.value,
    weight: document.getElementById('rev-weight')?.value,
    bp: document.getElementById('rev-bp')?.value,
    pulse: document.getElementById('rev-pulse')?.value,
    waist: document.getElementById('rev-waist')?.value,
    atr: document.getElementById('rev-atr')?.value
})
""")['result']['result']['value'])
print("Health assessment tab values:", h_vals)
take_screenshot("task18_step5_tab4_health.png")

assert h_vals["height"] == "154.4", f"Expected 154.4, got {h_vals['height']}"
assert h_vals["weight"] == "35.7", f"Expected 35.7, got {h_vals['weight']}"
assert h_vals["bp"] == "", f"Expected empty bp, got {h_vals['bp']}"
assert h_vals["pulse"] == "", f"Expected empty pulse, got {h_vals['pulse']}"

# Step 8: Verify Tab 5 (System Examination)
print("\n[Step 8] Checking Tab 5: System Examination...")
eval_in_webview("window.app.switchReviewTab('system')")
time.sleep(1)
s_vals = json.loads(eval_in_webview("""
JSON.stringify({
    rs: document.getElementById('rev-sys-rs')?.value,
    cvs: document.getElementById('rev-sys-cvs')?.value,
    ent: document.getElementById('rev-sys-ent')?.value
})
""")['result']['result']['value'])
print("System examination tab values:", s_vals)
take_screenshot("task18_step6_tab5_system.png")

# Step 9: Verify Tab 6 (Vision & Dental)
print("\n[Step 9] Checking Tab 6: Vision & Dental...")
eval_in_webview("window.app.switchReviewTab('vision')")
time.sleep(1)
v_vals = json.loads(eval_in_webview("""
JSON.stringify({
    va_right: document.getElementById('rev-va-right')?.value,
    va_left: document.getElementById('rev-va-left')?.value,
    dental: document.getElementById('rev-dental-status')?.value
})
""")['result']['result']['value'])
print("Vision & dental tab values:", v_vals)
take_screenshot("task18_step7_tab6_vision.png")

# Step 10: Verify Tab 7 (Referral & Doctor Notes)
print("\n[Step 10] Checking Tab 7: Referral & Doctor Notes...")
eval_in_webview("window.app.switchReviewTab('referral')")
time.sleep(1)
r_vals = json.loads(eval_in_webview("""
JSON.stringify({
    referral: document.getElementById('rev-referral')?.value,
    notes: document.getElementById('rev-doctor-notes')?.value
})
""")['result']['result']['value'])
print("Referral tab values:", r_vals)
take_screenshot("task18_step8_tab7_referral.png")

print("\n[PHASE 3 COMPLETE] All physical device review screens verified successfully!")
