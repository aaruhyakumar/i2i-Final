import pytest, io, openpyxl
from backend.services.glossary_service import ClinicalGlossaryService
from backend.services.extraction_v2 import ExtractionV2Pipeline
from backend.services.record_service import RecordService

def test_gender_variations_robustness():
    glossary = ClinicalGlossaryService()

    # Variations of Female
    for raw in ["[x] F", "[X] Female", "✓ Female", "F [x]", "Female [X]", "female", "f", "girl", "[✓] F", "F [✓]"]:
        res = glossary.normalize_text(raw, scope="gender")
        assert res["normalized"] == "Female", f"Failed for {raw}"

    # Variations of Male
    for raw in ["M [x]", "[x] M", "[X] Male", "Male [x]", "✓ Male", "M", "Male", "boy", "[✓] M", "M [✓]"]:
        res = glossary.normalize_text(raw, scope="gender")
        assert res["normalized"] == "Male", f"Failed for {raw}"

    # Multi-box combinations
    assert glossary.normalize_text("[ ] M [x] F", scope="gender")["normalized"] == "Female"
    assert glossary.normalize_text("[x] M [ ] F", scope="gender")["normalized"] == "Male"
    assert glossary.normalize_text("M [ ] F [x]", scope="gender")["normalized"] == "Female"
    assert glossary.normalize_text("M [x] F [ ]", scope="gender")["normalized"] == "Male"

    # Conflicting and empty
    assert glossary.normalize_text("[x] M [x] F", scope="gender")["status"] == "ambiguous_gender"
    assert glossary.normalize_text("[ ] M [ ] F", scope="gender")["status"] == "unmarked_checkbox"
    assert glossary.normalize_text("[ ]", scope="gender")["status"] == "unmarked_checkbox"


def test_vaccinated_for_age_checkbox_recognition():
    pipeline = ExtractionV2Pipeline()
    fld = {"key": "vaccinated_for_age", "type": "yes_no", "label": "Vaccinated for Age"}

    # Affirmative
    for raw in ["Yes", "yes", "Y", "✓", "✔", "[x]", "[X]", "[✓]", "[x] Yes", "Yes [x]", "✓ Yes", "[x] Yes [ ] No", "[ ] No [x] Yes"]:
        res = pipeline.validate_and_normalize_field(fld, {"raw_text": raw, "confidence": 0.95})
        assert res["normalized_value"] == "Yes", f"Failed for {raw}"
        assert res["status"] == "valid"

    # Negative
    for raw in ["No", "no", "N", "✗", "x", "Absent", "[x] No", "No [x]", "✓ No", "[ ] Yes [x] No", "[x] No [ ] Yes"]:
        res = pipeline.validate_and_normalize_field(fld, {"raw_text": raw, "confidence": 0.95})
        assert res["normalized_value"] == "No", f"Failed for {raw}"
        assert res["status"] == "valid"

    # Blank / Unmarked
    for raw in ["[ ]", "[  ]", "[ ] Yes [ ] No", "none", "nil"]:
        res = pipeline.validate_and_normalize_field(fld, {"raw_text": raw, "confidence": 0.95})
        assert res["status"] == "empty", f"Failed for {raw}"
        assert res["normalized_value"] is None

    # Ambiguous
    res = pipeline.validate_and_normalize_field(fld, {"raw_text": "[x] Yes [x] No", "confidence": 0.95})
    assert res["status"] == "format_warning"
    assert res["rule_applied"] == "checkbox_conflicting_needs_review"


