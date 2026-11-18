import zipfile
import xml.etree.ElementTree as ET

with zipfile.ZipFile("test_out.xlsx", 'r') as z:
    shared_strings = []
    if 'xl/sharedStrings.xml' in z.namelist():
        stree = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in stree.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si'):
            t = si.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')
            shared_strings.append(t.text if t is not None else "")

    sheet1_xml = z.read('xl/worksheets/sheet1.xml')
    s1_tree = ET.fromstring(sheet1_xml)
    rows = s1_tree.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row')
    
    header_vals = {}
    for c in rows[0].findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c'):
        ref = c.get('r')
        col_letter = ''.join(ch for ch in ref if ch.isalpha())
        t = c.get('t')
        v = c.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
        val = v.text if v is not None else ""
        if t == 's' and val.isdigit():
            val = shared_strings[int(val)]
        header_vals[col_letter] = val

    data_vals = {}
    for c in rows[1].findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c'):
        ref = c.get('r')
        col_letter = ''.join(ch for ch in ref if ch.isalpha())
        t = c.get('t')
        v = c.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
        val = v.text if v is not None else ""
        if t == 's' and val.isdigit():
            val = shared_strings[int(val)]
        data_vals[col_letter] = val

print("--- ALL NON-EMPTY EXCEL VALUES IN ROW 2 ---")
for col, head in sorted(header_vals.items()):
    val = data_vals.get(col)
    if val is not None and str(val).strip() != "":
        print(f"  Col {col} [{head}]: {repr(val)}")
