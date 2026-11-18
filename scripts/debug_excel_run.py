import os
import sys
import zipfile
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()
from backend.services.record_service import RecordService

rs = RecordService()
excel_bytes = rs.generate_excel_export(camp_id="d95c06fa-5c10-4991-8afb-6071bef5b3fe", only_reviewed=False)
test_path = "test_out.xlsx"
with open(test_path, "wb") as f:
    f.write(excel_bytes)

with zipfile.ZipFile(test_path, 'r') as z:
    wb_xml = z.read('xl/workbook.xml')
    tree = ET.fromstring(wb_xml)
    sheets = tree.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet')
    for s in sheets:
        print("Sheet:", s.attrib)

    shared_strings = []
    if 'xl/sharedStrings.xml' in z.namelist():
        stree = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in stree.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si'):
            t = si.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')
            shared_strings.append(t.text if t is not None else "")

    sheet1_xml = z.read('xl/worksheets/sheet1.xml')
    s1_tree = ET.fromstring(sheet1_xml)
    rows = s1_tree.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row')
    print("Sheet 1 rows:", len(rows))
    if len(rows) >= 2:
        for r_idx in range(min(2, len(rows))):
            row = rows[r_idx]
            vals = {}
            for c in row.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c'):
                ref = c.get('r')
                col_letter = ''.join(ch for ch in ref if ch.isalpha())
                t = c.get('t')
                v = c.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
                val = v.text if v is not None else ""
                if t == 's' and val.isdigit():
                    val = shared_strings[int(val)]
                vals[col_letter] = val
            print(f"Row {r_idx + 1}: {list(vals.items())[:15]}")
