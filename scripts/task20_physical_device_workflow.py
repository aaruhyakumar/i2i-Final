import os
import sys
import time
import json
import subprocess

ADB = r"C:\Users\ADMIN\AppData\Local\Android\Sdk\platform-tools\adb.exe"
ARTIFACT_DIR = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47"
CAMP_ID = "d95c06fa-5c10-4991-8afb-6071bef5b3fe"
RECORD_ID = "rec_20261117134632_f32f41"

def take_screenshot(filename: str):
    dev_path = f"/sdcard/{filename}"
    local_path = os.path.join(ARTIFACT_DIR, filename)
    subprocess.run([ADB, "shell", "screencap", "-p", dev_path], check=True)
    subprocess.run([ADB, "pull", dev_path, local_path], check=True)
    print(f"[Screenshot] Captured: {local_path}")
    return local_path

def get_current_socket():
    p = subprocess.run([ADB, "shell", "cat", "/proc/net/unix"], capture_output=True, text=True, check=True)
    for line in p.stdout.splitlines():
        if "webview_devtools" in line:
            parts = line.strip().split()
            if len(parts) >= 8 and parts[7].startswith("@webview_devtools"):
                return parts[7][1:]
    return None

def main():
    print("=== TASK 20: PHYSICAL DEVICE WORKFLOW & VISIBLE VERIFICATION ===")

    # Ensure device awake
    subprocess.run([ADB, "shell", "input", "keyevent", "KEYCODE_WAKEUP"], check=True)
    time.sleep(1)

    sock_name = get_current_socket()
    if not sock_name:
        print("[*] Launching app...")
        subprocess.run([ADB, "shell", "am", "start", "-n", "com.ink2insight.scanner/.MainActivity"], check=True)
        time.sleep(3)
        sock_name = get_current_socket()

    print(f"[*] Active Webview Socket: {sock_name}")
    subprocess.run([ADB, "reverse", "tcp:5001", "tcp:5001"], check=True)
    subprocess.run([ADB, "forward", "tcp:9222", f"localabstract:{sock_name}"], check=True)
    time.sleep(1)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.cdp_client import eval_in_webview

    # Step 1 & 2: Doctor Profile & Community Settings
    print("\n--- STEP 1 & 2: DOCTOR PROFILE & COMMUNITY SETTINGS ---")
    eval_in_webview("window.app.go('settings')")
    time.sleep(1.5)
    doc_info = json.loads(eval_in_webview("""
    JSON.stringify({
        doctor_name: localStorage.getItem('clinic_doctor_name'),
        community_name: localStorage.getItem('clinic_community_name'),
        community_code: localStorage.getItem('clinic_community_code'),
        auth_token_present: !!localStorage.getItem('auth_token')
    })
    """)['result']['result']['value'])
    print("  Doctor Session:", doc_info)
    assert doc_info['doctor_name'] == "Dr. Confirmed Audit"
    assert doc_info['community_code'] == "CAMP-2026"
    take_screenshot("task20_step1_doctor_profile_settings.png")

    # Step 3 & 4: Dashboard & Camp Counts
    print("\n--- STEP 3 & 4: CAMP DASHBOARD & COUNTS ---")
    eval_in_webview("""
    (async () => {
        await window.app.renderHome();
        window.app.go('home');
    })()
    """)
    time.sleep(2)
    dash_info = json.loads(eval_in_webview("""
    JSON.stringify({
        camp_count: window.app.state.camps.length,
        camp_name: window.app.state.camps[0]?.name,
        total_scanned: window.app.state.stats?.total_scanned,
        reviewed: window.app.state.stats?.reviewed,
        progress_pct: window.app.state.camps[0]?.progress_pct
    })
    """)['result']['result']['value'])
    print("  Dashboard Info:", dash_info)
    take_screenshot("task20_step2_camp_dashboard.png")

    # Step 5: Records Listing
    print("\n--- STEP 5: PATIENT RECORDS LISTING ---")
    eval_in_webview(f"window.app.openCampRecords('{CAMP_ID}')")
    time.sleep(2)
    rec_list_info = json.loads(eval_in_webview("""
    JSON.stringify({
        item_count: document.getElementById('records-full-list')?.querySelectorAll('.rec').length || 0,
        patient_name: document.getElementById('records-full-list')?.querySelector('.rec b')?.textContent || 'None'
    })
    """)['result']['result']['value'])
    print("  Records List Info:", rec_list_info)
    assert rec_list_info['patient_name'] == "B. SADHANA"
    take_screenshot("task20_step3_records_list.png")

    # Step 6: Patient Review & All 7 Sections
    print("\n--- STEP 6: PATIENT REVIEW & ALL 7 SECTIONS ---")
    eval_in_webview(f"window.app.openReview('{RECORD_ID}')")
    time.sleep(2)

    # Tab 1: Personal
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
    print("  Tab 1 (Personal):", p_vals)
    take_screenshot("task20_step4_tab1_personal.png")

    # Tab 2: Medical
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
    print("  Tab 2 (Medical):", m_vals)
    take_screenshot("task20_step5_tab2_medical.png")

    # Tab 3: Family
    eval_in_webview("window.app.switchReviewTab('family')")
    time.sleep(1)
    take_screenshot("task20_step6_tab3_family.png")

    # Tab 4: Health
    eval_in_webview("window.app.switchReviewTab('health')")
    time.sleep(1)
    take_screenshot("task20_step7_tab4_health.png")

    # Tab 5: Systemic
    eval_in_webview("window.app.switchReviewTab('system')")
    time.sleep(1)
    s_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        ent: document.getElementById('rev-sys-ent')?.value,
        skin: document.getElementById('rev-sys-skin')?.value
    })
    """)['result']['result']['value'])
    print("  Tab 5 (Systemic):", s_vals)
    take_screenshot("task20_step8_tab5_system.png")

    # Tab 6: Vision & Dental
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
    print("  Tab 6 (Vision & Dental):", v_vals)
    take_screenshot("task20_step9_tab6_vision.png")

    # Tab 7: Referral & Notes
    eval_in_webview("window.app.switchReviewTab('referral')")
    time.sleep(1)
    r_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        doc_sign: document.getElementById('rev-doc-sign')?.value
    })
    """)['result']['result']['value'])
    print("  Tab 7 (Referral):", r_vals)
    take_screenshot("task20_step10_tab7_referral.png")

    # Step 7: Original Scan Previews
    print("\n--- STEP 7: ORIGINAL SCAN PREVIEWS ---")
    eval_in_webview("document.getElementById('review').scrollTop = 0")
    time.sleep(1)
    scan_info = json.loads(eval_in_webview("""
    JSON.stringify({
        p1_src_present: !!document.getElementById('review-p1-img')?.src,
        p2_src_present: !!document.getElementById('review-p2-img')?.src,
        p1_complete: document.getElementById('review-p1-img')?.complete,
        p2_complete: document.getElementById('review-p2-img')?.complete
    })
    """)['result']['result']['value'])
    print("  Scan preview headers & images:", scan_info)
    assert scan_info['p1_src_present'] is True
    assert scan_info['p2_src_present'] is True
    take_screenshot("task20_step11_scan_preview.png")

    # Step 8: Patient Records Search & Filter
    print("\n--- STEP 8: RECORDS SEARCH & FILTERING ---")
    eval_in_webview(f"window.app.openCampRecords('{CAMP_ID}')")
    time.sleep(1)
    eval_in_webview("""
    (() => {
        const s = document.getElementById('records-search');
        if (s) {
            s.value = 'Sadhana';
            window.app.filterRecords();
        }
    })()
    """)
    time.sleep(1)
    filtered_count = eval_in_webview("document.getElementById('records-full-list')?.querySelectorAll('.rec').length")['result']['result']['value']
    print(f"  Search query 'Sadhana': matched {filtered_count} record(s)")
    assert filtered_count == 1
    take_screenshot("task20_step12_records_search.png")
    # Reset search
    eval_in_webview("""
    (() => {
        const s = document.getElementById('records-search');
        if (s) {
            s.value = '';
            window.app.filterRecords();
        }
    })()
    """)
    time.sleep(0.5)

    # Step 9 & 10: Excel Export & Live Spreadsheet Preview
    print("\n--- STEP 9 & 10: EXCEL EXPORT & SPREADSHEET PREVIEW ---")
    eval_in_webview(f"window.app.exportCampExcel('{CAMP_ID}')")
    time.sleep(2)
    excel_preview_info = json.loads(eval_in_webview("""
    JSON.stringify({
        screen: window.app.state?.currentScreen,
        ribbon_title: document.getElementById('excel-ribbon-filename')?.textContent,
        sheets: Array.from(document.querySelectorAll('.excel-sheet-tab')).map(t => t.textContent.trim()),
        has_table: !!document.querySelector('.excel-grid table')
    })
    """)['result']['result']['value'])
    print("  Excel Preview in App:", excel_preview_info)
    take_screenshot("task20_step13_excel_preview.png")

    # Step 11 & 12: Force-stop, Relaunch, Cold Restart Persistence
    print("\n--- STEP 11 & 12: FORCE-STOP, RELAUNCH, COLD RESTART PERSISTENCE ---")
    subprocess.run([ADB, "shell", "am", "force-stop", "com.ink2insight.scanner"], check=True)
    time.sleep(2)
    subprocess.run([ADB, "shell", "am", "start", "-n", "com.ink2insight.scanner/.MainActivity"], check=True)
    time.sleep(4)

    new_sock = get_current_socket()
    print(f"  New socket after cold restart: {new_sock}")
    subprocess.run([ADB, "reverse", "tcp:5001", "tcp:5001"], check=True)
    subprocess.run([ADB, "forward", "tcp:9222", f"localabstract:{new_sock}"], check=True)
    time.sleep(1)

    eval_in_webview("window.app.go('records')")
    time.sleep(1.5)
    eval_in_webview(f"window.app.openReview('{RECORD_ID}')")
    time.sleep(2)
    eval_in_webview("window.app.switchReviewTab('personal')")
    time.sleep(1)

    cold_p_vals = json.loads(eval_in_webview("""
    JSON.stringify({
        name: document.getElementById('rev-name')?.value,
        dob: document.getElementById('rev-dob')?.value,
        blood_group: document.getElementById('rev-blood-group')?.value,
        mobile: document.getElementById('rev-mobile')?.value,
        address: document.getElementById('rev-address')?.value,
        atr: document.getElementById('rev-atr')?.value
    })
    """)['result']['result']['value'])
    print("  Cold Restart Persistence Values:", cold_p_vals)
    assert cold_p_vals['name'] == "B. SADHANA"
    assert cold_p_vals['dob'] == "08/07/2013"
    assert cold_p_vals['blood_group'] == "O+ve"
    assert cold_p_vals['mobile'] == "9851753689"
    assert cold_p_vals['atr'] == "5"
    take_screenshot("task20_step14_cold_restart_persistence.png")

    print("\n>>> ALL PHYSICAL DEVICE WORKFLOW STAGES COMPLETED & VERIFIED! <<<")

if __name__ == "__main__":
    main()
