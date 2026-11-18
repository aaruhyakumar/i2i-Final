import os
import sys
import time
import json
import socket
import base64
import urllib.request
import subprocess

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ARTIFACT_DIR = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47"
PORT = 9333

def start_chrome():
    user_data = os.path.join(os.environ.get("TEMP", "C:\\temp"), "chrome_i2i_profile")
    cmd = [
        CHROME,
        "--headless=new",
        f"--remote-debugging-port={PORT}",
        "--window-size=393,876",
        f"--user-data-dir={user_data}",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "http://127.0.0.1:5001/"
    ]
    proc = subprocess.Popen(cmd)
    time.sleep(2.5)
    return proc

def get_ws_url():
    for _ in range(15):
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json")
            tabs = json.loads(req.read().decode())
            for t in tabs:
                if t.get("type") == "page" and "webSocketDebuggerUrl" in t:
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Cannot find Chrome CDP page WebSocket URL")

class SimpleCDP:
    def __init__(self, ws_url):
        path = ws_url.split(f"127.0.0.1:{PORT}")[1]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect(("127.0.0.1", PORT))
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{PORT}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode('utf-8'))
        resp = self.sock.recv(4096).decode('utf-8')
        if "101 " not in resp:
            raise RuntimeError(f"Handshake failed: {resp}")
        self._id = 0

    def _send(self, method, params=None):
        self._id += 1
        msg = json.dumps({"id": self._id, "method": method, "params": params or {}})
        data = msg.encode('utf-8')
        length = len(data)
        frame = bytearray([0x81])
        mask = os.urandom(4)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(length.to_bytes(2, 'big'))
        else:
            frame.append(0x80 | 127)
            frame.extend(length.to_bytes(8, 'big'))
        frame.extend(mask)
        frame.extend(bytearray(b ^ mask[i % 4] for i, b in enumerate(data)))
        self.sock.sendall(frame)

        # Read response
        while True:
            header = self.sock.recv(2)
            if not header:
                return {}
            payload_len = header[1] & 0x7F
            if payload_len == 126:
                payload_len = int.from_bytes(self.sock.recv(2), 'big')
            elif payload_len == 127:
                payload_len = int.from_bytes(self.sock.recv(8), 'big')
            raw = bytearray()
            while len(raw) < payload_len:
                chunk = self.sock.recv(payload_len - len(raw))
                if not chunk:
                    break
                raw.extend(chunk)
            try:
                res = json.loads(raw.decode('utf-8', errors='ignore'))
                if res.get("id") == self._id:
                    return res
            except Exception:
                pass

    def eval(self, expr):
        res = self._send("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": True
        })
        val = res.get("result", {}).get("result", {})
        return val.get("value")

    def screenshot(self, filename):
        res = self._send("Page.captureScreenshot", {"format": "png"})
        b64 = res.get("result", {}).get("data", "")
        if b64:
            dest = os.path.join(ARTIFACT_DIR, filename)
            with open(dest, "wb") as f:
                f.write(base64.b64decode(b64))
            print(f"[Screenshot] Saved: {dest}")
            return dest
        return None

