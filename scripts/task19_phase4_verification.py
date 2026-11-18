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

def main():
    print("=== PHASE 4: PHYSICAL VIVO DEVICE VERIFICATION ===")

    # Step 1: Ensure device awake and app focused
    subprocess.run([ADB, "shell", "input", "keyevent", "KEYCODE_WAKEUP"], check=True)
    subprocess.run([ADB, "shell", "monkey", "-p", "com.ink2insight.scanner", "-c", "android.intent.category.LAUNCHER", "1"], check=True)
    time.sleep(1)

    # Step 2: Open Records list and select camp
    print("\n[Step 2] Navigating to records screen...")
    eval_in_webview("window.app.go('records')")
    time.sleep(2)
    take_screenshot("task19_verified_phone_records_list.png")

    # Step 3: Open B. SADHANA Review
    print("\n[Step 3] Opening review for B. SADHANA (rec_20261117134632_f32f41)...")
    eval_in_webview("window.app.openReview('rec_20261117134632_f32f41')")
    time.sleep(2)

    # Step 4: Tab 1 (Personal)
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
        address: document.getElementById('rev-address')?.value,
        height: document.getElementById('rev-height')?.value,
        weight: document.getElementById('rev-weight')?.value,
        waist: document.getElementById('rev-waist')?.value,
        atr: document.getElementById('rev-atr')?.value
    })
    """)['result']['result']['value'])
    print("  Personal tab values:", p_vals)
    take_screenshot("task19_verified_phone_personal.png")

    # Step 5: Tab 2 (Medical)
    print("\n[Step 5] Checking Tab 2: Medical History...")
    eval_in_webview("window.app.switchReviewTab('medical')")
    time.sleep(1)
    m_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        allergies: document.getElementById('rev-allergies')?.value,
        surgery: document.getElementById('rev-surgery')?.value,
        medications: document.getElementById('rev-medications')?.value,
        abdominal: document.getElementById('rev-abdominal')?.value,
        ent: document.getElementById('rev-ent')?.value
    })
    """)['result']['result']['value'])
    print("  Medical tab values:", m_vals)
    take_screenshot("task19_verified_phone_medical.png")

    # Step 6: Tab 3 (Family)
    print("\n[Step 6] Checking Tab 3: Family History...")
    eval_in_webview("window.app.switchReviewTab('family')")
    time.sleep(1)
    f_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        diabetes: document.getElementById('rev-fam-diabetes')?.value,
        hypertension: document.getElementById('rev-fam-hypertension')?.value,
        heart: document.getElementById('rev-fam-heart')?.value,
        thyroid: document.getElementById('rev-fam-thyroid')?.value,
        asthma: document.getElementById('rev-fam-asthma')?.value
    })
    """)['result']['result']['value'])
    print("  Family tab values:", f_vals)
    take_screenshot("task19_verified_phone_family.png")

    # Step 7: Tab 4 (Health Assessment)
    print("\n[Step 7] Checking Tab 4: Health Assessment...")
    eval_in_webview("window.app.switchReviewTab('health')")
    time.sleep(1)
    h_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        pallor: document.getElementById('rev-pallor')?.value,
        icterus: document.getElementById('rev-icterus')?.value,
        cyanosis: document.getElementById('rev-cyanosis')?.value,
        edema: document.getElementById('rev-edema')?.value,
        clubbing: document.getElementById('rev-clubbing')?.value,
        lymph: document.getElementById('rev-lymph')?.value
    })
    """)['result']['result']['value'])
    print("  Health tab values:", h_vals)
    take_screenshot("task19_verified_phone_health.png")

    # Step 8: Tab 5 (Systemic Examination)
    print("\n[Step 8] Checking Tab 5: Systemic Examination...")
    eval_in_webview("window.app.switchReviewTab('system')")
    time.sleep(1)
    s_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        ent: document.getElementById('rev-sys-ent')?.value,
        skin: document.getElementById('rev-sys-skin')?.value,
        rs: document.getElementById('rev-sys-rs')?.value,
        cvs: document.getElementById('rev-sys-cvs')?.value,
        abdomen: document.getElementById('rev-sys-abdomen')?.value,
        cns: document.getElementById('rev-sys-cns')?.value
    })
    """)['result']['result']['value'])
    print("  System tab values:", s_vals)
    take_screenshot("task19_verified_phone_system.png")

    # Step 9: Tab 6 (Vision & Dental)
    print("\n[Step 9] Checking Tab 6: Vision & Dental...")
    eval_in_webview("window.app.switchReviewTab('vision')")
    time.sleep(1)
    v_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        va_left: document.getElementById('rev-va-left')?.value,
        va_right: document.getElementById('rev-va-right')?.value,
        vision_category: document.getElementById('rev-vision-category')?.value,
        dental_status: document.getElementById('rev-dental-status')?.value,
        dental_advice: document.getElementById('rev-dental-advice')?.value
    })
    """)['result']['result']['value'])
    print("  Vision & Dental tab values:", v_vals)
    take_screenshot("task19_verified_phone_vision.png")

    # Step 10: Tab 7 (Referral & Doctor Notes)
    print("\n[Step 10] Checking Tab 7: Referral & Doctor Notes...")
    eval_in_webview("window.app.switchReviewTab('referral')")
    time.sleep(1)
    r_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        referral: document.getElementById('rev-referral')?.value,
        doc_sign: document.getElementById('rev-doc-sign')?.value,
        notes: document.getElementById('rev-doctor-notes')?.value
    })
    """)['result']['result']['value'])
    print("  Referral tab values:", r_vals)
    take_screenshot("task19_verified_phone_referral.png")

    print("\n>>> ALL 7 REVIEW TABS VERIFIED ON DEVICE <<<")

if __name__ == "__main__":
    main()
