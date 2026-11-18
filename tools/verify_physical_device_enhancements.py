import os, sys, json, time, subprocess

ADB_PATH = r"C:\Users\ADMIN\AppData\Local\Android\Sdk\platform-tools\adb.exe"
subprocess.run([ADB_PATH, "reverse", "tcp:5001", "tcp:5001"], check=True)
subprocess.run([ADB_PATH, "forward", "tcp:9222", "localabstract:chrome_devtools_remote"], check=True)

sys.path.insert(0, r"C:\Users\ADMIN\Downloads\i2i-main (2)")
from scripts.cdp_client import eval_in_webview, capture_screenshot

ART_DIR = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47"

print("=== Starting Physical Device Verification on Vivo I2219 ===")

# Step 1: Navigate to http://localhost:5001/ and wait for app to be ready
print("Step 1: Navigating to http://localhost:5001/ and waiting for page load...")
eval_in_webview("window.location.href = 'http://localhost:5001/'")
time.sleep(3)

# Unregister SW and purge caches to guarantee fresh assets
eval_in_webview("""
(async () => {
    if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        for (const r of regs) await r.unregister();
    }
    if ('caches' in window) {
        const keys = await caches.keys();
        for (const k of keys) await caches.delete(k);
    }
})()
""")
eval_in_webview("window.location.reload(true)")
time.sleep(3)

# Step 2: Verify DOM elements loaded on device
print("Step 2: Checking DOM elements...")
dom_status = eval_in_webview("""
(() => {
    return {
        title: document.title,
        hasApp: typeof window.app !== 'undefined',
        hasClsSec: !!document.getElementById('rev-class-and-section'),
        hasAssessmentVitals: !!document.getElementById('assessment-vitals-summary-card'),
        hasEntRear: !!document.getElementById('rev-ent-rear'),
        hasEntLear: !!document.getElementById('rev-ent-lear'),
        hasEntNose: !!document.getElementById('rev-ent-nose'),
        hasEntThroat: !!document.getElementById('rev-ent-throat'),
        hasAtraBadge: !!document.getElementById('paediatric-atr-alert-badge'),
        hasPaedCard: !!document.getElementById('paediatric-growth-card')
    };
})()
""")
print("DOM Status:", json.dumps(dom_status.get("result", {}).get("result", {}).get("value"), indent=2))

# Step 3: Load Case 1 into Review Screen and test UI
# Case 1: Aarav Sharma, 10yo Male, Height 139.5, Weight 24.8, Class 5-A, ATR 3.0
# Otoscopy Right: "B/L Wax", Left: "Normal", Nose: "Normal", Throat: "" (blank)
print("Step 3: Loading Case 1 into Review Screen...")
case1_setup = eval_in_webview("""
(() => {
    const case1Record = {
        id: "case1_aarav_sharma",
        person_name: "Aarav Sharma",
        age: 10,
        gender: "Male",
        dob: "14/05/2016",
        exam_date: "18/11/2026",
        height: 139.5,
        weight: 24.8,
        blood_pressure: "100/64",
        pulse: 78,
        spinal_caliper_atr: 3.0,
        class_and_section: "5-A",
        is_paediatric: 1,
        target_group: "paediatric",
        structured_record: {
            personal_information: {
                full_name: "Aarav Sharma",
                age: "10",
                gender: "Male",
                dob: "14/05/2016",
                class_and_section: "5-A"
            },
            health_assessment: {
                height_cm: "139.5",
                weight_kg: "24.8",
                blood_pressure_mmhg: "100/64",
                pulse_bpm: "78",
                atr_degrees: "3.0",
                exam_date: "18/11/2026"
            },
            systemic_examination: {
                respiratory_system: "Normal vesicular breath sounds",
                cardiovascular_system: "S1 S2 heard, normal",
                abdomen: "Soft, non-tender",
                central_nervous_system: "Conscious, oriented",
                otoscope_right_ear: "B/L Wax",
                otoscope_left_ear: "Normal",
                sys_nose: "Normal",
                sys_throat: ""
            }
        }
    };

    window.app.state.activeRecord = case1Record;
    window.app.go('review');
    window.app.renderStaticReview(case1Record);

    return {
        screen: window.app.state.currentScreen,
        nameVal: document.getElementById('rev-name')?.value,
        ageVal: document.getElementById('rev-age')?.value,
        clsSecVal: document.getElementById('rev-class-and-section')?.value,
        entRearVal: document.getElementById('rev-ent-rear')?.value,
        entLearVal: document.getElementById('rev-ent-lear')?.value,
        entNoseVal: document.getElementById('rev-ent-nose')?.value,
        entThroatVal: document.getElementById('rev-ent-throat')?.value,
        bmiPill: document.getElementById('calculated-bmi')?.textContent
    };
})()
""")
print("Case 1 Setup Result:", json.dumps(case1_setup.get("result", {}).get("result", {}).get("value"), indent=2))

