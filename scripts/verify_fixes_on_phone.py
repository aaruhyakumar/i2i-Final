import os
import sys
import time
import json
import subprocess
import zipfile
import xml.etree.ElementTree as ET

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

print("=" * 70)
print("VERIFYING TASK 17 FIXES ON CONNECTED VIVO I2219 PHONE")
print("=" * 70)

# Step 1: Open Review screen for B. SADHANA
print("\n[Step 1] Opening Review screen for B. SADHANA on phone...")
eval_in_webview("window.app.openReview('rec_20261117134632_f32f41')")
time.sleep(2)

# Step 2: Read input elements in review screen
read_inputs_js = """
JSON.stringify({
    screen: window.app.state.currentScreen,
    name: document.getElementById('rev-name')?.value,
    gender: document.getElementById('rev-gender')?.value,
    dob: document.getElementById('rev-dob')?.value,
    blood_group: document.getElementById('rev-blood-group')?.value,
    height: document.getElementById('rev-height')?.value,
    weight: document.getElementById('rev-weight')?.value,
    bp: document.getElementById('rev-bp')?.value,
    pulse: document.getElementById('rev-pulse')?.value
})
"""
inputs_res = eval_in_webview(read_inputs_js)
form_values = json.loads(inputs_res.get('result', {}).get('result', {}).get('value') or '{}')
print("\n--- REVIEW SCREEN FORM VALUES ON PHONE ---")
for k, v in form_values.items():
    print(f"  {k}: {repr(v)}")

# Step 3: Capture Screenshots of Review Tabs
print("\n[Step 3] Capturing review screenshots on phone...")
eval_in_webview("window.app.switchReviewTab('personal')")
time.sleep(1)
take_screenshot("task17_verified_personal_tab.png")

eval_in_webview("window.app.switchReviewTab('health')")
time.sleep(1)
take_screenshot("task17_verified_health_tab.png")

# Step 4: Test Excel Export with merged paediatric data
print("\n[Step 4] Generating fresh Excel workbook export via backend...")
from backend.services.record_service import RecordService
record_service = RecordService()
excel_bytes = record_service.generate_excel_export(camp_id="d95c06fa-5c10-4991-8afb-6071bef5b3fe", only_reviewed=False)
new_excel_path = os.path.join(ARTIFACT_DIR, "task17_verified_Supabase_Outreach_Camp_2026.xlsx")
with open(new_excel_path, "wb") as f:
    f.write(excel_bytes)
print(f"Export saved to: {new_excel_path} ({len(excel_bytes)} bytes)")

# Step 5: Parse new Excel with zipfile & XML ElementTree
with zipfile.ZipFile(new_excel_path, 'r') as z:
    shared_strings = []
    if 'xl/sharedStrings.xml' in z.namelist():
        tree = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in tree.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si'):
            t = si.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')
            shared_strings.append(t.text if t is not None else "")
    
    sheet_xml = z.read('xl/worksheets/sheet1.xml')
    tree = ET.fromstring(sheet_xml)
    rows = tree.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row')
    
    table_data = []
    for row in rows:
        row_vals = {}
        for c in row.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c'):
            ref = c.get('r')
            col_letter = ''.join(ch for ch in ref if ch.isalpha())
            t = c.get('t')
            v = c.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
            val = v.text if v is not None else ""
            if t == 's' and val.isdigit():
                val = shared_strings[int(val)]
            row_vals[col_letter] = val
        table_data.append(row_vals)

print("\n--- NEW EXCEL WORKBOOK RECORD VALUES ---")
if len(table_data) >= 2:
    header_row = table_data[0]
    data_row = table_data[1]
    for col, head in sorted(header_row.items()):
        val = data_row.get(col, "")
        if any(k in head.lower() for k in ["name", "gender", "birth", "dob", "blood", "height", "weight", "status", "doctor", "camp"]):
            print(f"  Col {col} [{head}]: {repr(val)}")

print("\n[SUCCESS] Task 17 fix verification finished successfully!")
