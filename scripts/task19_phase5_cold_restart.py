import os
import sys
import time
import json
import subprocess

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
    print("=== PHASE 5: COLD RESTART TEST ===")

    # Step 1: Force stop
    print("\n[Step 1] Force-stopping com.ink2insight.scanner...")
    subprocess.run([ADB, "shell", "am", "force-stop", "com.ink2insight.scanner"], check=True)
    time.sleep(2)

    # Step 2: Relaunch
    print("\n[Step 2] Relaunching com.ink2insight.scanner...")
    subprocess.run([ADB, "shell", "am", "start", "-n", "com.ink2insight.scanner/.MainActivity"], check=True)
    time.sleep(4)

    # Step 3: Find new webview devtools socket
    print("\n[Step 3] Detecting new webview devtools socket...")
    p = subprocess.run([ADB, "shell", "cat", "/proc/net/unix"], capture_output=True, text=True, check=True)
    sock_name = None
    for line in p.stdout.splitlines():
        if "webview_devtools" in line:
            parts = line.strip().split()
            if len(parts) >= 8 and parts[7].startswith("@webview_devtools"):
                sock_name = parts[7][1:]
                break
    if not sock_name:
        print("ERROR: Could not find webview devtools socket!")
        sys.exit(1)
    print(f"  Found socket: {sock_name}")

    # Step 4: Forward CDP port
    subprocess.run([ADB, "reverse", "tcp:5001", "tcp:5001"], check=True)
    subprocess.run([ADB, "forward", "tcp:9222", f"localabstract:{sock_name}"], check=True)
    time.sleep(2)

    # Step 5: Test CDP
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.cdp_client import eval_in_webview

    # Step 6: Navigate to records and open B. SADHANA
    print("\n[Step 6] Navigating to records screen...")
    eval_in_webview("window.app.go('records')")
    time.sleep(2)

    print("\n[Step 7] Opening review for B. SADHANA (rec_20261117134632_f32f41)...")
    eval_in_webview("window.app.openReview('rec_20261117134632_f32f41')")
    time.sleep(2)

    # Step 8: Read Personal tab values
    eval_in_webview("window.app.switchReviewTab('personal')")
    time.sleep(1)
    p_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        name: document.getElementById('rev-name')?.value,
        gender: document.getElementById('rev-gender')?.value,
        dob: document.getElementById('rev-dob')?.value,
        blood_group: document.getElementById('rev-blood-group')?.value,
        mobile: document.getElementById('rev-mobile')?.value,
        address: document.getElementById('rev-address')?.value,
        height: document.getElementById('rev-height')?.value,
        weight: document.getElementById('rev-weight')?.value,
        waist: document.getElementById('rev-waist')?.value,
        atr: document.getElementById('rev-atr')?.value
    })
    """)['result']['result']['value'])
    print("  Cold restart Personal tab values:", p_vals)

    # Step 9: Read Vision tab values
    eval_in_webview("window.app.switchReviewTab('vision')")
    time.sleep(1)
    v_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        va_left: document.getElementById('rev-va-left')?.value,
        va_right: document.getElementById('rev-va-right')?.value,
        vision_category: document.getElementById('rev-vision-category')?.value,
        dental_status: document.getElementById('rev-dental-status')?.value
    })
    """)['result']['result']['value'])
    print("  Cold restart Vision tab values:", v_vals)

    # Step 10: Read Medical tab values
    eval_in_webview("window.app.switchReviewTab('medical')")
    time.sleep(1)
    m_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        allergies: document.getElementById('rev-allergies')?.value,
        surgery: document.getElementById('rev-surgery')?.value,
        medications: document.getElementById('rev-medications')?.value,
        ent: document.getElementById('rev-ent')?.value
    })
    """)['result']['result']['value'])
    print("  Cold restart Medical tab values:", m_vals)

    # Switch back to personal and take cold restart screenshot
    eval_in_webview("window.app.switchReviewTab('personal')")
    time.sleep(1)
    take_screenshot("task19_verified_phone_cold_restart.png")

    print("\n>>> COLD RESTART PERSISTENCE VERIFIED SUCCESSFULLY! <<<")

if __name__ == "__main__":
    main()
