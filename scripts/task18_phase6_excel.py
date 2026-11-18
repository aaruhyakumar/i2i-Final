import os
import sys
import zipfile
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from backend.services.record_service import RecordService

ARTIFACT_DIR = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47"
camp_id = "d95c06fa-5c10-4991-8afb-6071bef5b3fe"

print("=== PHASE 6: EXCEL EXPORT REGRESSION VERIFICATION ===")

# 1. Generate fresh export
print("\n[Step 1] Generating fresh camp Excel workbook from backend...")
rs = RecordService()
excel_bytes = rs.generate_excel_export(camp_id=camp_id, only_reviewed=False)
export_path = os.path.join(ARTIFACT_DIR, "task18_fresh_camp_export.xlsx")
with open(export_path, "wb") as f:
    f.write(excel_bytes)
print(f"Generated workbook: {export_path} ({len(excel_bytes)} bytes)")

# 2. Inspect Workbook Sheets
print("\n[Step 2] Inspecting workbook sheets...")
with zipfile.ZipFile(export_path, 'r') as z:
    wb_tree = ET.fromstring(z.read('xl/workbook.xml'))
    sheets = [s.attrib['name'] for s in wb_tree.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet')]
    print("Sheets in workbook:", sheets)
    assert sheets[0] == "Supabase Outreach Camp 2026", f"Expected paediatric camp sheet, got {sheets[0]}"
    assert sheets[1] == "Form Attributes", f"Expected Form Attributes, got {sheets[1]}"
    assert sheets[2] == "Paediatric Summary", f"Expected Paediatric Summary, got {sheets[2]}"
    print("Worksheet selection: PASS (Paediatric Health Screening format)")

    # Read shared strings
    shared_strings = []
    if 'xl/sharedStrings.xml' in z.namelist():
        stree = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in stree.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si'):
            t = si.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')
            shared_strings.append(t.text if t is not None else "")

    # Read Sheet 1
    s1_tree = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
    rows = s1_tree.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row')
    print(f"\n[Step 3] Sheet 1 total rows: {len(rows)} (1 header + {len(rows)-1} data rows)")
    assert len(rows) == 2, f"Expected exactly 1 data row, got {len(rows)-1}"

    # Parse headers
    headers = {}
    for c in rows[0].findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c'):
        ref = c.get('r')
        col = ''.join(ch for ch in ref if ch.isalpha())
        t = c.get('t')
        v = c.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
        val = v.text if v is not None else ""
        if t == 's' and val.isdigit(): val = shared_strings[int(val)]
        headers[col] = val

    # Parse Row 2
    row2 = {}
    for c in rows[1].findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c'):
        ref = c.get('r')
        col = ''.join(ch for ch in ref if ch.isalpha())
        t = c.get('t')
        v = c.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
        val = v.text if v is not None else ""
        if t == 's' and val.isdigit(): val = shared_strings[int(val)]
        row2[col] = val

print("\n--- POPULATED EXCEL CELLS IN ROW 2 ---")
for col, head in sorted(headers.items()):
    val = row2.get(col)
    if val is not None and str(val).strip() != "":
        print(f"  Col {col} [{head}]: {repr(val)}")

# Verification assertions
print("\n[Step 4] Checking field assertions...")
rec_id_val = row2.get('A')
name_val = row2.get('F')
dob_val = row2.get('H')
gender_val = row2.get('J')
bg_val = row2.get('K')
ht_val = row2.get('AC')
wt_val = row2.get('AD')
bp_val = row2.get('AG')
pulse_val = row2.get('AH')
age_val = row2.get('I')

print("  Record ID:", rec_id_val)
assert rec_id_val == "rec_20261117134632_f32f41"

print("  Student Name:", name_val)
assert name_val == "B. SADHANA"

print("  Date of Birth:", dob_val)
assert dob_val == "08/07/2013", f"Expected 08/07/2013, got {dob_val}"

print("  Gender:", gender_val)
assert gender_val == "Female"

print("  Blood Group:", bg_val)
assert bg_val == "O+ve", f"Expected O+ve, got {bg_val}"

print("  Height (cm):", ht_val)
assert ht_val == "154.4"

print("  Weight (kg):", wt_val)
assert wt_val == "35.7"

print("  Unrecorded BP in Excel:", repr(bp_val))
assert bp_val is None, "BP should not be invented"

print("  Unrecorded Pulse in Excel:", repr(pulse_val))
assert pulse_val is None, "Pulse should not be invented"

print("  Unrecorded Age in Excel:", repr(age_val))
assert age_val is None, "Age should not be invented"

print("\n[PHASE 6 COMPLETE] Fresh Excel export verified 100% against all criteria!")
