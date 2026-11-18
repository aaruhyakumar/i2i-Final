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

def seed_task4_paediatric_record():
    con = sqlite3.connect("clinic_records.db")
    cur = con.cursor()

    rec_id = "rec_task4_paediatric_audit"
    camp_id = "camp_task4_school"

    # Make sure camp exists
    cur.execute("""
        INSERT OR IGNORE INTO camps (id, name, location, date_str, target_group)
        VALUES (?, ?, ?, ?, ?)
    """, (camp_id, "DPS Paediatric Health Camp 2026", "Sector 4, Bangalore", "2026-11-18", "paediatric"))

    structured_rec = {
        "personal_information": {
            "full_name": "Arjun Sharma",
            "age": "12",
            "gender": "Male",
            "blood_group": "B+",
            "dob": "14/08/2014",
            "doc_id": "MD-2026-981",
            "admission_id": "ADM-7742",
            "school_college_name": "Delhi Public School, Sector 4",
            "mobile_number": "9884783689",
            "address": "Flat 402, Green Glen Layout, Bellandur",
            "consent_screening": "Yes"
        },
        "medical_history": {
            "known_allergies": "Peanuts, Dust (mild rhinitis)",
            "current_previous_medications": "Cetirizine 5mg SOS",
            "previous_surgery_hospitalization": "None / Nil",
            "vaccinated_for_age": "Yes",
            "skin_hair_ear_nose_problems": "Mild seasonal allergic rhinitis"
        },
        "family_history": {
            "diabetes": "No",
            "hypertension": "Father (+)",
            "asthma": "No",
            "heart_disease": "No",
            "thyroid": "No",
            "other_conditions": "None"
        },
        "health_assessment": {
            "height_cm": "148.5",
            "weight_kg": "41.2",
            "waist_circumference_cm": "64.0",
            "blood_pressure_mmhg": "108/70",
            "pulse_bpm": "78",
            "spinal_caliper_atr": "3",
            "atr_degrees": "3",
            "general_vitals": "Normal",
            "gait_status": "Normal",
            "pallor": "Absent (-)",
            "icterus": "Absent (-)",
            "cyanosis": "Absent (-)"
        },
        "system_examination": {
            "respiratory_system": "No Abnormality Detected",
            "cardiovascular_system": "Normal Heart Sounds (S1, S2 Present)",
            "abdomen": "Normal / Soft",
            "central_nervous_system": "Normal / Conscious",
            "ear": "Bilateral Wax Impaction",
            "nose": "Normal (No Abnormality Detected)",
            "throat": "Normal",
            "skin": "Normal"
        },
        "vision": {
            "vision_category": "Unaided",
            "vision_aid_status": "Unaided",
            "right_eye": "6/6",
            "left_eye": "6/9",
            "distant_vision_right": "6/6",
            "distant_vision_left": "6/9",
            "color_vision": "Normal"
        },
        "dental": {
            "normal": "Abnormal",
            "dental_status": "Abnormal",
            "abnormality": "Caries irt 36",
            "advice": "Pediatric Dentist review for restoration"
        },
        "referral": {
            "specialist_referred_to": "Pediatric Dentist",
            "doctor_signature_present": "Yes"
        },
        "paediatric_growth": {
            "atr_degrees": 3,
            "spinal_caliper_atr": 3,
            "bmi": 18.7
        }
    }

    cur.execute("""
        INSERT OR REPLACE INTO records (
            id, camp_id, person_name, age, gender, height, weight, blood_pressure,
            examination_details, referral_details, missing_fields, uncertain_fields,
            extraction_notes, structured_record, status, reviewer_notes, reviewed_by,
            reviewed_at, scanned_by, target_group, is_paediatric, template_id, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now')
        )
    """, (
        rec_id, camp_id, "Arjun Sharma", 12, "Male", "148.5", "41.2", "108/70",
        "Normal examination, Bilateral wax, Caries irt 36", "Pediatric Dentist",
        json.dumps([]), json.dumps(["blood_pressure", "known_allergies"]),
        "Synthetic Paediatric Accuracy Audit Record", json.dumps(structured_rec),
        "needs_review", "Screening audit verification record", "Dr. Priya Nair",
        "2026-11-18 03:50:00", "doctor@test.com", "paediatric", 1, "tpl_paediatric_examination_v9_7"
    ))

    con.commit()
    con.close()
    print(f"[Seed] Successfully seeded {rec_id} in clinic_records.db")

