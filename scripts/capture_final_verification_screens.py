import os
import sys
import time
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

print("Navigating to review screen for rec_20261117134632_f32f41...")
eval_in_webview("window.app.openReview('rec_20261117134632_f32f41')")
time.sleep(1.5)

# Personal Tab
eval_in_webview("window.app.switchReviewTab('personal')")
time.sleep(1)
take_screenshot("task17_verified_phone_personal.png")

# Health Tab
eval_in_webview("window.app.switchReviewTab('health')")
time.sleep(1)
take_screenshot("task17_verified_phone_health.png")

# Navigate back to records
eval_in_webview("window.app.go('records')")
time.sleep(1.5)
take_screenshot("task17_verified_phone_records_list.png")

print("All screenshots successfully captured!")
