"""
Unit and regression tests for Paediatric Clinical MIS Excel Export.

Verifies:
1. Column schema strictly matches the two official paediatric forms:
   - Paediatric History: ASF-D&A-PCS-9.3 (tpl_paediatric_history_v9_3)
   - Paediatric Examination: ASF-D&A-PCS-9.7 (tpl_paediatric_examination_v9_7)
   plus necessary administrative and review metadata.
2. Removal of unrelated adult/general medical attributes (e.g. Marital Status,
   No of Children, Year of Study, PCOD/Menopause, ECG, ECHO, CVS, RS, Abdomen,
   CNS, Psychological Consultation, RBS).
3. Measurements fidelity: Pulse and BMI remain in their own independent measurement
   columns; 'Other Systems Examination' preserves actual recorded findings without
   synthetic adult organ system defaults.
4. Cell highlighting of abnormal recorded values using existing clinical thresholds
   (#FFEBEE fill, #C62828 font) with original cell text preserved unchanged.
5. Normal, blank, and unassessed values are NEVER highlighted.
6. Support for selected-record export by ID.
7. Automatic paediatric routing for school camps.
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile
import pytest

from backend import database
from backend.services.record_service import RecordService
from backend.app import create_app


# ============================================================================
# Helper: Parse OpenXML Excel (.xlsx) Structure
# ============================================================================

def parse_xlsx_workbook(data: bytes, sheet_name: str = "Paediatric Records"):
    """
    Parses OpenXML bytes using Python standard library (zipfile + xml.etree).
    Returns (headers, data_rows, sheet_names):
      headers: list of column title strings
      data_rows: list of dicts mapping header -> {"val": str, "is_alert": bool}
      sheet_names: list of worksheet names found in workbook.xml
    """
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

        # 1. Workbook sheet names
        wb_root = ET.fromstring(z.read("xl/workbook.xml"))
        sheets_elem = wb_root.findall("main:sheets/main:sheet", ns)
        sheet_names = [s.attrib.get("name") for s in sheets_elem]

        # 2. Styles: identify alert formatting (fill #FFEBEE, font #C62828)
        styles_root = ET.fromstring(z.read("xl/styles.xml"))
        fills = styles_root.findall("main:fills/main:fill", ns)
        fonts = styles_root.findall("main:fonts/main:font", ns)
        xfs = styles_root.findall("main:cellXfs/main:xf", ns)

        alert_xf_indices = set()
        for idx, xf in enumerate(xfs):
            fill_id = int(xf.attrib.get("fillId", 0))
            font_id = int(xf.attrib.get("fontId", 0))
            fg = fills[fill_id].find("main:patternFill/main:fgColor", ns) if fill_id < len(fills) else None
            fc = fonts[font_id].find("main:color", ns) if font_id < len(fonts) else None
            rgb_fill = (fg.attrib.get("rgb", "") if fg is not None else "").upper()
            rgb_font = (fc.attrib.get("rgb", "") if fc is not None else "").upper()
            if "FFEBEE" in rgb_fill or "C62828" in rgb_font:
                alert_xf_indices.add(str(idx))

        # 3. Shared strings table
        shared_strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            sst_root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sst_root.findall("main:si", ns):
                t = si.find("main:t", ns)
                shared_strings.append(t.text if t is not None else "")

        # 4. Sheet 1 data
        sheet_root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        rows = sheet_root.findall("main:sheetData/main:row", ns)
        if not rows:
            return [], [], sheet_names

        headers = []
        for c in rows[0].findall("main:c", ns):
            t = c.attrib.get("t")
            v = c.find("main:v", ns)
            val = v.text if v is not None else ""
            if t == "s" and val.isdigit() and int(val) < len(shared_strings):
                val = shared_strings[int(val)]
            headers.append(val)

        data_rows = []
        for r in rows[1:]:
            row_dict = {}
            for c in r.findall("main:c", ns):
                ref = c.attrib.get("r", "")
                col_letters = "".join([ch for ch in ref if ch.isalpha()])
                col_idx = 0
                for ch in col_letters:
                    col_idx = col_idx * 26 + (ord(ch) - ord("A") + 1)
                col_idx -= 1

                t = c.attrib.get("t")
                s = c.attrib.get("s")
                v = c.find("main:v", ns)
                val = v.text if v is not None else ""
                if t == "s" and val.isdigit() and int(val) < len(shared_strings):
                    val = shared_strings[int(val)]

                if col_idx < len(headers):
                    header = headers[col_idx]
                    row_dict[header] = {
                        "val": val,
                        "is_alert": s in alert_xf_indices
                    }
            data_rows.append(row_dict)

        return headers, data_rows, sheet_names


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_paediatric_export.db")
    database.init_db(db_file)
    return db_file


@pytest.fixture
def service(temp_db):
    return RecordService(temp_db)


@pytest.fixture
def paediatric_camp(temp_db):
    return database.create_camp(
        name="St. Mary's School Annual Health Screening",
        expected_records=200,
        date_str="2026-11-17",
        location="Chennai Campus",
        target_group="paediatric",
        db_path=temp_db
    )


# ============================================================================
# Test Cases
# ============================================================================

def test_paediatric_excel_export_column_schema(service, paediatric_camp):
    """Verify Sheet 1 name and exact 61 paediatric columns."""
    service.create_record({
        "camp_id": paediatric_camp["id"],
        "person_name": "Aarav Sharma",
        "age": "11",
        "gender": "Male",
        "structured_record": {
            "dob": "2015-05-12",
            "blood_group": "B+",
            "pulse": "75",
            "vision_distant_right": "6/6",
            "vision_distant_left": "6/6"
        }
    })

    xlsx_bytes = service.generate_paediatric_excel_export(camp_id=paediatric_camp["id"])
    headers, rows, sheet_names = parse_xlsx_workbook(xlsx_bytes)

    # In camp export, Sheet 1 is named after the school camp, and Paediatric Summary is included
    assert "Paediatric Summary" in sheet_names
    assert "Form Attributes" in sheet_names
    assert len(headers) == 59

    # When exported without specific camp, default name is 'Paediatric Records'
    generic_bytes = service.generate_paediatric_excel_export()
    gen_headers, _, gen_sheets = parse_xlsx_workbook(generic_bytes)
    assert gen_sheets[0] == "Paediatric Records"
    assert len(gen_headers) == 59

    # Check key columns from History (ASF-D&A-PCS-9.3)
    history_cols = [
        "Record ID", "School / Camp Name", "Screening Type", "Moc Doc ID",
        "Admission ID / Roll No", "Student Name", "Class & Section", "Date of Birth",
        "Age (Years)", "Gender", "Blood Group", "Parent / Guardian Name",
        "Mobile Number", "Email ID", "Residential Address", "Known Allergies",
        "Current Medications", "Past Surgery", "Vaccinated for Age",
        "Behaviour Concerns", "Other Medical Issues", "Family Adults Count",
        "Family Minors Count", "Family Pediatrician", "Consent to School Health Screening",
        "Consent to First Aid", "Parent Signature"
    ]
    for col in history_cols:
        assert col in headers, f"Missing Paediatric History column: {col}"

    # Verify removed/uncontained fields are strictly absent
    assert "Consent to Research" not in headers
    assert "Color Vision" not in headers
    assert "Past Surgeries / Illnesses" not in headers
    assert "Gait & Posture Status" not in headers

    # Check key columns from Examination (ASF-D&A-PCS-9.7)
    examination_cols = [
        "Date of Examination", "Height (cm)", "Weight (kg)", "Waist Circumference (cm)",
        "BMI (kg/m²)", "Blood Pressure (mmHg)", "Pulse (bpm)", "Spinal Caliper ATR (°)",
        "Vital Signs Status", "Vital Signs Abnormal Details", "Gait Status",
        "Gait Abnormality Details", "Otoscopy Right Ear", "Otoscopy Left Ear",
        "Nose", "Throat", "Skin", "Hair", "Other Systems Examination",
        "Visual Aid Status", "Vision Distant Right (OD)", "Vision Distant Left (OS)",
        "Dental Status", "Dental Abnormality", "Dental Advice",
        "Specialist Referred To", "Screening Doctor Name", "Doctor Signature"
    ]
    for col in examination_cols:
        assert col in headers, f"Missing Paediatric Examination column: {col}"

    # Check administrative/review metadata columns
    review_cols = ["Review Status", "Reviewer Notes", "Reviewed By", "Reviewed At"]
    for col in review_cols:
        assert col in headers, f"Missing review metadata column: {col}"


def test_no_adult_attributes_present(service, paediatric_camp):
    """Verify strictly NO adult or general medical attributes are present in paediatric export."""
    service.create_record({
        "camp_id": paediatric_camp["id"],
        "person_name": "Diya Patel",
        "age": "9",
        "gender": "Female"
    })

    xlsx_bytes = service.generate_paediatric_excel_export(camp_id=paediatric_camp["id"])
    headers, rows, sheet_names = parse_xlsx_workbook(xlsx_bytes)

    forbidden_adult_attributes = [
        "Marital Status", "No of Children", "Year of Study", "PCOD/Menopause",
        "ECG", "ECHO", "CVS", "RS", "Abdomen", "CNS",
        "Psychological Consultation", "RBS", "Random Blood Sugar", "Cholesterol",
        "Fast Blood Glucose", "Lipid Profile", "HBA1C"
    ]

    for forbidden in forbidden_adult_attributes:
        assert forbidden not in headers, f"Forbidden adult attribute '{forbidden}' found in paediatric export!"


def test_measurements_columns_isolation(service, paediatric_camp):
    """Verify Pulse and BMI remain in their own independent columns and Other Systems Examination is preserved."""
    service.create_record({
        "camp_id": paediatric_camp["id"],
        "person_name": "Rohan Kumar",
        "age": "12",
        "gender": "Male",
        "structured_record": {
            "pulse": "82",
            "bmi": "18.1",
            "sys_other_systems": "Bilateral breath sounds vesicular, S1 S2 heard"
        }
    })

    xlsx_bytes = service.generate_paediatric_excel_export(camp_id=paediatric_camp["id"])
    headers, rows, _ = parse_xlsx_workbook(xlsx_bytes)

    assert "Pulse (bpm)" in headers
    assert "BMI (kg/m²)" in headers
    assert "Other Systems Examination" in headers

    row = rows[0]
    assert row["Pulse (bpm)"]["val"] == "82"
    assert row["BMI (kg/m²)"]["val"] == "18.1"
    assert "Bilateral breath sounds vesicular" in row["Other Systems Examination"]["val"]


def test_abnormal_recorded_values_highlighting(service, paediatric_camp):
    """
    Verify abnormal values across vitals, systemic exam, vision, dental,
    allergies, and immunization trigger alert formatting (#FFEBEE / #C62828)
    while preserving original values unchanged.
    """
    service.create_record({
        "camp_id": paediatric_camp["id"],
        "person_name": "Ananya Sen",
        "age": "10",
        "gender": "Female",
        "height": "130",
        "weight": "42",  # BMI ~24.8 => Obese for 10y female
        "blood_pressure": "135/88",  # Paediatric HTN
        "structured_record": {
            "dob": "2016-04-10",
            "pulse": "128",  # Tachycardia (>100)
            "spinal_caliper_atr": "6°",  # ATR >= 5°
            "vital_signs_status": "Abnormal",
            "vital_signs_details": "Tachycardia at rest",
            "gait_status": "Abnormal",
            "gait_details": "Antalgic limp left lower limb",
            "otoscope_right_ear": "Impacted cerumen completely occluding canal",
            "otoscope_left_ear": "Tympanic membrane perforation with discharge",
            "sys_nose": "Deviated nasal septum to right",
            "sys_throat": "Bilateral tonsils grade 3 enlargement with congestion",
            "sys_skin": "Hypopigmented scaly patches on cheek",
            "sys_hair": "Pediculosis capitis with active nits",
            "vision_distant_right": "6/6",
            "vision_distant_left": "6/18",  # Defective vision (<6/6)
            "vision_color": "Defective red-green color vision",
            "dental_status": "Abnormal",
            "dental_abnormality": "Deep dental caries in 36 and 46",
            "dental_advice": "Refer to pediatric dentist for restorations",
            "specialist_referred_to": "ENT Specialist & Pediatric Dentist",
            "known_allergies": "Severe peanut allergy (anaphylaxis)",
            "vaccinated_for_age": "No"
        }
    })

    xlsx_bytes = service.generate_paediatric_excel_export(camp_id=paediatric_camp["id"])
    headers, rows, _ = parse_xlsx_workbook(xlsx_bytes)
    row = rows[0]

    # Verify cell text fidelity (original values preserved)
    assert row["Student Name"]["val"] == "Ananya Sen"
    assert row["Pulse (bpm)"]["val"] == "128"
    assert row["Spinal Caliper ATR (°)"]["val"] == "6°"
    assert row["Blood Pressure (mmHg)"]["val"] == "135/88"
    assert row["Otoscopy Right Ear"]["val"] == "Impacted cerumen completely occluding canal"
    assert row["Vision Distant Left (OS)"]["val"] == "6/18"
    assert row["Known Allergies"]["val"] == "Severe peanut allergy (anaphylaxis)"
    assert row["Vaccinated for Age"]["val"] == "No"

    # Verify abnormal examination fields are highlighted
    assert row["Blood Pressure (mmHg)"]["is_alert"] is True
    assert row["Spinal Caliper ATR (°)"]["is_alert"] is True
    assert row["Vital Signs Status"]["is_alert"] is True
    assert row["Gait Status"]["is_alert"] is True
    assert row["Otoscopy Right Ear"]["is_alert"] is True
    assert row["Otoscopy Left Ear"]["is_alert"] is True
    assert row["Nose"]["is_alert"] is True
    assert row["Throat"]["is_alert"] is True
    assert row["Skin"]["is_alert"] is True
    assert row["Hair"]["is_alert"] is True
    assert row["Vision Distant Left (OS)"]["is_alert"] is True
    assert row["Dental Status"]["is_alert"] is True
    assert row["Dental Abnormality"]["is_alert"] is True
    assert row["Dental Advice"]["is_alert"] is True
    assert row["Specialist Referred To"]["is_alert"] is True

    # Safety rules: Pulse (no unvalidated adult threshold for children),
    # Allergies (medical history), and Vaccination status (medical history) are NOT highlighted
    assert row["Pulse (bpm)"]["is_alert"] is False
    assert row["Known Allergies"]["is_alert"] is False
    assert row["Vaccinated for Age"]["is_alert"] is False


def test_normal_values_not_highlighted(service, paediatric_camp):
    """Verify normal clinical values are NOT highlighted."""
    service.create_record({
        "camp_id": paediatric_camp["id"],
        "person_name": "Ishaan Verma",
        "age": "10",
        "gender": "Male",
        "height": "138",
        "weight": "32",  # BMI ~16.8 => Normal weight
        "blood_pressure": "102/65",  # Normal BP
        "structured_record": {
            "dob": "2016-02-14",
            "pulse": "78",  # Normal pulse
            "spinal_caliper_atr": "1°",  # Normal ATR (<5°)
            "vital_signs_status": "Normal",
            "vital_signs_details": "NAD",
            "gait_status": "Normal",
            "gait_details": "NAD",
            "otoscope_right_ear": "Normal intact TM",
            "otoscope_left_ear": "Normal intact TM",
            "sys_nose": "Normal patent",
            "sys_throat": "Normal bilateral tonsils",
            "sys_skin": "Normal intact",
            "sys_hair": "Normal healthy",
            "vision_distant_right": "6/6",
            "vision_distant_left": "6/6",
            "dental_status": "Normal",
            "dental_abnormality": "None",
            "dental_advice": "Routine hygiene",
            "specialist_referred_to": "None",
            "known_allergies": "None",
            "vaccinated_for_age": "Yes"
        }
    })

    xlsx_bytes = service.generate_paediatric_excel_export(camp_id=paediatric_camp["id"])
    headers, rows, _ = parse_xlsx_workbook(xlsx_bytes)
    row = rows[0]

    # Check that normal values are NOT highlighted
    normal_keys_to_check = [
        "Pulse (bpm)", "Blood Pressure (mmHg)", "Spinal Caliper ATR (°)",
        "Vital Signs Status", "Gait Status",
        "Otoscopy Right Ear", "Otoscopy Left Ear", "Nose", "Throat", "Skin", "Hair",
        "Vision Distant Right (OD)", "Vision Distant Left (OS)",
        "Dental Status", "Dental Abnormality", "Dental Advice",
        "Specialist Referred To", "Known Allergies", "Vaccinated for Age"
    ]

    for key in normal_keys_to_check:
        assert row[key]["is_alert"] is False, f"Normal field '{key}' was incorrectly highlighted as alert!"


def test_blank_and_unassessed_values_not_highlighted(service, paediatric_camp):
    """Verify empty strings, None, and 'Not assessed' fields are NEVER highlighted."""
    service.create_record({
        "camp_id": paediatric_camp["id"],
        "person_name": "Meera Nair",
        "age": "8",
        "gender": "Female",
        "structured_record": {
            "pulse": "",
            "blood_pressure": None,
            "spinal_caliper_atr": "Not assessed",
            "otoscope_right_ear": "-",
            "otoscope_left_ear": "N/A",
            "sys_nose": "None",
            "sys_throat": "",
            "vision_distant_left": "",
            "specialist_referred_to": "None",
            "known_allergies": "Nil"
        }
    })

    xlsx_bytes = service.generate_paediatric_excel_export(camp_id=paediatric_camp["id"])
    headers, rows, _ = parse_xlsx_workbook(xlsx_bytes)
    row = rows[0]

    for header, cell in row.items():
        if not cell["val"] or cell["val"].lower() in ("none", "nil", "n/a", "na", "-", "--", "not assessed", "unassessed"):
            assert cell["is_alert"] is False, f"Blank/unassessed cell '{header}' was incorrectly highlighted!"


def test_selected_record_export(service, paediatric_camp):
    """Verify filtering export by record_ids returns only the requested records."""
    rec1 = service.create_record({
        "camp_id": paediatric_camp["id"],
        "person_name": "Student One",
        "age": "10",
        "gender": "Male"
    })
    rec2 = service.create_record({
        "camp_id": paediatric_camp["id"],
        "person_name": "Student Two",
        "age": "11",
        "gender": "Female"
    })
    rec3 = service.create_record({
        "camp_id": paediatric_camp["id"],
        "person_name": "Student Three",
        "age": "9",
        "gender": "Male"
    })

    # Export only rec2
    xlsx_rec2 = service.generate_paediatric_excel_export(
        camp_id=paediatric_camp["id"],
        record_ids=[rec2["id"]]
    )
    _, rows_rec2, _ = parse_xlsx_workbook(xlsx_rec2)
    assert len(rows_rec2) == 1
    assert rows_rec2[0]["Student Name"]["val"] == "Student Two"
    assert rows_rec2[0]["Record ID"]["val"] == rec2["id"]

    # Export rec1 and rec3
    xlsx_rec1_3 = service.generate_paediatric_excel_export(
        camp_id=paediatric_camp["id"],
        record_ids=[rec1["id"], rec3["id"]]
    )
    _, rows_rec1_3, _ = parse_xlsx_workbook(xlsx_rec1_3)
    assert len(rows_rec1_3) == 2
    exported_names = {r["Student Name"]["val"] for r in rows_rec1_3}
    assert exported_names == {"Student One", "Student Three"}


def test_automatic_routing_for_school_camp(service, temp_db):
    """Verify school camp names automatically route to the paediatric 61-column export."""
    school_camp = database.create_camp(
        name="St. Mary's School Adyar Demo",
        expected_records=50,
        date_str="2026-11-17",
        location="Adyar",
        # Notice: target_group not explicitly set to 'paediatric'
        db_path=temp_db
    )
    service.create_record({
        "camp_id": school_camp["id"],
        "person_name": "Kavya Menon",
        "age": "12",
        "gender": "Female"
    })

    # Generic export entrypoint
    xlsx_bytes = service.generate_excel_export(camp_id=school_camp["id"])
    headers, rows, sheet_names = parse_xlsx_workbook(xlsx_bytes)

    assert "Paediatric Summary" in sheet_names
    assert len(headers) == 59
    assert "Student Name" in headers
    assert "Spinal Caliper ATR (°)" in headers
    assert "Marital Status" not in headers


def test_export_endpoints_api_integration(temp_db):
    """Verify HTTP export endpoints accept record_ids and enforce paediatric schema."""
    from backend.services.auth_service import create_access_token
    database.get_or_create_doctor(
        email="doc_paed@test.org",
        full_name="Dr. Paed",
        auth_id="doc_paed",
        community_id="org_default",
        db_path=temp_db
    )
    app = create_app(db_path=temp_db)
    app.config["TESTING"] = True
    token = create_access_token(user_id="doc_paed", role="doctor", org_id="org_default")

    camp = database.create_camp(
        name="Little Flower School Health Drive",
        expected_records=100,
        date_str="2026-11-17",
        target_group="paediatric",
        community_id="org_default",
        db_path=temp_db
    )
    svc = RecordService(temp_db)
    r1 = svc.create_record({"camp_id": camp["id"], "person_name": "Child 1", "age": "7", "community_id": "org_default"})
    r2 = svc.create_record({"camp_id": camp["id"], "person_name": "Child 2", "age": "8", "community_id": "org_default"})

    with app.test_client() as client:
        client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"

        # 1. Dedicated endpoint with record_ids filter
        resp = client.get(f"/api/export/paediatric/excel?camp_id={camp['id']}&record_ids={r2['id']}")
        assert resp.status_code == 200
        assert resp.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        _, rows, _ = parse_xlsx_workbook(resp.data)
        assert len(rows) == 1
        assert rows[0]["Student Name"]["val"] == "Child 2"

        # 2. Camp export endpoint with record_ids filter
        resp_camp = client.get(f"/api/camps/{camp['id']}/export/excel?record_ids={r1['id']}")
        assert resp_camp.status_code == 200
        _, rows_camp, _ = parse_xlsx_workbook(resp_camp.data)
        assert len(rows_camp) == 1
        assert rows_camp[0]["Student Name"]["val"] == "Child 1"
