"""Tests for Stage 1: Claude Paediatric Extraction Reliability.

Validates:
1. Cross-page field coalescence: Page 1 demographics are never overwritten by empty Page 2 fields.
2. Authority partitions: Page 2 is authoritative for clinical examination and growth fields.
3. Conflict detection: Non-matching cross-page values are flagged with conflict_warning, not silently merged.
4. Checkbox, tick, stroke and bracket recognition normalization.
5. Snellen visual acuity pre-printed '6/' denominator reconstruction.
6. Spinal caliper ATR degree parsing, 'Nil'/'NAD' handling, and ambiguous field protection (never forced to 0).
7. Strict numerical fidelity: no silent decimal shifts or digit alterations.
8. Clinical glossary scoped abbreviation expansion for camp case sheets.
9. PAEDIATRIC_EXTRACTION_PROMPT clinical rule completeness.
10. End-to-end integration with paediatric screening alerts.
"""

import pytest
import io
from unittest.mock import patch
from PIL import Image, ImageDraw

from backend.services.ai_service import (
    PAEDIATRIC_EXTRACTION_PROMPT,
    merge_multipage_normalized_records,
    extract as ai_extract,
)
from backend.services.extraction_v2 import (
    default_extraction_pipeline,
    SNELLEN_VALUES,
)
from backend.services.glossary_service import default_glossary_service
from backend.services.paediatric_screening import evaluate_paediatric_record
from backend import database


def _make_test_image(text: str) -> bytes:
    """Create a minimal JPEG image with footer text for classification."""
    img = Image.new("RGB", (600, 800), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), "Apollo Shine Paediatric Screening", fill=(0, 0, 0))
    draw.text((20, 760), text, fill=(0, 0, 0))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


# ==============================================================================
# 1. Cross-Page Coalescence: Page 1 Demographics Protected Against Empty Page 2
# ==============================================================================

def test_cross_page_empty_page2_preserves_page1_demographics():
    """Empty or unread Page 2 fields must NEVER overwrite populated Page 1 demographic fields."""
    raw_extracted = {
        # Page 1 Demographics
        "person_name": ["Aaditya Patel", 0.98],
        "dob": ["12/05/2014", 0.95],
        "age": ["10", 0.95],
        "gender": ["Male", 0.98],
        "blood_group": ["B+ve", 0.90],
        "parent_name": ["Ramesh Patel", 0.95],
        "mobile_number": ["9876543210", 0.95],
        "email": ["ramesh@example.com", 0.90],
        "address": ["12 Gandhi St, Chennai", 0.90],
        "school_name": ["Delhi Public School", 0.98],
        "class_and_section": ["5-B", 0.95],
        "admission_id": ["DPS-2024-101", 0.95],
        "screening_type": ["General Health", 0.95],
        # Page 2 Examination
        "height": ["142.5", 0.92],
        "weight": ["36.2", 0.92],
        "blood_pressure": ["100/60", 0.90],
        "spinal_caliper_atr": ["5", 0.88],
        "doctor_name": ["Dr. Ananya Ray", 0.95],
        "doctor_signature": ["Signed", 0.95],
    }

    # Simulate Page 1 normalization
    p1_res = default_extraction_pipeline.process_extracted_payload(
        "tpl_paediatric_history_v9_3", raw_extracted
    )
    # Simulate Page 2 normalization where person_name is BLANK on Page 2
    raw_p2_blank_name = dict(raw_extracted)
    raw_p2_blank_name["person_name"] = ["", 0.1]
    p2_res = default_extraction_pipeline.process_extracted_payload(
        "tpl_paediatric_examination_v9_7", raw_p2_blank_name
    )

    merged = merge_multipage_normalized_records([p1_res, p2_res])
    fields = merged["fields"]

    # Assert Page 1 demographics survived intact
    assert "person_name" in fields
    assert fields["person_name"]["normalized_value"] == "Aaditya Patel"
    assert fields["person_name"]["source_page"] == 1
    assert fields["person_name"]["status"] == "valid"

    # Assert Page 2 examination values are present
    assert fields["height"]["normalized_value"] == "142.5"
    assert fields["height"]["source_page"] == 2
    assert fields["spinal_caliper_atr"]["normalized_value"] == "5"

    # person_name must NOT be marked missing or uncertain
    assert "person_name" not in merged["missing_fields"]
    assert "person_name" not in merged["uncertain_fields"]


# ==============================================================================
# 2. Authority Partition: Page 2 Authoritative for Examination Fields
# ==============================================================================

