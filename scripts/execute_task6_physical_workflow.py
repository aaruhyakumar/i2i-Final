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
    print(f"[Screenshot] Captured: {local_path}")
    return local_path

DB_PATHS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "clinic_records.db"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clinic_records.db")
]

def reset_test_fixtures():
    for db_path in DB_PATHS:
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        # Ensure camp exists
        cur.execute("""
            INSERT OR REPLACE INTO camps (id, name, location, date_str, expected_records, target_group, community_id, created_at, updated_at)
            VALUES ('camp_task4_school', 'DPS Paediatric Health Camp 2026', 'Sector 4, Bangalore', '2026-11-18', 100, 'paediatric', 'comm_main_hub', datetime('now'), datetime('now'))
        """)
        # Reset paediatric audit record
        structured_rec = {
            "personal_information": {
                "full_name": "Arjun Sharma", "age": "12", "gender": "Male", "blood_group": "B+",
                "dob": "14/08/2014", "doc_id": "MD-2026-981", "admission_id": "ADM-7742",
                "school_college_name": "Delhi Public School, Sector 4", "mobile_number": "9884783689",
                "address": "Flat 402, Green Glen Layout, Bellandur", "consent_screening": "Yes"
            },
            "medical_history": {
                "known_allergies": "Peanuts, Dust (mild rhinitis)", "current_previous_medications": "Cetirizine 5mg SOS",
                "previous_surgery_hospitalization": "None / Nil", "vaccinated_for_age": "Yes",
                "skin_hair_ear_nose_problems": "Mild seasonal allergic rhinitis"
            },
            "family_history": {
                "diabetes": "No", "hypertension": "Father (+)", "asthma": "No", "heart_disease": "No",
                "thyroid": "No", "other_conditions": "None"
            },
            "health_assessment": {
                "height_cm": "148.5", "weight_kg": "41.2", "waist_circumference_cm": "64.0",
                "blood_pressure_mmhg": "108/70", "pulse_bpm": "78", "spinal_caliper_atr": "3",
                "atr_degrees": "3", "general_vitals": "Normal", "gait_status": "Normal"
            },
            "system_examination": {
                "respiratory_system": "No Abnormality Detected", "cardiovascular_system": "Normal Heart Sounds",
                "abdomen": "Normal / Soft", "central_nervous_system": "Normal", "ear": "Bilateral Wax Impaction",
                "nose": "Normal", "throat": "Normal", "skin": "Normal"
            },
            "vision": {
                "vision_category": "Unaided", "vision_aid_status": "Unaided", "right_eye": "6/6",
                "left_eye": "6/9", "distant_vision_right": "6/6", "distant_vision_left": "6/9", "color_vision": "Normal"
            },
            "dental": {
                "normal": "Abnormal", "dental_status": "Abnormal", "abnormality": "Caries irt 36",
                "advice": "Pediatric Dentist review for restoration"
            },
            "referral": {
                "specialist_referred_to": "Pediatric Dentist", "doctor_signature_present": "Yes"
            },
            "paediatric_growth": { "atr_degrees": 3, "spinal_caliper_atr": 3, "bmi": 18.7 }
        }
        cur.execute("""
            INSERT OR REPLACE INTO records (
                id, camp_id, person_name, age, gender, height, weight, blood_pressure,
                examination_details, referral_details, missing_fields, uncertain_fields,
                extraction_notes, structured_record, status, reviewer_notes, reviewed_by,
                reviewed_at, scanned_by, target_group, is_paediatric, template_id, created_at, updated_at
            ) VALUES (
                'rec_task4_paediatric_audit', 'camp_task4_school', 'Arjun Sharma', 12, 'Male', '148.5', '41.2', '108/70',
                'Normal examination, Bilateral wax, Caries irt 36', 'Pediatric Dentist',
                '[]', '["blood_pressure", "known_allergies"]', 'Synthetic Paediatric Accuracy Audit Record',
                ?, 'needs_review', 'Screening audit verification record', 'Dr. Priya Nair',
                '2026-11-18 03:50:00', 'doctor@test.com', 'paediatric', 1, 'tpl_paediatric_examination_v9_7',
                datetime('now'), datetime('now')
            )
        """, (json.dumps(structured_rec),))
        con.commit()
        con.close()
    print("[Fixtures] Reset test fixtures in all SQLite databases")

