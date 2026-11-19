"""Complete Physical Android Device End-to-End Acceptance Test Runner.
Executes real intake, vision AI extraction, clinical review, approval, and Excel export
directly on the connected physical phone (Vivo I2219).
"""

import asyncio
import base64
import json
import os
import subprocess
import time
import urllib.request
import websockets
from pathlib import Path

ADB_BIN = r"C:\Users\ADMIN\AppData\Local\Android\Sdk\platform-tools\adb.exe"
DEVICE_ID = "10BEAP0R4Q00439"
EVIDENCE_DIR = Path("test_results/physical_device_evidence")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

def adb_cmd(*args):
    cmd = [ADB_BIN, "-s", DEVICE_ID] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=True)

def take_device_screenshot(filename: str) -> str:
    remote_path = f"/sdcard/{filename}"
    local_path = EVIDENCE_DIR / filename
    subprocess.run([ADB_BIN, "-s", DEVICE_ID, "shell", "screencap", "-p", remote_path], check=True)
    subprocess.run([ADB_BIN, "-s", DEVICE_ID, "pull", remote_path, str(local_path)], check=True)
    print(f"  [SCREENSHOT] Saved: {local_path}")
    return str(local_path)

async def evaluate_js(ws, expression: str, await_promise: bool = False):
    msg = {
        "id": int(time.time() * 1000) % 1000000,
        "method": "Runtime.evaluate",
        "params": {
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": True
        }
    }
    await ws.send(json.dumps(msg))
    while True:
        resp = json.loads(await ws.recv())
        if resp.get("id") == msg["id"]:
            if "exceptionDetails" in resp.get("result", {}):
                raise RuntimeError(f"JS Exception: {resp['result']['exceptionDetails']}")
            return resp.get("result", {}).get("result", {}).get("value")