def test_cross_page_page2_authoritative_for_clinical_examination():
    """Page 2 clinical examination fields are authoritative and carry provenance source_page=2."""
    raw_extracted = {
        "person_name": ["Sneha Reddy", 0.95],
        "admission_id": ["ADM-550", 0.95],
        "exam_date": ["14/07/2026", 0.95],
        "height": ["138.0", 0.92],
        "weight": ["31.5", 0.92],
        "blood_pressure": ["98/62", 0.90],
        "pulse": ["76", 0.92],
        "spinal_caliper_atr": ["0°", 0.95],
        "vital_signs_status": ["Normal", 0.95],
        "gait_status": ["Normal", 0.95],
        "otoscope_right_ear": ["B/L Wax", 0.90],
        "otoscope_left_ear": ["B/L Wax", 0.90],
        "sys_nose": ["Normal", 0.95],
        "sys_throat": ["Normal", 0.95],
        "vision_distant_right": ["6", 0.95],
        "vision_distant_left": ["6", 0.95],
        "dental_status": ["Normal", 0.95],
        "specialist_referred_to": ["ENT Specialist", 0.90],
        "doctor_name": ["Dr. K. Srinivas", 0.95],
        "doctor_signature": ["Signed", 0.95],
    }

    p1_res = default_extraction_pipeline.process_extracted_payload(
        "tpl_paediatric_history_v9_3", raw_extracted
    )
    p2_res = default_extraction_pipeline.process_extracted_payload(
        "tpl_paediatric_examination_v9_7", raw_extracted
    )

    merged = merge_multipage_normalized_records([p1_res, p2_res])
    fields = merged["fields"]

    # Verify Page 2 authority and provenance
    assert fields["exam_date"]["source_page"] == 2
    assert fields["height"]["source_page"] == 2
    assert fields["otoscope_right_ear"]["source_page"] == 2
    assert fields["specialist_referred_to"]["normalized_value"] == "ENT Specialist"
    assert fields["specialist_referred_to"]["source_page"] == 2

    # Verify Snellen reconstruction
    assert fields["vision_distant_right"]["normalized_value"] == "6/6"
    assert fields["vision_distant_right"]["raw_text"] == "6"


# ==============================================================================
# 3. Conflict Detection: Conflicting Page 1 and Page 2 Values Flagged Safely
# ==============================================================================

def test_cross_page_conflict_detection_and_warning():
    """When Page 1 and Page 2 carry conflicting values, preserve Page 1 and flag conflict_warning."""
    raw_p1 = {
        "person_name": ["B. Sadhana", 0.95],
        "admission_id": ["ADM-101", 0.95],
        "class_and_section": ["5-A", 0.95],
    }
    raw_p2 = {
        "person_name": ["B. Sandhya", 0.92],  # Conflicting name
        "admission_id": ["ADM-999", 0.90],    # Conflicting ID
        "class_and_section": ["5-A", 0.95],    # Matching section
        "height": ["140.0", 0.90],
        "weight": ["32.0", 0.90],
        "doctor_name": ["Dr. Rao", 0.95],
        "doctor_signature": ["Yes", 0.95]
    }

    p1_res = default_extraction_pipeline.process_extracted_payload(
        "tpl_paediatric_history_v9_3", raw_p1
    )
    p2_res = default_extraction_pipeline.process_extracted_payload(
        "tpl_paediatric_examination_v9_7", raw_p2
    )

    merged = merge_multipage_normalized_records([p1_res, p2_res])
    fields = merged["fields"]

    # person_name conflict check
    name_field = fields["person_name"]
    assert name_field["normalized_value"] == "B. Sadhana"  # Page 1 preserved
    assert name_field["status"] == "conflict_warning"
    assert "conflict_details" in name_field
    assert name_field["conflict_details"]["page1_raw"] == "B. Sadhana"
    assert name_field["conflict_details"]["page2_raw"] == "B. Sandhya"
    assert "person_name" in merged["conflicts"]
    assert "person_name" in merged["uncertain_fields"]

    # admission_id conflict check
    id_field = fields["admission_id"]
    assert id_field["normalized_value"] == "ADM-101"
    assert id_field["status"] == "conflict_warning"
    assert "admission_id" in merged["conflicts"]

    # class_and_section matching check (no conflict)
    sec_field = fields["class_and_section"]
    assert sec_field["status"] == "valid"
    assert "class_and_section" not in merged["conflicts"]