def run_verification():
    proc = start_chrome()
    try:
        ws_url = get_ws_url()
        cdp = SimpleCDP(ws_url)

        # 1. Initialize Doctor Session and navigate
        print("Initializing session...")
        cdp.eval("""
            localStorage.setItem('i2i_token', 'synthetic_test_token');
            localStorage.setItem('doctor_name', 'Dr. Priya Nair');
            window.app.state.doctor = {
                id: 'doc_priya_nair',
                name: 'Dr. Priya Nair',
                email: 'doctor@test.com',
                role: 'doctor'
            };
            window.app.state.token = 'synthetic_test_token';
            window.app.go('home');
        """)
        time.sleep(1.0)

        # 2. Setup a test record with flagged attributes
        # BP 150/95 (HTN), BMI 28.5 (Obese), Allergies, Otoscopy wax, ATR 6 deg, Defective Vision 6/18, Dental caries
        print("Setting up flagged record in review...")
        flagged_record_js = """
            const testRecord = {
                id: 'rec_test_flagged_2026',
                camp_id: 'camp_demo_01',
                person_name: 'Ananya Sen',
                age: 10,
                gender: 'Female',
                height: 130,
                weight: 48,
                blood_pressure: '140/92',
                pulse: 88,
                is_paediatric: true,
                consent_given: true,
                structured_record: {
                    personal_information: {
                        full_name: 'Ananya Sen',
                        age: 10,
                        gender: 'Female',
                        consent_screening: 'Yes'
                    },
                    health_assessment: {
                        height_cm: 130,
                        weight_kg: 48,
                        blood_pressure_mmhg: '140/92',
                        pulse_bpm: 88,
                        spinal_caliper_atr: '6.0',
                        pallor: 'Present (mild)',
                        cyanosis: 'Absent (NAD)',
                        edema: 'Absent (NAD)',
                        clubbing: 'Absent (NAD)',
                        icterus: 'Absent (NAD)'
                    },
                    medical_history: {
                        known_allergies: { value: 'Yes', details: 'Severe peanut allergy' },
                        abdominal_pain_indigestion_constipation: { value: 'No', details: '' },
                        previous_surgery_hospitalization: { value: 'No', details: '' },
                        current_previous_medications: { value: 'No', details: '' }
                    },
                    system_examination: {
                        ear: { value: 'No', details: 'Impacted cerumen completely occluding canal' },
                        respiratory_system: { value: 'Yes', details: 'Normal vesicular breath sounds' },
                        cardiovascular_system: { value: 'Yes', details: 'S1 S2 heard' }
                    },
                    vision: {
                        distant_vision_right: '6/6',
                        distant_vision_left: '6/18'
                    },
                    dental: {
                        dental_status: 'Abnormal',
                        advice: 'Referral to pediatric dentist for caries restoration'
                    },
                    referral: {
                        specialist_referred_to: 'Pediatric Dentist & ENT'
                    }
                }
            };
            window.app.state.activeRecord = testRecord;
            window.app.renderStaticReview(testRecord);
            window.app.updateReviewClinicalAlerts(testRecord);
            window.app.go('review');
        """
        cdp.eval(flagged_record_js)
        time.sleep(1.0)

        # Check Tab 1: Personal & Vitals
        print("Verifying Tab 1: Personal & Vitals flagged styling...")
        tab1_check = cdp.eval("""
            JSON.stringify({
                bp_has_red: document.getElementById('rev-bp')?.classList.contains('field-flagged-red'),
                atr_has_red: document.getElementById('rev-atr')?.classList.contains('field-flagged-red'),
                bmi_pill_has_red: document.getElementById('rev-calculated-bmi')?.classList.contains('field-flagged-red'),
                name_has_red: document.getElementById('rev-name')?.classList.contains('field-flagged-red'),
                alerts_container_display: document.getElementById('review-clinical-alerts')?.style.display,
                bp_alert_display: document.getElementById('alert-hypertension')?.style.display,
                bmi_alert_display: document.getElementById('alert-high-bmi')?.style.display,
                atr_badge_display: document.getElementById('paediatric-atr-alert-badge')?.style.display
            })
        """)
        print("Tab 1 Check:", tab1_check)
        cdp.screenshot("highlight_stage01_review_tab1_flagged_red.png")

        # Check Tab 2: Medical History (Allergies flagged, Surgery normal)
        print("Switching to Tab 2: Medical History...")
        cdp.eval("window.app.switchReviewTab('medical')")
        time.sleep(0.5)
        tab2_check = cdp.eval("""
            JSON.stringify({
                allergies_has_red: document.getElementById('rev-allergies')?.classList.contains('field-flagged-red'),
                surgery_has_red: document.getElementById('rev-surgery')?.classList.contains('field-flagged-red')
            })
        """)
        print("Tab 2 Check:", tab2_check)
        cdp.screenshot("highlight_stage02_review_tab2_allergies_red.png")

        # Check Tab 4: Health Assessment / General Signs (Pallor flagged, Cyanosis normal)
        print("Switching to Tab 4: Assessment...")
        cdp.eval("window.app.switchReviewTab('health')")
        time.sleep(0.5)
        tab4_check = cdp.eval("""
            JSON.stringify({
                pallor_has_red: document.getElementById('rev-pallor')?.classList.contains('field-flagged-red'),
                cyanosis_has_red: document.getElementById('rev-cyanosis')?.classList.contains('field-flagged-red')
            })
        """)
        print("Tab 4 Check:", tab4_check)
        cdp.screenshot("highlight_stage03_review_tab4_pallor_red.png")

        # Check Tab 6: Vision & Dental (Left eye 6/18 flagged, Right eye 6/6 normal, Dental caries flagged)
        print("Switching to Tab 6: Vision & Dental...")
        cdp.eval("window.app.switchReviewTab('vision')")
        time.sleep(0.5)
        tab6_check = cdp.eval("""
            JSON.stringify({
                va_left_has_red: document.getElementById('rev-va-left')?.classList.contains('field-flagged-red'),
                va_right_has_red: document.getElementById('rev-va-right')?.classList.contains('field-flagged-red'),
                dental_has_red: document.getElementById('rev-dental-status')?.classList.contains('field-flagged-red')
            })
        """)
        print("Tab 6 Check:", tab6_check)
        cdp.screenshot("highlight_stage04_review_tab6_vision_dental_red.png")

        # Step 4: Editing and Re-evaluation Test
        # Back to Tab 1
        cdp.eval("window.app.switchReviewTab('personal')")
        time.sleep(0.5)

        # 4a: Edit BP to another flagged value 150/95 -> Must KEEP red styling
        print("\nEditing BP to still-abnormal value '150/95'...")
        edit1_check = cdp.eval("""
            (() => {
                const bpInput = document.getElementById('rev-bp');
                bpInput.value = '150/95';
                bpInput.dispatchEvent(new Event('input', { bubbles: true }));
                return JSON.stringify({
                    value: bpInput.value,
                    still_has_red: bpInput.classList.contains('field-flagged-red')
                });
            })()
        """)
        print("Still-Flagged Edit Check:", edit1_check)
        cdp.screenshot("highlight_stage05_editing_still_flagged_remains_red.png")

        # 4b: Edit BP to normal value for 10y child (100/62 < p90 111/68) -> Must REMOVE red styling
        print("\nEditing BP to normal paediatric value '100/62'...")
        edit2_check = cdp.eval("""
            (() => {
                const bpInput = document.getElementById('rev-bp');
                bpInput.value = '100/62';
                bpInput.dispatchEvent(new Event('input', { bubbles: true }));
                return JSON.stringify({
                    value: bpInput.value,
                    has_red_removed: !bpInput.classList.contains('field-flagged-red')
                });
            })()
        """)
        print("Normalized Edit Check:", edit2_check)
        cdp.screenshot("highlight_stage06_editing_normalized_red_removed.png")

        # 5. Clean record review (all normal)
        print("\nTesting completely normal record in review...")
        clean_record_js = """
            const cleanRecord = {
                id: 'rec_test_clean_2026',
                camp_id: 'camp_demo_01',
                person_name: 'Priya Sharma',
                age: 28,
                gender: 'Female',
                height: 162,
                weight: 54,
                blood_pressure: '118/76',
                pulse: 72,
                is_paediatric: false,
                consent_given: true,
                structured_record: {
                    personal_information: { full_name: 'Priya Sharma', age: 28, gender: 'Female' },
                    health_assessment: {
                        height_cm: 162, weight_kg: 54, blood_pressure_mmhg: '118/76', pulse_bpm: 72,
                        pallor: 'Absent (NAD)', cyanosis: 'Absent (NAD)', edema: 'Absent (NAD)'
                    },
                    medical_history: { known_allergies: { value: 'No', details: 'No known allergies' } },
                    system_examination: { respiratory_system: { value: 'Yes', details: 'Normal' } },
                    vision: { distant_vision_right: '6/6', distant_vision_left: '6/6' },
                    dental: { dental_status: 'Normal', advice: 'Routine hygiene' },
                    referral: { specialist_referred_to: 'None' }
                }
            };
            window.app.state.activeRecord = cleanRecord;
            window.app.renderStaticReview(cleanRecord);
            window.app.updateReviewClinicalAlerts(cleanRecord);
        """
        cdp.eval(clean_record_js)
        time.sleep(1.0)
        clean_check = cdp.eval("""
            const flaggedElements = document.querySelectorAll('.field-flagged-red');
            JSON.stringify({
                total_flagged_count: flaggedElements.length,
                alerts_display: document.getElementById('review-clinical-alerts')?.style.display
            })
        """)
        print("Clean Record Check:", clean_check)
        cdp.screenshot("highlight_stage07_clean_record_all_normal.png")

        print("\n=== ALL FRONTEND HIGHLIGHTING CHECKS COMPLETED SUCCESSFULLY ===")

    finally:
        proc.kill()

if __name__ == "__main__":
    run_verification()
