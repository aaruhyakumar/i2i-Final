import zipfile
import xml.etree.ElementTree as ET

wb_path = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47\task17_verified_Supabase_Outreach_Camp_2026.xlsx"

with zipfile.ZipFile(wb_path, 'r') as z:
    wb_xml = z.read('xl/workbook.xml')
    tree = ET.fromstring(wb_xml)
    sheets = tree.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet')
    for s in sheets:
        print("Sheet:", s.attrib)