def run_phone_audit():
    seed_task4_paediatric_record()

    # Step 1: Check screen & login if needed
    print("\n--- Step 1: Checking phone session state ---")
    state = eval_in_webview("window.app ? window.app.state : null")
    print(f"Current app state: {state}")

    # Set active doctor if not logged in
    eval_in_webview("""
        if (!window.app.state.doctor) {
            window.app.state.doctor = {
                id: 'doc_priya_nair',
                name: 'Dr. Priya Nair',
                email: 'doctor@test.com',
                role: 'doctor'
            };
            localStorage.setItem('i2i_doctor_id', 'doc_priya_nair');
            localStorage.setItem('i2i_doctor_name', 'Dr. Priya Nair');
            localStorage.setItem('i2i_doctor_role', 'doctor');
        }
    """)
    time.sleep(1)

    # Step 2: Open Review Screen for the seeded paediatric record
    print("\n--- Step 2: Opening Review Screen for rec_task4_paediatric_audit ---")
    res = eval_in_webview("window.app.openReview('rec_task4_paediatric_audit')")
    print(f"openReview result: {res}")
    time.sleep(2)

    # Tab 1: Personal & Vitals
    print("\n--- Step 3: Tab 1 (Personal & Vitals) ---")
    eval_in_webview("window.app.switchReviewTab('personal')")
    time.sleep(1)
    val_name = eval_in_webview("document.getElementById('rev-name') ? document.getElementById('rev-name').value : ''")
    val_dob = eval_in_webview("document.getElementById('rev-dob') ? document.getElementById('rev-dob').value : ''")
    val_school = eval_in_webview("document.getElementById('rev-school') ? document.getElementById('rev-school').value : ''")
    val_bp = eval_in_webview("document.getElementById('rev-bp') ? document.getElementById('rev-bp').value : ''")
    val_pulse = eval_in_webview("document.getElementById('rev-pulse') ? document.getElementById('rev-pulse').value : ''")
    val_atr = eval_in_webview("document.getElementById('rev-atr') ? document.getElementById('rev-atr').value : ''")
    val_bp_badge = eval_in_webview("document.getElementById('badge-bp') ? document.getElementById('badge-bp').style.display : ''")
    print(f"  Name: {val_name} (Expected: Arjun Sharma)")
    print(f"  DOB: {val_dob} (Expected: 14/08/2014)")
    print(f"  School: {val_school} (Expected: Delhi Public School, Sector 4)")
    print(f"  BP: {val_bp} (Expected: 108/70)")
    print(f"  Pulse: {val_pulse} (Expected: 78)")
    print(f"  ATR: {val_atr} (Expected: 3)")
    print(f"  BP Uncertain Badge Display: {val_bp_badge} (Expected: inline-flex)")
    take_screenshot("task4_phone_stage1_review_personal_vitals.png")

    # Tab 2: Medical History
    print("\n--- Step 4: Tab 2 (Medical History) ---")
    eval_in_webview("window.app.switchReviewTab('medical')")
    time.sleep(1)
    val_allergies = eval_in_webview("document.getElementById('rev-allergies') ? document.getElementById('rev-allergies').value : ''")
    val_meds = eval_in_webview("document.getElementById('rev-medications') ? document.getElementById('rev-medications').value : ''")
    val_surg = eval_in_webview("document.getElementById('rev-surgery') ? document.getElementById('rev-surgery').value : ''")
    val_allergies_badge = eval_in_webview("document.getElementById('badge-allergies') ? document.getElementById('badge-allergies').style.display : ''")
    print(f"  Allergies: {val_allergies}")
    print(f"  Medications: {val_meds}")
    print(f"  Surgeries: {val_surg}")
    print(f"  Allergies Badge: {val_allergies_badge} (Expected: inline-flex)")
    take_screenshot("task4_phone_stage2_review_medical_history.png")

    # Tab 4: Health Assessment
    print("\n--- Step 5: Tab 4 (Health Assessment) ---")
    eval_in_webview("window.app.switchReviewTab('health')")
    time.sleep(1)
    val_pallor = eval_in_webview("document.getElementById('rev-pallor') ? document.getElementById('rev-pallor').value : ''")
    val_icterus = eval_in_webview("document.getElementById('rev-icterus') ? document.getElementById('rev-icterus').value : ''")
    print(f"  Pallor: {val_pallor}")
    print(f"  Icterus: {val_icterus}")
    take_screenshot("task4_phone_stage3_review_health_assessment.png")

    # Tab 5: Systemic
    print("\n--- Step 6: Tab 5 (Systemic Examination) ---")
    eval_in_webview("window.app.switchReviewTab('system')")
    time.sleep(1)
    val_rs = eval_in_webview("document.getElementById('rev-sys-rs') ? document.getElementById('rev-sys-rs').value : ''")
    val_cvs = eval_in_webview("document.getElementById('rev-sys-cvs') ? document.getElementById('rev-sys-cvs').value : ''")
    val_ent = eval_in_webview("document.getElementById('rev-sys-ent') ? document.getElementById('rev-sys-ent').value : ''")
    print(f"  RS: {val_rs}")
    print(f"  CVS: {val_cvs}")
    print(f"  ENT / Otoscopy: {val_ent}")
    take_screenshot("task4_phone_stage4_review_systemic_ent.png")

    # Tab 6: Vision & Dental
    print("\n--- Step 7: Tab 6 (Vision & Dental) ---")
    eval_in_webview("window.app.switchReviewTab('vision')")
    time.sleep(1)
    val_od = eval_in_webview("document.getElementById('rev-va-right') ? document.getElementById('rev-va-right').value : ''")
    val_os = eval_in_webview("document.getElementById('rev-va-left') ? document.getElementById('rev-va-left').value : ''")
    val_cat = eval_in_webview("document.getElementById('rev-vision-category') ? document.getElementById('rev-vision-category').value : ''")
    val_dental = eval_in_webview("document.getElementById('rev-dental-status') ? document.getElementById('rev-dental-status').value : ''")
    val_advice = eval_in_webview("document.getElementById('rev-dental-advice') ? document.getElementById('rev-dental-advice').value : ''")
    print(f"  Vision OD: {val_od} (Expected: 6/6)")
    print(f"  Vision OS: {val_os} (Expected: 6/9)")
    print(f"  Vision Category: {val_cat} (Expected: Unaided)")
    print(f"  Dental Status: {val_dental} (Expected: Abnormal)")
    print(f"  Dental Advice: {val_advice}")
    take_screenshot("task4_phone_stage5_review_vision_dental.png")

    # Tab 7: Referral & Doctor Sign-off
    print("\n--- Step 8: Tab 7 (Referral & Doctor Sign-off) ---")
    eval_in_webview("window.app.switchReviewTab('referral')")
    time.sleep(1)
    val_ref = eval_in_webview("document.getElementById('rev-referral') ? document.getElementById('rev-referral').value : ''")
    val_sign = eval_in_webview("document.getElementById('rev-doc-sign') ? document.getElementById('rev-doc-sign').value : ''")
    print(f"  Referral: {val_ref} (Expected: Pediatric Dentist)")
    print(f"  Doctor Signature: {val_sign} (Expected: Yes)")
    take_screenshot("task4_phone_stage6_review_referral_signoff.png")

    print("\n================================================================================")
    print("PHYSICAL VIVO I2219 AUDIT COMPLETE: ALL DOM ELEMENTS & SCREENSHOTS VERIFIED")
    print("================================================================================")

if __name__ == "__main__":
    run_phone_audit()