def test_cross_page_equivalent_values_no_spurious_conflict():
    """Minor formatting differences ('ADM-101' vs 'ADM101') should not trigger conflict warnings."""
    raw_p1 = {
        "person_name": ["Rahul Sharma", 0.95],
        "admission_id": ["ADM-101", 0.95],
        "class_and_section": ["5-A", 0.95],
    }
    raw_p2 = {
        "person_name": ["Rahul Sharma", 0.95],
        "admission_id": ["ADM101", 0.95],
        "class_and_section": ["5A", 0.95],
    }

    p1_res = default_extraction_pipeline.process_extracted_payload(
        "tpl_paediatric_history_v9_3", raw_p1
    )
    p2_res = default_extraction_pipeline.process_extracted_payload(
        "tpl_paediatric_examination_v9_7", raw_p2
    )

    merged = merge_multipage_normalized_records([p1_res, p2_res])
    assert len(merged["conflicts"]) == 0
    assert merged["fields"]["person_name"]["status"] == "valid"
    assert merged["fields"]["admission_id"]["status"] == "valid"


# ==============================================================================
# 4. Checkbox, Tick, Slash, and Mark Recognition
# ==============================================================================

def test_checkbox_affirmative_and_negative_marks():
    """Validates affirmative (ticks, slashes, bars) and negative (crosses) mark normalization."""
    field_def_consent = {"key": "parent_signature", "type": "signature_present"}
    field_def_system = {"key": "sys_nose", "type": "system_check", "glossary_scope": "examination"}

    # Affirmative marks
    for mark in ("✓", "✔", "☑", "[x]", "/", "|", "Yes", "signed", "true"):
        res = default_extraction_pipeline.validate_and_normalize_field(
            field_def_consent, {"raw_text": mark, "confidence": 0.95}
        )
        assert res["normalized_value"] == "Yes"
        assert res["status"] == "valid"

    # Negative marks
    for mark in ("✗", "x", "No", "absent", "unsigned", "false"):
        res = default_extraction_pipeline.validate_and_normalize_field(
            field_def_consent, {"raw_text": mark, "confidence": 0.95}
        )
        assert res["normalized_value"] == "No"
        assert res["status"] == "valid"

    # System check normal marks
    for mark in ("Normal", "N", "WNL", "w.n.l", "NAD", "n.a.d", "Clear", "Intact"):
        res = default_extraction_pipeline.validate_and_normalize_field(
            field_def_system, {"raw_text": mark, "confidence": 0.95}
        )
        assert res["normalized_value"] == "Normal"
        assert res["status"] == "valid"

    # System check abnormal marks
    for mark in ("Abnormal", "abn", "ab"):
        res = default_extraction_pipeline.validate_and_normalize_field(
            field_def_system, {"raw_text": mark, "confidence": 0.95}
        )
        assert res["normalized_value"] == "Abnormal"
        assert res["status"] == "valid"


# ==============================================================================
# 5. Snellen Visual Acuity Denominator Reconstruction
# ==============================================================================

def test_snellen_visual_acuity_denominator_reconstruction():
    """Pre-printed '6/' prefix omitted on paper is reconstructed in normalized value; raw text kept."""
    field_def_vision = {
        "key": "vision_distant_right",
        "type": "fraction",
        "glossary_scope": "vision"
    }

    # Denominators written without '6/'
    test_denominators = [("6", "6/6"), ("9", "6/9"), ("12", "6/12"), ("18", "6/18"), ("24", "6/24"), ("36", "6/36"), ("60", "6/60")]
    for written, expected_norm in test_denominators:
        res = default_extraction_pipeline.validate_and_normalize_field(
            field_def_vision, {"raw_text": written, "confidence": 0.92}
        )
        assert res["raw_text"] == written  # Raw preserved
        assert res["normalized_value"] == expected_norm
        assert res["status"] == "valid"
        assert res["rule_applied"] == "snellen_denominator_reconstructed"

    # Full fractions written
    res_full = default_extraction_pipeline.validate_and_normalize_field(
        field_def_vision, {"raw_text": "6/6", "confidence": 0.95}
    )
    assert res_full["normalized_value"] == "6/6"
    assert res_full["status"] == "valid"

    # Non-fraction clinical notations
    for notation in ("CF", "CF 1m", "HM", "PL", "NPL", "PL+", "PL-"):
        res_nf = default_extraction_pipeline.validate_and_normalize_field(
            field_def_vision, {"raw_text": notation, "confidence": 0.90}
        )
        assert res_nf["status"] == "valid"

    # Ambiguous vision entry
    res_amb = default_extraction_pipeline.validate_and_normalize_field(
        field_def_vision, {"raw_text": "blur", "confidence": 0.80}
    )
    assert res_amb["status"] == "ambiguous_vision"


