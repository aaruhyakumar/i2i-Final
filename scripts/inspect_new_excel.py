import zipfile
import xml.etree.ElementTree as ET
import os

wb_path = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47\task17_verified_Supabase_Outreach_Camp_2026.xlsx"

with zipfile.ZipFile(wb_path, 'r') as z:
    shared_strings = []
    if 'xl/sharedStrings.xml' in z.namelist():
        tree = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in tree.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si'):
            t = si.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')
            shared_strings.append(t.text if t is not None else "")
    
    # Read sheet1.xml
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

print("Total rows:", len(table_data))
if len(table_data) >= 2:
    header_row = table_data[0]
    data_row = table_data[1]
    print("\n--- NEW EXCEL WORKBOOK RECORD VALUES ---")
    for col, head in sorted(header_row.items()):
        val = data_row.get(col, "")
        if val != "" and val is not None:
            print(f"  Col {col} [{head}]: {repr(val)}")
        elif any(k in head.lower() for k in ["name", "gender", "birth", "dob", "blood", "height", "weight", "status"]):
            print(f"  Col {col} [{head}]: <EMPTY>")