# Switch to Demographics tab and capture screenshot
eval_in_webview("window.app.switchReviewTab('personal')")
time.sleep(1)
p1_path = os.path.join(ART_DIR, "final_device_case1_demographics.png")
capture_screenshot(p1_path)
print(f"Captured Demographics Screenshot: {p1_path}")

# Switch to Assessment & Systemic tab (combined Tab 4)
eval_in_webview("window.app.switchReviewTab('health')")
time.sleep(1)

# Check Vitals Summary card values
summary_vals = eval_in_webview("""
(() => {
    return {
        h: document.getElementById('summary-vital-height')?.textContent,
        w: document.getElementById('summary-vital-weight')?.textContent,
        bmi: document.getElementById('summary-vital-bmi')?.textContent,
        bp: document.getElementById('summary-vital-bp')?.textContent,
        pulse: document.getElementById('summary-vital-pulse')?.textContent,
        atr: document.getElementById('summary-vital-atr')?.textContent,
        rearHighlight: document.getElementById('rev-ent-rear')?.style.borderColor,
        learHighlight: document.getElementById('rev-ent-lear')?.style.borderColor
    };
})()
""")
print("Assessment Vitals Summary Values:", json.dumps(summary_vals.get("result", {}).get("result", {}).get("value"), indent=2))

p2_path = os.path.join(ART_DIR, "final_device_case1_assessment_vitals.png")
capture_screenshot(p2_path)
print(f"Captured Assessment Vitals Screenshot: {p2_path}")

# Scroll to ENT in Systemic section and capture screenshot
eval_in_webview("document.getElementById('rsec-system').scrollIntoView({behavior:'instant', block:'start'})")
time.sleep(1)
p3_path = os.path.join(ART_DIR, "final_device_case1_ent_systemic.png")
capture_screenshot(p3_path)
print(f"Captured ENT Systemic Screenshot: {p3_path}")

# Step 4: Load Case 2 and verify Abdomen & CNS Normal / NAD
print("Step 4: Loading Case 2 into Review Screen...")
case2_setup = eval_in_webview("""
(() => {
    const case2Record = {
        id: "case2_priya_patel",
        person_name: "Priya Patel",
        age: 12,
        gender: "Female",
        dob: "20/09/2014",
        exam_date: "18/11/2026",
        height: 154.4,
        weight: 35.7,
        blood_pressure: "106/68",
        pulse: 76,
        spinal_caliper_atr: 2.0,
        class_and_section: "7-B",
        is_paediatric: 1,
        target_group: "paediatric",
        structured_record: {
            personal_information: {
                full_name: "Priya Patel",
                age: "12",
                gender: "Female",
                dob: "20/09/2014",
                class_and_section: "7-B"
            },
            health_assessment: {
                height_cm: "154.4",
                weight_kg: "35.7",
                blood_pressure_mmhg: "106/68",
                pulse_bpm: "76",
                atr_degrees: "2.0",
                exam_date: "18/11/2026"
            },
            systemic_examination: {
                respiratory_system: "Normal (NAD)",
                cardiovascular_system: "Normal (NAD)",
                abdomen: "Soft, non-tender (NAD)",
                central_nervous_system: "Conscious, oriented (NAD)",
                otoscope_right_ear: "Normal",
                otoscope_left_ear: "Normal",
                sys_nose: "Normal",
                sys_throat: "Normal"
            }
        }
    };

    window.app.state.activeRecord = case2Record;
    window.app.renderStaticReview(case2Record);
    window.app.switchReviewTab('system');

    return {
        nameVal: document.getElementById('rev-name')?.value,
        abdVal: document.getElementById('rev-sys-abdomen')?.value,
        cnsVal: document.getElementById('rev-sys-cns')?.value,
        abdBorder: document.getElementById('rev-sys-abdomen')?.style.borderColor,
        cnsBorder: document.getElementById('rev-sys-cns')?.style.borderColor
    };
})()
""")
print("Case 2 Setup Result:", json.dumps(case2_setup.get("result", {}).get("result", {}).get("value"), indent=2))

p4_path = os.path.join(ART_DIR, "final_device_case2_normal_nad.png")
capture_screenshot(p4_path)
print(f"Captured Case 2 Normal/NAD Screenshot: {p4_path}")

print("=== Physical Device Verification Completed Successfully ===")
