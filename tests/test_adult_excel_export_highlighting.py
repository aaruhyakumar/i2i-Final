import io
import pytest
from backend import database
from backend.services.record_service import RecordService
from tests.test_paediatric_excel_export import parse_xlsx_workbook

@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_adult_export.db")
    database.init_db(db_file)
    return db_file

@pytest.fixture
def service(temp_db):
    return RecordService(temp_db)

@pytest.fixture
def adult_camp(temp_db):
    return database.create_camp(
        name="Corporate Health Outreach Camp 2026",
        location="Bengaluru Tech Park",
        expected_records=150,
        date_str="2026-11-18",
        target_group="adult",
        db_path=temp_db
    )

def test_adult_excel_export_alert_highlighting(service, adult_camp):
    # Create an adult record with flagged values:
    # High BP 150/95, High BMI 28.5, Tachycardia pulse 115, High RBS 180, positive allergies, defective vision 6/18, dental caries, referral
    service.create_record({
        "camp_id": adult_camp["id"],
        "person_name": "Ramesh Kumar",
        "age": "45",
        "gender": "Male",
        "height": "170",
        "weight": "82.5",  # BMI ~28.5 => Obese/Overweight
        "blood_pressure": "150/95",  # Stage 2 HTN
        "pulse": "115",  # Tachycardia
        "random_blood_sugar_mgdl": "180",  # Impaired / Diabetic
        "structured_record": {
            "personal_information": {
                "full_name": "Ramesh Kumar",
                "age": "45",
                "gender": "Male"
            },
            "medical_history": {
                "known_allergies": {"value": "Yes", "details": "Penicillin allergy (anaphylaxis)"},
                "abdominal_pain_indigestion_constipation": {"value": "No", "details": ""},
                "irregular_pcod_menopause": {"value": "No", "details": ""}
            },
            "health_assessment": {
                "height_cm": "170",
                "weight_kg": "82.5",
                "blood_pressure_mmhg": "150/95",
                "pulse_bpm": "115",
                "random_blood_sugar_mgdl": "180",
                "pallor": "Present (mild)",
                "cyanosis": "Absent",
                "edema": "Absent"
            },
            "system_examination": {
                "respiratory_system": {"normal": "No", "details": "Bilateral expiratory wheezing"},
                "cardiovascular_system": {"normal": "Yes", "details": "Normal S1 S2"}
            },
            "vision": {
                "distant_vision_right": "6/6",
                "distant_vision_left": "6/18"
            },
            "dental": {
                "dental_status": "Abnormal",
                "abnormality": "Dental caries right lower molar",
                "advice": "Dental filling advised"
            },
            "referral": {
                "specialist_referred_to": "Cardiologist & Dentist"
            }
        }
    })

    # Normal adult record
    service.create_record({
        "camp_id": adult_camp["id"],
        "person_name": "Priya Sharma",
        "age": "28",
        "gender": "Female",
        "height": "162",
        "weight": "54",  # BMI ~20.6 => Normal
        "blood_pressure": "118/76",  # Normal BP
        "pulse": "72",  # Normal pulse
        "random_blood_sugar_mgdl": "95",  # Normal RBS
        "structured_record": {
            "personal_information": {
                "full_name": "Priya Sharma",
                "age": "28",
                "gender": "Female"
            },
            "medical_history": {
                "known_allergies": {"value": "No", "details": "No known allergies"}
            },
            "health_assessment": {
                "height_cm": "162",
                "weight_kg": "54",
                "blood_pressure_mmhg": "118/76",
                "pulse_bpm": "72",
                "random_blood_sugar_mgdl": "95",
                "pallor": "Absent (NAD)",
                "cyanosis": "Absent",
                "edema": "Absent"
            },
            "system_examination": {
                "respiratory_system": {"normal": "Yes", "details": "Normal vesicular breath sounds"},
                "cardiovascular_system": {"normal": "Yes", "details": "Normal S1 S2"}
            },
            "vision": {
                "distant_vision_right": "6/6",
                "distant_vision_left": "6/6"
            },
            "dental": {
                "dental_status": "Normal",
                "advice": "Routine hygiene"
            },
            "referral": {
                "specialist_referred_to": "None"
            }
        }
    })

    xlsx_bytes = service.generate_adult_excel_export(camp_id=adult_camp["id"])
    headers, rows, _ = parse_xlsx_workbook(xlsx_bytes)

    row_abn = next(r for r in rows if r["Full Name"]["val"] == "Ramesh Kumar")
    row_norm = next(r for r in rows if r["Full Name"]["val"] == "Priya Sharma")

    assert row_abn["Full Name"]["val"] == "Ramesh Kumar"
    assert row_abn["Full Name"]["is_alert"] is False
    assert row_abn["Blood Pressure"]["is_alert"] is True
    assert row_abn["BMI"]["is_alert"] is True
    assert row_abn["Pulse"]["is_alert"] is True
    assert row_abn["RBS"]["is_alert"] is True
    assert row_abn["Any Known Allergy"]["is_alert"] is True
    assert row_abn["Pallor"]["is_alert"] is True
    assert row_abn["RS"]["is_alert"] is True
    assert row_abn["Distant Vision Left Eye"]["is_alert"] is True
    assert row_abn["Dental Status"]["is_alert"] is True
    assert row_abn["Specialist Referred To"]["is_alert"] is True

    # Normal fields in Ramesh's record should NOT be alerted
    assert row_abn["Cyanosis"]["is_alert"] is False
    assert row_abn["Edema"]["is_alert"] is False
    assert row_abn["Distant Vision Right Eye"]["is_alert"] is False

    # Second row is Priya Sharma (all normal)
    assert row_norm["Full Name"]["val"] == "Priya Sharma"
    assert row_norm["Blood Pressure"]["is_alert"] is False
    assert row_norm["BMI"]["is_alert"] is False
    assert row_norm["Pulse"]["is_alert"] is False
    assert row_norm["RBS"]["is_alert"] is False
    assert row_norm["Any Known Allergy"]["is_alert"] is False
    assert row_norm["Pallor"]["is_alert"] is False
    assert row_norm["Cyanosis"]["is_alert"] is False
    assert row_norm["Distant Vision Right Eye"]["is_alert"] is False
    assert row_norm["Distant Vision Left Eye"]["is_alert"] is False
    assert row_norm["Dental Status"]["is_alert"] is False
    assert row_norm["Specialist Referred To"]["is_alert"] is False