# ==============================================================================
# 6. Spinal Caliper ATR: Degrees, Negatives, and Ambiguous Protection
# ==============================================================================

def test_spinal_caliper_atr_interpretation():
    """ATR degree values parse cleanly, 'Nil'/'NAD' normalize to None without alert, ambiguous flags warning."""
    field_def_atr = {
        "key": "spinal_caliper_atr",
        "type": "number",
        "alert_hook": "check_spinal_atr"
    }

    # Numeric degrees
    for written, expected in [("5°", "5"), ("5 deg", "5"), ("0°", "0"), ("0", "0"), ("7.5°", "7.5")]:
        res = default_extraction_pipeline.validate_and_normalize_field(
            field_def_atr, {"raw_text": written, "confidence": 0.90}
        )
        assert res["raw_text"] == written
        assert res["normalized_value"] == expected
        assert res["status"] == "valid"

    # Negative / unrecorded notations
    for neg in ("Nil", "NAD", "None", "-", "--", "normal"):
        res = default_extraction_pipeline.validate_and_normalize_field(
            field_def_atr, {"raw_text": neg, "confidence": 0.95}
        )
        assert res["raw_text"] == neg
        assert res["normalized_value"] is None
        assert res["status"] == "empty"
        assert res["rule_applied"] == "atr_negative_noted"

    # Ambiguous entry: MUST NOT BE FORCED TO 0 (which would falsely clear child of scoliosis risk)
    res_amb = default_extraction_pipeline.validate_and_normalize_field(
        field_def_atr, {"raw_text": "mild curve", "confidence": 0.70}
    )
    assert res_amb["raw_text"] == "mild curve"
    assert res_amb["normalized_value"] is None
    assert res_amb["status"] == "format_warning"
    assert res_amb["normalized_value"] != "0"


# ==============================================================================
# 7. Numerical Fidelity: No Silent Decimal Shifts
# ==============================================================================

def test_numerical_fidelity_strict_preservation():
    """Written numbers are never decimal-shifted or altered, outliers are flagged but preserved."""
    field_def_height = {
        "key": "height",
        "type": "number",
        "validation": "^(4[0-9]|[5-9][0-9]|1[0-9]{2}|2[0-4][0-9])(\\.\\d+)?$"
    }

    # Doctor writes "15.4" for height (outlier in cm): raw and normalized remain "15.4", flagged format_warning
    res_low = default_extraction_pipeline.validate_and_normalize_field(
        field_def_height, {"raw_text": "15.4", "confidence": 0.90}
    )
    assert res_low["raw_text"] == "15.4"
    assert res_low["normalized_value"] == "15.4"  # NOT shifted to 154!
    assert res_low["status"] == "format_warning"

    # Doctor writes "154" for height: raw and normalized remain "154"
    res_norm = default_extraction_pipeline.validate_and_normalize_field(
        field_def_height, {"raw_text": "154", "confidence": 0.95}
    )
    assert res_norm["raw_text"] == "154"
    assert res_norm["normalized_value"] == "154"
    assert res_norm["status"] == "valid"


# ==============================================================================
# 8. Clinical Glossary Scoped Abbreviation Expansion
# ==============================================================================

def test_domain_shorthand_glossary_expansion():
    """Common camp abbreviations expand accurately within their clinical scopes."""
    # ENT Examination
    dns = default_glossary_service.normalize_text("DNS", scope="examination")
    assert dns["normalized"] == "Deviated Nasal Septum (DNS)"

    csom = default_glossary_service.normalize_text("CSOM", scope="examination")
    assert csom["normalized"] == "Chronic Suppurative Otitis Media (CSOM)"

    aom = default_glossary_service.normalize_text("AOM", scope="examination")
    assert aom["normalized"] == "Acute Otitis Media (AOM)"

    wax = default_glossary_service.normalize_text("B/L Wax", scope="examination")
    assert wax["normalized"] == "Bilateral Wax Impaction"

    tm = default_glossary_service.normalize_text("TM intact", scope="examination")
    assert tm["normalized"] == "Tympanic Membrane Intact"

    ped = default_glossary_service.normalize_text("Pediculosis", scope="examination")
    assert ped["normalized"] == "Pediculosis (Head Lice)"

    # Dental
    caries = default_glossary_service.normalize_text("Caries", scope="dental")
    assert caries["normalized"] == "Dental Caries"


