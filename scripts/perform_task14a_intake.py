import sys
import os
import base64
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.cdp_client import eval_in_webview

def main():
    print("Executing Task 14A Steps 1-5: Paediatric Patient Intake & Scanning...")
    
    # 1. Verify selected camp
    camp_state = eval_in_webview("JSON.stringify({selectedCampId: window.app.state.selectedCampId, camps: window.app.state.camps})")
    camp_info = json.loads(camp_state['result']['result']['value'])
    print("Camp info:", camp_info)
    assert camp_info['selectedCampId'] == 'd95c06fa-5c10-4991-8afb-6071bef5b3fe', "Wrong camp selected!"
    
    # Navigate to home first
    eval_in_webview("window.app.go('home')")
    time.sleep(1)
    
    # 2. Load the official paediatric form images
    p1_path = os.path.join("testingsample", "WhatsApp Image 2026-11-17 at 10.30.53 AM (1).jpeg") # ASF-D&A-PCS-9.3
    p2_path = os.path.join("testingsample", "WhatsApp Image 2026-11-17 at 10.30.53 AM.jpeg")     # ASF-D&A-PCS-9.7
    
    with open(p1_path, "rb") as f:
        p1_b64 = base64.b64encode(f.read()).decode("utf-8")
    with open(p2_path, "rb") as f:
        p2_b64 = base64.b64encode(f.read()).decode("utf-8")
        
    print(f"Loaded Page 1 ({len(p1_b64)} chars base64) and Page 2 ({len(p2_b64)} chars base64)")
    
    # 3. Deliver scan payload via native scan bridge handler
    js_deliver = f"""
    (() => {{
        const p1 = "{p1_b64}";
        const p2 = "{p2_b64}";
        window.app.handleNativeScanMessage({{
            type: 'NATIVE_SCAN_SUCCESS',
            scanId: Date.now(),
            images: [p1, p2],
            targetPageIndex: null
        }});
        return JSON.stringify({{
            currentScreen: window.app.state.currentScreen,
            hasBlobs: [!!window.app.state.pageBlobs[0], !!window.app.state.pageBlobs[1]],
            detectedTemplates: window.app.state.detectedTemplateIds,
            formFamily: window.app.state.formFamily,
            selectedCampId: window.app.state.selectedCampId
        }});
    }})()
    """
    
    res = eval_in_webview(js_deliver)
    val = res.get('result', {}).get('result', {}).get('value')
    print("Delivery Result:", val)
    
    time.sleep(1)
    
    # Check pairs screen details
    details_js = """
    JSON.stringify({
        currentScreen: window.app.state.currentScreen,
        p1Url: !!window.app.state.pageUrls[0],
        p2Url: !!window.app.state.pageUrls[1],
        p1BlobSize: window.app.state.pageBlobs[0]?.size,
        p2BlobSize: window.app.state.pageBlobs[1]?.size,
        t1Badge: document.getElementById('pairs-t1-badge')?.textContent,
        t2Badge: document.getElementById('pairs-t2-badge')?.textContent,
        btnText: document.getElementById('extract-run-btn')?.textContent?.trim()
    })
    """
    det_res = eval_in_webview(details_js)
    print("Pairs Screen Details:", det_res.get('result', {}).get('result', {}).get('value'))

if __name__ == "__main__":
    main()