def test_ent_four_independent_fields_and_excel_export():
    svc = RecordService()
    sample_record = {
        "id": "rec_paed_ent_001",
        "person_name": "Aarav Sharma",
        "age": 10,
        "gender": "Male",
        "target_group": "paediatric",
        "is_paediatric": 1,
        "class_and_section": "5-A",
        "height": 139.5,
        "weight": 24.8,
        # bmi not set directly -> should be derived
        "blood_pressure": "100/64",
        "spinal_caliper_atr": 3.0,
        "otoscope_right_ear": "B/L Wax",
        "otoscope_left_ear": "Normal",
        "sys_nose": "NAD",
        # sys_throat left blank -> should be "Not Recorded"
        "sys_other_systems": "Normal",
        "abdomen": "Soft, non-tender",
        "cns": "Conscious, oriented",
        "status": "reviewed"
    }

    excel_bytes = svc._generate_paediatric_excel_export(
        camp_id="camp_01",
        camp={"id": "camp_01", "name": "KV School Camp", "date_str": "2026-11-18"},
        records=[sample_record]
    )
    assert len(excel_bytes) > 0

    wb = openpyxl.load_workbook(io.BytesIO(excel_bytes))
    ws = wb["KV School Camp"]

    # Build header map
    headers = [cell.value for cell in ws[1]]
    header_map = {name: idx for idx, name in enumerate(headers, 1)}

    # Verify Class & Section
    assert "Class & Section" in header_map
    assert ws.cell(row=2, column=header_map["Class & Section"]).value == "5-A"

    # Verify BMI is derived (24.8 / (1.395)^2 = 12.75 -> 12.8 or 12.7)
    assert "BMI (kg/m²)" in header_map
    bmi_val = ws.cell(row=2, column=header_map["BMI (kg/m²)"]).value
    assert float(bmi_val) == pytest.approx(12.75, abs=0.2)

    # Verify 4 ENT independent columns
    assert "Otoscopy Right Ear" in header_map
    assert "Otoscopy Left Ear" in header_map
    assert "Nose" in header_map
    assert "Throat" in header_map

    # Check written text preserved
    assert ws.cell(row=2, column=header_map["Otoscopy Right Ear"]).value == "B/L Wax"
    # Check Normal preserved
    assert ws.cell(row=2, column=header_map["Otoscopy Left Ear"]).value == "Normal"
    # Check NAD normalized to Normal
    assert ws.cell(row=2, column=header_map["Nose"]).value == "Normal"
    # Check blank normalized to "Not Recorded"
    assert ws.cell(row=2, column=header_map["Throat"]).value == "Not Recorded"

    # Verify Abdomen & CNS preserved in other systems
    other_sys = ws.cell(row=2, column=header_map["Other Systems Examination"]).value
    assert "Abdomen:" in other_sys
    assert "CNS:" in other_sys


def test_ent_abnormal_highlighting_logic():
    # Test _is_paediatric_field_abnormal on ENT
    # 1. Blank / Not Recorded -> False (never highlight blank)
    assert RecordService._is_paediatric_field_abnormal("otoscope_right_ear", "Not Recorded", {}, {}) is False
    assert RecordService._is_paediatric_field_abnormal("otoscope_right_ear", "", {}, {}) is False
    # 2. Normal / NAD -> False
    assert RecordService._is_paediatric_field_abnormal("otoscope_right_ear", "Normal", {}, {}) is False
    assert RecordService._is_paediatric_field_abnormal("otoscope_right_ear", "NAD", {}, {}) is False
    assert RecordService._is_paediatric_field_abnormal("sys_nose", "Normal (NAD)", {}, {}) is False
    assert RecordService._is_paediatric_field_abnormal("sys_throat", "Clear", {}, {}) is False
    # 3. Abnormal -> True
    assert RecordService._is_paediatric_field_abnormal("otoscope_right_ear", "B/L Wax", {}, {}) is True
    assert RecordService._is_paediatric_field_abnormal("otoscope_left_ear", "CSOM with perforation", {}, {}) is True
    assert RecordService._is_paediatric_field_abnormal("sys_nose", "DNS right side", {}, {}) is True
    assert RecordService._is_paediatric_field_abnormal("sys_throat", "Bilateral tonsillitis", {}, {}) is True