# ==============================================================================
# 9. Prompt Completeness: PAEDIATRIC_EXTRACTION_PROMPT Rules Verification
# ==============================================================================

def test_paediatric_extraction_prompt_contains_all_clinical_rules():
    """Verify PAEDIATRIC_EXTRACTION_PROMPT contains the required clinical rules."""
    prompt = PAEDIATRIC_EXTRACTION_PROMPT

    # Checkbox & mark rules
    assert "checkmark (✓)" in prompt
    assert "diagonal slash (/)" in prompt
    assert "vertical stroke (|)" in prompt
    assert "cross (✗)" in prompt
    assert "Circled or underlined" in prompt

    # Brackets & multi-row
    assert "curly bracket '{'" in prompt
    assert "spans multiple items" in prompt

    # Bilateral notation
    assert "B/L" in prompt
    assert "otoscope_right_ear and otoscope_left_ear" in prompt

    # Visual acuity 6/
    assert "pre-prints '6/'" in prompt
    assert "Counting Fingers" in prompt or "CF" in prompt

    # Spinal Caliper ATR
    assert "Spinal Caliper ATR" in prompt
    assert "'Nil', 'NAD'" in prompt
    assert "never guess 0" in prompt

    # Domain shorthand
    assert "DNS" in prompt
    assert "CSOM" in prompt
    assert "Caries irt" in prompt

    # Numerical fidelity
    assert "Copy numbers exactly as written" in prompt
    assert "shift decimal points" in prompt


# ==============================================================================
# 10. End-to-End Extraction with Cross-Page Coalescence & Clinical Alerts
# ==============================================================================

def test_e2e_ai_extract_integration_with_coalescence(tmp_path):
    """End-to-end ai_extract integration: Page 1 demographics unite with Page 2 vitals for clinical alerts."""
    db_file = str(tmp_path / "test_stage1.db")
    database.init_db(db_file)

    p1_bytes = _make_test_image("ASF-D&A-PCS-9.3/07/25 Paediatric History")
    p2_bytes = _make_test_image("ASF-D&A-PCS-9.7/07/26 Health Assessment Examination")

    camp = database.create_camp("School Health Camp", 100, target_group="paediatric", db_path=db_file)

    mock_llm_payload = {
        "success": True,
        "data": {
            # Page 1 Demographics
            "person_name": ["Meera Krishnan", 0.98],
            "dob": ["10/04/2014", 0.95],
            "age": ["10", 0.95],
            "gender": ["Female", 0.95],
            "blood_group": ["A+ve", 0.90],
            "school_name": ["National Public School", 0.95],
            "class_and_section": ["5-C", 0.95],
            "admission_id": ["NPS-501", 0.95],
            # Page 2 Clinical Examination
            "exam_date": ["15/07/2026", 0.95],
            "height": ["140.0", 0.92],
            "weight": ["52.0", 0.92],  # Elevated BMI for 10-year-old female
            "blood_pressure": ["124/82", 0.90],  # Elevated BP
            "spinal_caliper_atr": ["6°", 0.88],  # Positive scoliosis screen >= 5°
            "vision_distant_right": ["6", 0.92],
            "vision_distant_left": ["6", 0.92],
            "doctor_name": ["Dr. S. Nair", 0.95],
            "doctor_signature": ["Signed", 0.95]
        }
    }

    with patch("backend.services.claude_service.ClaudeService.extract_from_images", return_value=mock_llm_payload):
        res = ai_extract(
            student_form_bytes=p1_bytes,
            health_assessment_bytes=p2_bytes,
            camp_id=camp["id"],
            db_path=db_file
        )

        assert res["success"] is True
        assert res["form_family"] == "paediatric"
        norm = res["normalized_fields"]

        # Confirm Page 1 demographics survived
        assert norm["person_name"]["normalized_value"] == "Meera Krishnan"
        assert norm["dob"]["normalized_value"] == "10/04/2014"

        # Confirm Page 2 vitals survived
        assert norm["height"]["normalized_value"] == "140.0"
        assert norm["weight"]["normalized_value"] == "52.0"
        assert norm["spinal_caliper_atr"]["normalized_value"] == "6"

        # Confirm Snellen fraction reconstruction
        assert norm["vision_distant_right"]["normalized_value"] == "6/6"

        # Confirm paediatric clinical alerts were generated using combined data
        assert res.get("is_paediatric") == 1
        alerts = res.get("paediatric_alerts", [])
        assert any("Scoliosis" in a for a in alerts)
