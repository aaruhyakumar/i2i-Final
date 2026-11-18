import zipfile
import xml.etree.ElementTree as ET

wb_path = "downloaded_Supabase_Outreach_Camp_2026_Health_Records.xlsx"

with zipfile.ZipFile(wb_path, 'r') as z:
    # 1. Read shared strings
    shared_strings = []
    if 'xl/sharedStrings.xml' in z.namelist():
        tree = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in tree.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si'):
            t = si.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')
            shared_strings.append(t.text if t is not None else "")
    
    # 2. Read sheet 1
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
    print("\n--- ROW 2 (PATIENT RECORD) VALUES ---")
    for col, head in sorted(header_row.items()):
        val = data_row.get(col, "")
        if val != "":
            print(f"Col {col} [{head}]: {val}")
        else:
            # Check if this is one of our key fields
            if any(k in head.lower() for k in ["name", "gender", "birth", "dob", "age", "blood", "height", "weight", "vision", "wax", "ear", "pulse", "bp"]):
                print(f"Col {col} [{head}]: <BLANK>")
