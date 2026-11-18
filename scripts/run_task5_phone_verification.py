import os
import sys
import time
import json
import sqlite3
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.cdp_client import eval_in_webview

ADB = r"C:\Users\ADMIN\AppData\Local\Android\Sdk\platform-tools\adb.exe"
ARTIFACT_DIR = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47"

def take_screenshot(filename: str) -> str:
    dev_path = f"/sdcard/{filename}"
    local_path = os.path.join(ARTIFACT_DIR, filename)
    subprocess.run([ADB, "shell", "screencap", "-p", dev_path], check=True)
    subprocess.run([ADB, "pull", dev_path, local_path], check=True)
    print(f"[Screenshot] Saved: {local_path}")
    return local_path

def main():
    print("=== TASK 5 PHYSICAL MOBILE DEVICE VERIFICATION (Vivo I2219) ===")

    # 1. Reload the page to load fresh frontend/index.html, styles.css, app.js
    print("[1/6] Reloading WebView to load updated assets...")
    eval_in_webview("window.location.reload()")
    time.sleep(3.0)

    # 2. Check login state & authenticate if needed
    print("[2/6] Ensuring doctor session...")
    eval_in_webview("""
        if (!window.app.state.doctor) {
            window.app.state.doctor = {
                id: 'doc_priya_nair',
                name: 'Dr. Priya Nair',
                email: 'doctor@test.com',
                role: 'doctor'
            };
            window.app.state.token = 'synthetic_test_token';
            localStorage.setItem('i2i_token', 'synthetic_test_token');
        }
        window.app.go('home');
    """)
    time.sleep(1.5)
    take_screenshot("task5_phone_stage1_home_dashboard.png")

    # 3. Test Pairs Screen Retake Controls
    print("[3/6] Navigating to Pairs screen to verify Retake controls...")
    eval_in_webview("""
        window.app.state.currentPair = {
            page1: {
                dataUrl: 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600" style="background:%23EFF6FF"><text x="50" y="80" fill="%231E40AF" font-size="20" font-family="sans-serif">Paediatric History (Page 1)</text><text x="50" y="120" fill="%234B5563" font-size="14">ASF-D&amp;A-PCS-9.3</text></svg>',
                template_id: 'tpl_paediatric_history_v9_3',
                detected_form: 'Paediatric History',
                confidence: 0.98
            },
            page2: {
                dataUrl: 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600" style="background:%23F0FDF4"><text x="50" y="80" fill="%23166534" font-size="20" font-family="sans-serif">Paediatric Exam (Page 2)</text><text x="50" y="120" fill="%234B5563" font-size="14">ASF-D&amp;A-PCS-9.7</text></svg>',
                template_id: 'tpl_paediatric_examination_v9_7',
                detected_form: 'Paediatric Examination',
                confidence: 0.99
            }
        };
        window.app.go('pairs');
        if (typeof window.app.renderPairsScreen === 'function') {
            window.app.renderPairsScreen();
        }
    """)
    time.sleep(1.5)
    take_screenshot("task5_phone_stage2_pairs_retake_fixed.png")

    # 4. Test Review Screen Approval Wording
    print("[4/6] Navigating to Review screen to verify 'Approve & Finalize Record'...")
    eval_in_webview("""
        (async () => {
            const records = await window.app.api.getRecords();
            const rec = records.find(r => r.id === 'rec_task4_paediatric_audit') || records[0];
            if (rec) {
                await window.app.openRecord(rec.id);
            } else {
                window.app.go('review');
            }
        })();
    """)
    time.sleep(2.0)
    # Scroll down to make the approval button visible in screenshot
    eval_in_webview("""
        const reviewScr = document.querySelector('#review .body');
        if (reviewScr) reviewScr.scrollTop = reviewScr.scrollHeight;
    """)
    time.sleep(1.0)
    take_screenshot("task5_phone_stage3_review_approval_wording.png")

    # 5. Test Records Screen (No flicker / white flash, clean bottom nav)
    print("[5/6] Navigating to Records screen to verify smooth rendering...")
    eval_in_webview("window.app.go('records')")
    time.sleep(1.5)
    take_screenshot("task5_phone_stage4_records_no_flicker.png")

    # 6. Test Export Screen (Single primary button, scrollable sheet tabs, no duplicate nav)
    print("[6/6] Navigating to Export screen to verify consolidated button & tab bar...")
    eval_in_webview("window.app.go('export')")
    time.sleep(1.5)
    take_screenshot("task5_phone_stage5_export_single_button_tabs.png")

    print("\n=== PHYSICAL DEVICE VERIFICATION COMPLETE ===")

if __name__ == "__main__":
    main()