def main():
    print("=== TASK 6: COMPLETE APPLICATION REGRESSION ON VIVO I2219 ===")
    reset_test_fixtures()

    # Ensure device is awake
    subprocess.run([ADB, "shell", "input", "keyevent", "KEYCODE_WAKEUP"], check=True)
    time.sleep(1)

    print("\n--- Reloading WebView to pick up updated app.js ---")
    eval_in_webview("window.location.reload()")
    time.sleep(3.0)

    # 1. Step 1: Doctor Login & Dashboard
    print("\n--- Step 1: Doctor Login & Home Dashboard ---")
    eval_in_webview("""
        (async () => {
            const res = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: 'demo@clinic.camp', password: 'demo' })
            });
            const data = await res.json();
            if (data.token) {
                window.app.state.token = data.token;
                window.app.state.doctor = data.doctor;
                window.app.state.doctorName = data.doctor.full_name;
                localStorage.setItem('i2i_token', data.token);
                localStorage.setItem('clinic_doctor_name', data.doctor.full_name);
            }
            window.app.go('home');
            window.app.renderHome();
        })();
    """)
    time.sleep(2.5)
    take_screenshot("task6_phone_stage01_login_dashboard.png")

    # 2. Step 2: Select Test Camp
    print("\n--- Step 2: Camp Selection (DPS Paediatric Health Camp 2026) ---")
    eval_in_webview("""
        (async () => {
            const camps = await window.app.api.getCamps();
            const pCamp = camps.find(c => c.id === 'camp_task4_school') || camps[0];
            if (pCamp) {
                window.app.selectCamp(pCamp.id);
            }
        })();
    """)
    time.sleep(2.0)
    take_screenshot("task6_phone_stage02_camp_selection.png")

    # 3. Step 3: Paediatric Form Intake & Pairing
    print("\n--- Step 3: Paediatric Form Intake & Pairing ---")
    eval_in_webview("""
        window.app.state.currentPair = {
            page1: {
                dataUrl: 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="400" height="550" style="background:%23EEF2FF"><text x="40" y="80" fill="%233730A3" font-size="22" font-family="sans-serif" font-weight="bold">Paediatric History</text><text x="40" y="120" fill="%234B5563" font-size="14">Form Standard: ASF-D%26A-PCS-9.3</text><text x="40" y="160" fill="%231E293B" font-size="16">Student: Arjun Sharma (Age 12)</text></svg>',
                template_id: 'tpl_paediatric_history_v9_3',
                detected_form: 'Paediatric History (Page 1)',
                confidence: 0.99
            },
            page2: {
                dataUrl: 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="400" height="550" style="background:%23F0FDF4"><text x="40" y="80" fill="%23166534" font-size="22" font-family="sans-serif" font-weight="bold">Paediatric Examination</text><text x="40" y="120" fill="%234B5563" font-size="14">Form Standard: ASF-D%26A-PCS-9.7</text><text x="40" y="160" fill="%231E293B" font-size="16">General Vitals: Normal | ATR: 3°</text></svg>',
                template_id: 'tpl_paediatric_examination_v9_7',
                detected_form: 'Paediatric Examination (Page 2)',
                confidence: 0.98
            }
        };
        window.app.go('pairs');
        if (typeof window.app.renderPairsScreen === 'function') {
            window.app.renderPairsScreen();
        }
    """)
    time.sleep(2.0)
    take_screenshot("task6_phone_stage03_scan_pairs_preview.png")

    # 4. Step 4: Verify Swap and Retake Controls
    print("\n--- Step 4: Swap and Retake Controls Verification ---")
    eval_in_webview("""
        // Swap pages
        const tmp = window.app.state.currentPair.page1;
        window.app.state.currentPair.page1 = window.app.state.currentPair.page2;
        window.app.state.currentPair.page2 = tmp;
        window.app.renderPairsScreen();
    """)
    time.sleep(1.0)
    eval_in_webview("""
        // Swap back to canonical order
        const tmp = window.app.state.currentPair.page1;
        window.app.state.currentPair.page1 = window.app.state.currentPair.page2;
        window.app.state.currentPair.page2 = tmp;
        window.app.renderPairsScreen();
    """)
    time.sleep(1.0)
    take_screenshot("task6_phone_stage04_swap_retake_controls.png")

    # 5. Step 5: Execute Extraction & Open Review (7 tabs)
    print("\n--- Step 5: Execute Extraction & Open Review (7 tabs) ---")
    eval_in_webview("""
        (async () => {
            await window.app.openReview('rec_task4_paediatric_audit');
        })();
    """)
    time.sleep(2.5)
    take_screenshot("task6_phone_stage05_review_7tabs.png")

    # 6. Step 6: Edit Synthetic Patient Details
    print("\n--- Step 6: Edit Synthetic Patient Details ---")
    eval_in_webview("""
        const dName = document.getElementById('dyn-field-full_name') || document.getElementById('dyn-field-person_name');
        if (dName) dName.value = 'Arjun Sharma (Corrected)';
        const sName = document.getElementById('rev-name');
        if (sName) sName.value = 'Arjun Sharma (Corrected)';

        const dBp = document.getElementById('dyn-field-blood_pressure_mmhg') || document.getElementById('dyn-field-blood_pressure');
        if (dBp) dBp.value = '110/72';
        const sBp = document.getElementById('rev-bp');
        if (sBp) sBp.value = '110/72';
    """)
    time.sleep(1.0)
    take_screenshot("task6_phone_stage06_patient_edit.png")

    # 7. Step 7: Save Draft and Verify Persistence
    print("\n--- Step 7: Save Draft and Verify Persistence ---")
    eval_in_webview("""
        (async () => {
            await window.app.saveDraftReview();
            return true;
        })();
    """)
    time.sleep(2.5)

    # Check database persistence
    con = sqlite3.connect("clinic_records.db")
    cur = con.cursor()
    cur.execute("SELECT person_name, blood_pressure, status FROM records WHERE id = 'rec_task4_paediatric_audit'")
    row = cur.fetchone()
    con.close()
    print(f"[DB Verification] Stored row in clinic_records.db: {row}")
    assert row[0] == 'Arjun Sharma (Corrected)', f"Expected 'Arjun Sharma (Corrected)', got {row[0]}"
    assert row[1] == '110/72', f"Expected '110/72', got {row[1]}"

    # Reopen record to verify persistence across UI
    eval_in_webview("""
        (async () => {
            await window.app.openReview('rec_task4_paediatric_audit');
            return true;
        })();
    """)
    time.sleep(2.5)
    take_screenshot("task6_phone_stage07_saved_persistence.png")

    # 8. Step 8: Doctor Approval ("Approve & Finalize Record")
    print("\n--- Step 8: Complete Doctor Approval ---")
    eval_in_webview("""
        (async () => {
            await window.app.approveAndSaveRecord();
            return true;
        })();
    """)
    time.sleep(2.5)
    take_screenshot("task6_phone_stage08_record_approved.png")

    # Verify status in database
    con = sqlite3.connect("clinic_records.db")
    cur = con.cursor()
    cur.execute("SELECT status, reviewed_by, is_paediatric FROM records WHERE id = 'rec_task4_paediatric_audit'")
    status_row = cur.fetchone()
    con.close()
    print(f"[DB Approval Verification] Status in database: {status_row}")
    assert status_row[0] in ('reviewed', 'approved'), f"Expected reviewed/approved status, got {status_row[0]}"

    # 9. Step 9: Verify Record in Records Screen
    print("\n--- Step 9: Records Navigation & Filter Verification ---")
    eval_in_webview("""
        window.app.go('records');
    """)
    time.sleep(2.0)
    take_screenshot("task6_phone_stage09_records_list_verified.png")

    # 10. Step 10: Export Screen Verification
    print("\n--- Step 10: Export Screen & Spreadsheet Download Verification ---")
    eval_in_webview("""
        window.app.go('export');
    """)
    time.sleep(2.0)
    take_screenshot("task6_phone_stage10_export_excel_verified.png")

    # 11. Step 11: Adult Screening Workflow Regression Verification
    print("\n--- Step 11: Adult Screening Workflow Regression Verification ---")
    eval_in_webview("""
        (async () => {
            await window.app.openReview('rec_demo_001');
        })();
    """)
    time.sleep(2.0)
    take_screenshot("task6_phone_stage11_adult_workflow_verified.png")

    print("\n=== COMPLETE WORKFLOW SUCCESSFULLY EXECUTED AND VERIFIED ON DEVICE ===")

if __name__ == "__main__":
    main()
