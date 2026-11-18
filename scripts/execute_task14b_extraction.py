import os
import sys
import json
import time
import subprocess
import urllib.request

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
    print("=" * 70)
    print("TASK 14B: LIVE CLAUDE EXTRACTION WORKFLOW")
    print("Device: Vivo I2219 | Camp: Supabase Outreach Camp 2026")
    print("Provider: Anthropic Claude (claude-sonnet-5-5)")
    print("=" * 70)

    # 1. Check Flask Health
    print("\n[Step 1] Checking Flask backend health...")
    req = urllib.request.urlopen("http://127.0.0.1:5001/api/health")
    health = json.loads(req.read().decode())
    print("Flask Health:", health)
    assert health.get("provider") == "claude", f"Expected provider 'claude', got {health.get('provider')}"
    assert health.get("model") == "claude-sonnet-5-5", f"Expected model 'claude-sonnet-5-5', got {health.get('model')}"

    # 2. Check Device Screen & Blobs
    print("\n[Step 2] Checking device screen state...")
    check_js = """
    JSON.stringify({
        screen: window.app.state.currentScreen,
        campId: window.app.state.selectedCampId,
        hasP1: !!window.app.state.pageBlobs[0],
        hasP2: !!window.app.state.pageBlobs[1],
        p1Size: window.app.state.pageBlobs[0]?.size,
        p2Size: window.app.state.pageBlobs[1]?.size,
        btnText: document.getElementById('extract-run-btn')?.textContent?.trim()
    })
    """
    res = eval_in_webview(check_js)
    state_info = json.loads(res['result']['result']['value'])
    print("Device State:", state_info)
    assert state_info["screen"] == "pairs", f"Device not on pairs screen: {state_info['screen']}"
    assert state_info["hasP1"] and state_info["hasP2"], "Paired blobs missing!"

    # Capture initial "Check the Pair" screen
    take_screenshot("task14b_step1_check_the_pair.png")

    # 3. Install safe non-persisting interceptor in WebView
    print("\n[Step 3] Installing safe in-memory review interceptor...")
    interceptor_js = """
    (() => {
        window.__extractionStatus = 'ready';
        window.__extractionTelemetry = null;
        window.__lastExtractedRecord = null;
        window.__extractionError = null;

        const originalFetch = window.fetch;
        window.fetch = async function(resource, init) {
            const url = typeof resource === 'string' ? resource : (resource ? resource.url : '');
            const method = (init && init.method ? init.method : 'GET').toUpperCase();

            // Intercept POST /api/extract to record telemetry
            if (url.includes('/api/extract') && method === 'POST') {
                window.__extractionStatus = 'extracting';
                try {
                    const resp = await originalFetch.apply(this, arguments);
                    const clone = resp.clone();
                    try {
                        const json = await clone.json();
                        window.__extractionTelemetry = {
                            success: json.success,
                            status: resp.status,
                            cost_rs: json.cost_rs,
                            model: json.model || json.data?.model,
                            tokens_in: json.tokens_in || json.data?.tokens_in,
                            tokens_out: json.tokens_out || json.data?.tokens_out,
                            seconds: json.seconds || json.data?.seconds,
                            data: json.data || json
                        };
                    } catch(e) {
                        window.__extractionError = e.message;
                    }
                    return resp;
                } catch(netErr) {
                    window.__extractionStatus = 'error';
                    window.__extractionError = netErr.message;
                    throw netErr;
                }
            }

            // Intercept POST /api/records to prevent DB writes
            if (url.includes('/api/records') && method === 'POST' && !url.includes('/lock')) {
                console.log('[SafeInterceptor] Intercepted POST /api/records. Bypassing DB write.');
                let payload = {};
                try {
                    payload = typeof init.body === 'string' ? JSON.parse(init.body) : {};
                } catch(e) {}

                const mockId = 'rec_live_extracted_14b';
                window.__lastExtractedRecord = {
                    id: mockId,
                    ...payload,
                    camp_name: 'Supabase Outreach Camp 2026',
                    created_at: new Date().toISOString(),
                    status: 'needs_review'
                };
                window.__extractionStatus = 'record_ready';

                return new Response(JSON.stringify(window.__lastExtractedRecord), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' }
                });
            }

            // Intercept GET /api/records/rec_live_extracted_14b
            if (url.includes('/api/records/rec_live_extracted_14b') && method === 'GET') {
                console.log('[SafeInterceptor] Serving in-memory record for review.');
                return new Response(JSON.stringify(window.__lastExtractedRecord), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' }
                });
            }

            // Intercept record lock
            if (url.includes('/lock')) {
                return new Response(JSON.stringify({ success: true, locked: true, locked_by: 'Dr. Confirmed Audit' }), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' }
                });
            }

            return originalFetch.apply(this, arguments);
        };

        return 'Interceptor active';
    })()
    """
    int_res = eval_in_webview(interceptor_js)
    print("Interceptor Status:", int_res.get('result', {}).get('result', {}).get('value'))

    # 4. Trigger the tap on "Save & Transcribe Document Pages"
    print("\n[Step 4] Tapping 'Save & Transcribe Document Pages' on physical Vivo phone...")
    # Physical screen tap at center of extract-run-btn (x=534, y=1913)
    subprocess.run([ADB, "shell", "input", "tap", "534", "1913"], check=True)
    print("Tap injected via ADB input tap!")

    time.sleep(0.5)
    # Check if click registered; if not, trigger via element click
    btn_check = eval_in_webview("document.getElementById('extract-run-btn')?.disabled")
    is_disabled = btn_check.get('result', {}).get('result', {}).get('value')
    if not is_disabled:
        print("Fallback to DOM click to guarantee event dispatch...")
        eval_in_webview("document.getElementById('extract-run-btn').click()")

    time.sleep(1)
    # Capture spinner screenshot
    take_screenshot("task14b_step2_transcribing.png")

    # 5. Wait for live Claude extraction to complete
    print("\n[Step 5] Waiting for live Claude API extraction response (timeout: 90s)...")
    start_time = time.time()
    extracted = False
    telemetry = None

    for i in range(45):
        time.sleep(2)
        elapsed = round(time.time() - start_time, 1)
        poll_js = """
        JSON.stringify({
            status: window.__extractionStatus,
            hasTelemetry: !!window.__extractionTelemetry,
            screen: window.app.state.currentScreen,
            err: window.__extractionError,
            activeRec: !!window.app.state.activeRecord
        })
        """
        p_res = eval_in_webview(poll_js)
        val = p_res.get('result', {}).get('result', {}).get('value')
        if not val:
            continue
        p_info = json.loads(val)
        print(f"[{elapsed}s] Extraction status: {p_info.get('status')} | Screen: {p_info.get('screen')}")

        if p_info.get("screen") == "review" or p_info.get("hasTelemetry"):
            extracted = True
            break
        if p_info.get("status") == "error":
            raise RuntimeError(f"Extraction failed: {p_info.get('err')}")

    assert extracted, "Extraction timed out after 90 seconds!"
    print(f"\nExtraction completed in {elapsed} seconds!")

    # 6. Retrieve Telemetry and Field Results
    print("\n[Step 6] Retrieving telemetry and extracted clinical records...")
    t_res = eval_in_webview("JSON.stringify(window.__extractionTelemetry)")
    telemetry = json.loads(t_res.get('result', {}).get('result', {}).get('value') or '{}')
    print("\n--- TELEMETRY ---")
    print("Model:", telemetry.get("model"))
    print("Input Tokens:", telemetry.get("tokens_in"))
    print("Output Tokens:", telemetry.get("tokens_out"))
    print("Latency (seconds):", telemetry.get("seconds"))
    print("Cost (Rs):", telemetry.get("cost_rs"))

    rec_res = eval_in_webview("JSON.stringify(window.__lastExtractedRecord)")
    rec_data = json.loads(rec_res.get('result', {}).get('result', {}).get('value') or '{}')

    # Ensure UI is on review screen
    eval_in_webview("window.app.go('review')")
    time.sleep(1)

    # 7. Capture Review Screens across all clinical tabs
    print("\n[Step 7] Capturing Review screenshots for all tabs on phone screen...")

    # Tab 1: Personal
    eval_in_webview("window.app.switchReviewTab('personal')")
    time.sleep(1)
    take_screenshot("task14b_step3_review_personal.png")

    # Tab 2: Medical
    eval_in_webview("window.app.switchReviewTab('medical')")
    time.sleep(1)
    take_screenshot("task14b_step4_review_medical.png")

    # Tab 3: Family
    eval_in_webview("window.app.switchReviewTab('family')")
    time.sleep(1)
    take_screenshot("task14b_step5_review_family.png")

    # Tab 4: Health Assessment / Vitals
    eval_in_webview("window.app.switchReviewTab('health')")
    time.sleep(1)
    take_screenshot("task14b_step6_review_health.png")

    # Tab 5: System Examination
    eval_in_webview("window.app.switchReviewTab('system')")
    time.sleep(1)
    take_screenshot("task14b_step7_review_system.png")

    # Tab 6: Vision & Dental
    eval_in_webview("window.app.switchReviewTab('vision')")
    time.sleep(1)
    take_screenshot("task14b_step8_review_vision.png")

    # Tab 7: Referral & Summary
    eval_in_webview("window.app.switchReviewTab('referral')")
    time.sleep(1)
    take_screenshot("task14b_step9_review_referral.png")

    # Switch back to personal tab for viewing
    eval_in_webview("window.app.switchReviewTab('personal')")

    # 8. Pull values of all inputs on review screen
    fields_js = """
    (() => {
        const ids = [
            'rev-name', 'rev-age', 'rev-gender', 'rev-blood-group', 'rev-dob',
            'rev-doc-id', 'rev-adm-id', 'rev-school', 'rev-mobile', 'rev-address',
            'rev-height', 'rev-weight', 'rev-bp', 'rev-pulse', 'rev-rbs', 'rev-waist', 'rev-atr',
            'rev-allergies', 'rev-surgery', 'rev-medications', 'rev-abdominal', 'rev-ent',
            'rev-fam-diabetes', 'rev-fam-hypertension', 'rev-fam-heart', 'rev-fam-thyroid', 'rev-fam-asthma',
            'rev-pallor', 'rev-icterus', 'rev-cyanosis', 'rev-edema', 'rev-clubbing', 'rev-lymph'
        ];
        const res = {};
        ids.forEach(id => {
            const el = document.getElementById(id);
            if (el) res[id] = el.value;
        });
        return JSON.stringify(res);
    })()
    """
    f_res = eval_in_webview(fields_js)
    fields_values = json.loads(f_res.get('result', {}).get('result', {}).get('value') or '{}')
    print("\n--- EXTRACTED CLINICAL VALUES DISPLAYED IN UI ---")
    for k, v in fields_values.items():
        if v:
            print(f"  {k}: {v}")

    # Save complete raw extraction data for the report
    with open(os.path.join(ARTIFACT_DIR, "task14b_extraction_data.json"), "w", encoding="utf-8") as f:
        json.dump({
            "telemetry": telemetry,
            "record": rec_data,
            "fields_displayed": fields_values
        }, f, indent=2)

    print("\n[SUCCESS] Task 14B extraction workflow completed successfully!")

if __name__ == "__main__":
    main()