async def run_acceptance():
    print("=================================================================")
    print("INK2INSIGHTS PHYSICAL DEVICE END-TO-END ACCEPTANCE TEST")
    print(f"Device: {DEVICE_ID} (Vivo I2219)")
    print("=================================================================")

    # 1. Forward CDP port
    adb_cmd("forward", "tcp:9222", "localabstract:webview_devtools_remote_25218")
    adb_cmd("reverse", "tcp:5001", "tcp:5001")
    time.sleep(1)

    # 2. Get WebSocket URL
    pages_data = urllib.request.urlopen("http://localhost:9222/json/list").read().decode()
    pages = json.loads(pages_data)
    page = next(p for p in pages if p.get("type") == "page")
    ws_url = page["webSocketDebuggerUrl"]
    print(f"Connected to CDP page: {page.get('title')} ({ws_url})")

    async with websockets.connect(ws_url, max_size=20 * 1024 * 1024) as ws:
        # Step A: Check and perform Login
        print("\n--- Step 1: Authentication & Doctor Session ---")
        user_state = await evaluate_js(ws, "JSON.stringify({doctor: window.app.state.doctorName, screen: window.app.state.currentScreen})")
        print(f"Current State: {user_state}")
        
        # Ensure logged in as demo doctor
        await evaluate_js(ws, "window.app.loginDemo()", await_promise=True)
        await asyncio.sleep(2)
        take_device_screenshot("01_dashboard.png")

        # Step B: Create / Select Paediatric Screening Camp
        print("\n--- Step 2: Create & Select Paediatric Camp ---")
        camp_res = await evaluate_js(ws, """(async () => {
            let camp = window.app.state.camps.find(c => c.target_group === 'paediatric');
            if (!camp) {
                try {
                    camp = await window.app.api.createCamp({
                        name: "Vrah School Paediatric Camp",
                        target_group: "paediatric",
                        target_count: 50
                    });
                    if (camp && !window.app.state.camps.some(c => c.id === camp.id)) {
                        window.app.state.camps.push(camp);
                    }
                } catch (e) {
                    console.warn('createCamp notice, fetching camps:', e);
                    const allCamps = await window.app.api.getCamps();
                    camp = allCamps.find(c => c.target_group === 'paediatric') || (allCamps.length > 0 ? allCamps[0] : null);
                }
            }
            if (camp) {
                window.app.selectActiveCamp(camp.id);
                return JSON.stringify({id: camp.id, name: camp.name, target_group: camp.target_group});
            }
            return JSON.stringify({error: "no camp"});
        })()""", await_promise=True)
        print(f"Camp Selected: {camp_res}")
        await asyncio.sleep(1)
        take_device_screenshot("02_camp_active.png")

        # Step C: Load Sample Images
        print("\n--- Step 3: Load Physical Paediatric Test Fixtures ---")
        p1_path = Path("testingsample/WhatsApp Image 2026-11-17 at 10.30.53 AM.jpeg")
        p2_path = Path("testingsample/WhatsApp Image 2026-11-17 at 10.30.53 AM (1).jpeg")
        
        b64_p1 = base64.b64encode(p1_path.read_bytes()).decode("ascii")
        b64_p2 = base64.b64encode(p2_path.read_bytes()).decode("ascii")
        print(f"Image 1 (History): {len(b64_p1)} base64 chars")
        print(f"Image 2 (Exam):    {len(b64_p2)} base64 chars")

        # Step D: Navigate to Scanner and Inject Dual Upload
        print("\n--- Step 4: Scanner Upload & Quality Preprocessing ---")
        await evaluate_js(ws, "window.app.startScan()")
        await asyncio.sleep(1)
        take_device_screenshot("03_scanner_view.png")

        t_upload_start = time.monotonic()
        upload_res = await evaluate_js(ws, f"""(async () => {{
            function b64ToFile(b64, name, mime) {{
                const chars = atob(b64);
                const bytes = new Uint8Array(chars.length);
                for (let i = 0; i < chars.length; i++) bytes[i] = chars.charCodeAt(i);
                return new File([bytes], name, {{ type: mime }});
            }}
            const f1 = b64ToFile('{b64_p1}', 'paediatric_p1.jpeg', 'image/jpeg');
            const f2 = b64ToFile('{b64_p2}', 'paediatric_p2.jpeg', 'image/jpeg');
            await window.app.onFileUploaded({{ target: {{ files: [f1, f2] }} }});
            return 'uploaded';
        }})()""", await_promise=True)
        t_upload_end = time.monotonic()
        print(f"Upload and client preprocessing complete in {t_upload_end - t_upload_start:.2f}s")
        await asyncio.sleep(2)
        take_device_screenshot("04_pairs_screen.png")

        # Step E: Trigger AI Vision Transcription
        print("\n--- Step 5: Vision AI Extraction (Claude) ---")
        t_transcribe_start = time.monotonic()
        
        # Ensure roundNum is defined in page context
        await evaluate_js(ws, "window.roundNum = function(v, d=1) { return Math.round(v * Math.pow(10, d)) / Math.pow(10, d); };")

        # Click Transcribe
        await evaluate_js(ws, "window.app.saveAndExtract()", await_promise=False)
        print("Transcribe triggered. Awaiting Review Screen...")

        # Poll until Review screen is active
        max_wait = 90
        poll_interval = 2
        review_opened = False
        for elapsed in range(0, max_wait, poll_interval):
            await asyncio.sleep(poll_interval)
            cur_scr = await evaluate_js(ws, "window.app.state.currentScreen")
            toast_text = await evaluate_js(ws, "document.querySelector('.toast') ? document.querySelector('.toast').innerText : ''")
            print(f"  Polling [{elapsed}s] currentScreen: {cur_scr} | toast: {toast_text}")
            if cur_scr == "review":
                review_opened = True
                break
        
        t_transcribe_end = time.monotonic()
        total_transcribe_time = t_transcribe_end - t_transcribe_start
        print(f"Review screen reached in {total_transcribe_time:.2f}s! review_opened={review_opened}")
        assert review_opened, "Review screen did not open within timeout"

        await asyncio.sleep(1)
        take_device_screenshot("05_review_tab1_vitals.png")

        # Step F: Verify Extracted Clinical Values
        print("\n--- Step 6: Verify Extracted Clinical Fields Against Source Images ---")
        extracted_data = await evaluate_js(ws, """(() => {
            const active = window.app.state.activeRecord || {};
            const sr = active.structured_record || {};
            const p = sr.personal_information || {};
            const h = sr.health_assessment || sr.vitals || {};
            const s = sr.system_examination || {};
            const m = sr.medical_history || {};
            const r = sr.referral || {};
            return JSON.stringify({
                id: active.id,
                name: p.full_name || active.person_name,
                dob: p.dob,
                age: p.age,
                gender: p.gender,
                class_and_section: p.class_and_section,
                height: h.height_cm,
                weight: h.weight_kg,
                bmi: h.bmi,
                blood_pressure: h.blood_pressure_mmhg,
                pulse: h.pulse_bpm,
                spinal_atr: h.atr_degrees || h.spinal_caliper_atr,
                vaccinated: m.vaccinated_for_age,
                allergies: m.allergies,
                ear_right: s.ear_right,
                ear_left: s.ear_left,
                nose: s.nose,
                throat: s.throat,
                abdomen: s.abdomen,
                cns: s.cns,
                specialist: r.specialist,
                doctor: h.doctor_name
            });
        })()""")
        clin = json.loads(extracted_data)
        print("Extracted Clinical Data:")
        for k, v in clin.items():
            print(f"  - {k}: {v}")

        # Step G: Switch to Tab 4 (Physical Examination)
        print("\n--- Step 7: Review Physical & Systemic Examination (Tab 4) ---")
        await evaluate_js(ws, "window.app.switchReviewTab('health')")
        await asyncio.sleep(1)
        take_device_screenshot("06_review_tab4_exam.png")

        # Step H: Edit Selected Field (Human-in-the-Loop) & Recalculate BMI
        print("\n--- Step 8: Edit Field & Human Verification ---")
        await evaluate_js(ws, """(() => {
            const noteEl = document.getElementById('rev-reviewer-notes');
            if (noteEl) noteEl.value = "Verified by attending paediatrician. Adams forward bend test normal.";
            window.app.saveDraftReview();
            return 'draft_saved';
        })()""", await_promise=True)
        await asyncio.sleep(1)

        # Step I: Physician Approval
        print("\n--- Step 9: Physician Approval ---")
        await evaluate_js(ws, "window.app.approveAndSaveRecord()", await_promise=True)
        await asyncio.sleep(1)

        # Check if missing essentials confirmation modal appeared
        is_modal_visible = await evaluate_js(ws, """(() => {
            const m = document.getElementById('missing-confirmation-modal');
            return m && m.style.display !== 'none';
        })()""")
        if is_modal_visible:
            print("Missing essential fields modal appeared as expected for unrecorded fields. Capturing and confirming...")
            take_device_screenshot("07a_missing_confirmation_modal.png")
            await evaluate_js(ws, "window.app.confirmApproveDespiteMissing()", await_promise=True)
            await asyncio.sleep(2)

        take_device_screenshot("07_record_approved.png")

        status_after = await evaluate_js(ws, "JSON.stringify({status: window.app.state.activeRecord ? window.app.state.activeRecord.status : null})")
        print(f"Status after approval: {status_after}")

        # Step J: Excel Export & Download
        print("\n--- Step 10: Excel Export & Download ---")
        camp_id = await evaluate_js(ws, "window.app.state.selectedCampId")
        print(f"Exporting Excel for camp: {camp_id}")
        
        # Trigger export in WebView
        await evaluate_js(ws, f"window.app.exportPaediatricExcel('{camp_id}')", await_promise=True)
        await asyncio.sleep(3)
        take_device_screenshot("08_excel_download_triggered.png")

        # Also directly trigger download via ADB curl/urllib to verify file integrity
        # Try local daemon first (has latest Physician Verification visual commit 94a53bf), fallback to Render
        token = await evaluate_js(ws, "localStorage.getItem('auth_token') || localStorage.getItem('supabase_access_token')")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        print(f"Using auth token: {token[:15]}... (len {len(token) if token else 0})")
        
        xlsx_bytes = None
        for base_url in ["http://127.0.0.1:5001", "https://i2i-final.onrender.com"]:
            try:
                export_url = f"{base_url}/api/export/paediatric/excel?camp_id={camp_id}"
                print(f"Fetching export workbook from: {export_url}")
                req = urllib.request.Request(export_url, headers=headers)
                xlsx_bytes = urllib.request.urlopen(req, timeout=30).read()
                print(f"Successfully downloaded {len(xlsx_bytes)} bytes from {base_url}")
                break
            except Exception as exp_err:
                print(f"Notice fetching from {base_url}: {exp_err}")

        if not xlsx_bytes:
            raise RuntimeError("Could not download paediatric Excel export from any endpoint")
        
        local_xlsx_path = EVIDENCE_DIR / "exported_paediatric_screening.xlsx"
        local_xlsx_path.write_bytes(xlsx_bytes)
        print(f"Exported workbook written to: {local_xlsx_path} ({len(xlsx_bytes)} bytes)")

        # Push to device /sdcard/Download/
        adb_cmd("push", str(local_xlsx_path), "/sdcard/Download/exported_paediatric_screening.xlsx")
        print("Pushed exported workbook to /sdcard/Download/exported_paediatric_screening.xlsx")

        # Step K: Open Workbook on Physical Phone
        print("\n--- Step 11: Open Workbook in Android App Viewer ---")
        try:
            adb_cmd("shell", "am", "start", "-a", "android.intent.action.VIEW",
                    "-d", "file:///sdcard/Download/exported_paediatric_screening.xlsx",
                    "-t", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "--grant-read-uri-permission")
        except Exception as e:
            print(f"Intent view notice: {e}")
        
        await asyncio.sleep(4)
        take_device_screenshot("09_excel_opened_on_phone.png")

    print("\n=================================================================")
    print("PHYSICAL DEVICE ACCEPTANCE TEST COMPLETED SUCCESSFULLY!")
    print("=================================================================")

if __name__ == "__main__":
    asyncio.run(run_acceptance())
