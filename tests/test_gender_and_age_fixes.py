"""Regression tests for Stage 2B Bug Fixes:
1. Fix 1: Gender and glossary normalization
2. Fix 2: Examination-date-based age calculation
"""

from datetime import date
import pytest

from backend.services.glossary_service import (
    ClinicalGlossaryService,
    default_glossary_service,
)
from backend.services.extraction_v2 import default_extraction_pipeline
from backend.services.paediatric_screening import (
    parse_clinical_date,
    calculate_completed_age,
    classify_paediatric,
    classify_paediatric_bmi,
    classify_paediatric_bp,
)


# ===========================================================================
# Fix 1: Gender & Glossary Normalization Tests
# ===========================================================================

class TestGenderNormalization:
    """Verify gender context resolves F -> Female and M -> Male without glossary pollution."""

    @pytest.fixture
    def glossary(self):
        return default_glossary_service

    @pytest.fixture
    def pipeline(self):
        return default_extraction_pipeline

    @pytest.mark.parametrize("raw_input,expected_norm", [
        ("F", "Female"),
        ("f", "Female"),
        ("Female", "Female"),
        ("female", "Female"),
        ("fem", "Female"),
        ("girl", "Female"),
        ("woman", "Female"),
        ("M", "Male"),
        ("m", "Male"),
        ("Male", "Male"),
        ("male", "Male"),
        ("boy", "Male"),
        ("man", "Male"),
        ("Other", "Other"),
        ("other", "Other"),
        ("o", "Other"),
    ])
    def test_direct_gender_terms_in_gender_field(self, glossary, raw_input, expected_norm):
        """Standard abbreviations and terms resolve correctly in gender fields."""
        res = glossary.normalize_text(raw_input, field_key="gender")
        assert res["normalized"] == expected_norm
        assert res["raw_text"] == raw_input
        assert res["scope_used"] == "gender"

    @pytest.mark.parametrize("checkbox_raw,expected_norm", [
        ("[x] F", "Female"),
        ("[X] F", "Female"),
        ("[✓] Female", "Female"),
        ("[✔] female", "Female"),
        ("(x) F", "Female"),
        ("✓ F", "Female"),
        ("✔ F", "Female"),
        ("☑ F", "Female"),
        ("F [x]", "Female"),
        ("Female [x]", "Female"),
        ("[x] M", "Male"),
        ("[X] M", "Male"),
        ("[✓] Male", "Male"),
        ("(x) M", "Male"),
        ("✓ M", "Male"),
        ("M [x]", "Male"),
        ("[x] Other", "Other"),
    ])
    def test_checkbox_marked_gender(self, glossary, checkbox_raw, expected_norm):
        """Checkbox, tick, and bracket marks are cleanly stripped and mapped."""
        res = glossary.normalize_text(checkbox_raw, field_key="gender")
        assert res["normalized"] == expected_norm
        assert res["raw_text"] == checkbox_raw
        assert res["scope_used"] == "gender"

    @pytest.mark.parametrize("ambiguous_raw", [
        "M/F",
        "F/M",
        "???",
        "unreadable",
        "unknown",
        "illegible",
        "not clear",
        "F/U",  # In a gender field, 'F/U' must NOT become 'Follow-up'
    ])
    def test_ambiguous_values_do_not_expand_to_doctor_short_forms(self, glossary, ambiguous_raw):
        """Ambiguous/unrecognized gender values are preserved as raw and flagged new_term."""
        res = glossary.normalize_text(ambiguous_raw, field_key="gender")
        assert res["normalized"] == ambiguous_raw
        assert res["raw_text"] == ambiguous_raw
        assert res["status"] == "new_term"
        assert res["is_expanded"] is False
        assert "Follow-up" not in res["normalized"]

    def test_unrelated_clinical_meaning_of_f_preserved(self, glossary):
        """In non-gender clinical contexts, 'F' and 'F/U' must still expand to Follow-up."""
        # General doctor notes or referral context
        res_fu = glossary.normalize_text("F/U", scope="referral", field_key="plan")
        assert res_fu["normalized"] == "Follow-Up Advised"
        assert res_fu["is_expanded"] is True

        res_f = glossary.normalize_text("F", field_key="doctor_notes")
        assert res_f["normalized"] == "Follow-up"
        assert res_f["is_expanded"] is True

    def test_pipeline_validation_and_provenance(self, pipeline):
        """Extraction pipeline validates gender, sets valid status, and preserves raw text."""
        field_def = {
            "key": "gender",
            "type": "option_list",
            "options": ["Male", "Female", "Other"],
            "required": True,
        }

        # Case 1: Raw 'F' -> Normalized 'Female', status 'valid', raw_text 'F'
        wrapped_f = pipeline.validate_and_normalize_field(
            field_def, {"raw_text": "F", "confidence": 0.95}
        )
        assert wrapped_f["normalized_value"] == "Female"
        assert wrapped_f["raw_text"] == "F"
        assert wrapped_f["status"] == "valid"
        assert "gender" in wrapped_f["rule_applied"]

        # Case 2: Raw 'M' -> Normalized 'Male', status 'valid', raw_text 'M'
        wrapped_m = pipeline.validate_and_normalize_field(
            field_def, {"raw_text": "M", "confidence": 0.92}
        )
        assert wrapped_m["normalized_value"] == "Male"
        assert wrapped_m["raw_text"] == "M"
        assert wrapped_m["status"] == "valid"

        # Case 3: Ambiguous gender string -> status 'format_warning', raw_text preserved
        wrapped_amb = pipeline.validate_and_normalize_field(
            field_def, {"raw_text": "Unclear", "confidence": 0.50}
        )
        assert wrapped_amb["normalized_value"] == "Unclear"
        assert wrapped_amb["raw_text"] == "Unclear"
        assert wrapped_amb["status"] == "format_warning"


# ===========================================================================
# Fix 2: Examination-Date-Based Age Calculation Tests
# ===========================================================================

class TestClinicalDateParsing:
    """Verify parse_clinical_date supports all expected clinical formats and rejects invalid ones."""

    def test_date_objects(self):
        d = date(2024, 11, 4)
        assert parse_clinical_date(d) == d

    @pytest.mark.parametrize("date_str,expected_date", [
        ("2013-07-08", date(2013, 7, 8)),
        ("2024-11-04", date(2024, 11, 4)),
        ("08/07/2013", date(2013, 7, 8)),
        ("8/7/2013", date(2013, 7, 8)),
        ("08-07-2013", date(2013, 7, 8)),
        ("08.07.2013", date(2013, 7, 8)),
        ("04/11/2024", date(2024, 11, 4)),
        ("4/11/24", date(2024, 11, 4)),    # 2-digit year <= 50 -> 2000+yy
        ("08/09/24", date(2024, 9, 8)),
        ("8/9/24", date(2024, 9, 8)),
        ("15/05/99", date(1999, 5, 15)),   # 2-digit year > 50 -> 1900+yy
    ])
    def test_valid_clinical_date_strings(self, date_str, expected_date):
        assert parse_clinical_date(date_str) == expected_date

    @pytest.mark.parametrize("invalid_input", [
        None,
        "",
        "   ",
        "None",
        "nil",
        "unknown",
        "???",
        "31/02/2024",    # Invalid leap day
        "32/01/2024",    # Day out of range
        "15/13/2024",    # Month out of range
        "00/00/0000",
        "not-a-date",
        "12345",
    ])
    def test_invalid_clinical_dates_return_none(self, invalid_input):
        assert parse_clinical_date(invalid_input) is None


class TestCompletedAgeCalculation:
    """Verify calculate_completed_age strictly implements completed years with birthday boundaries."""

    def test_birthday_boundary_before_birthday(self):
        """Day before birthday -> age is completed years - 1."""
        dob = date(2013, 7, 8)
        exam_before = date(2024, 7, 7)
        assert calculate_completed_age(dob, exam_before) == 10

    def test_birthday_boundary_on_birthday(self):
        """Exactly on birthday -> full completed year reached."""
        dob = date(2013, 7, 8)
        exam_on = date(2024, 7, 8)
        assert calculate_completed_age(dob, exam_on) == 11

    def test_birthday_boundary_after_birthday(self):
        """Day after birthday -> completed year."""
        dob = date(2013, 7, 8)
        exam_after = date(2024, 7, 9)
        assert calculate_completed_age(dob, exam_after) == 11

    def test_case_1_source_values(self):
        """Benchmark Case 1: DOB 08/07/2013, Exam 04/11/2024 -> completed age 11."""
        dob = date(2013, 7, 8)
        exam = date(2024, 11, 4)
        assert calculate_completed_age(dob, exam) == 11

    def test_case_2_source_values(self):
        """Benchmark Case 2: DOB 08/09/2015, Exam 08/09/2024 (exact birthday) -> completed age 9."""
        dob = date(2015, 9, 8)
        exam = date(2024, 9, 8)
        assert calculate_completed_age(dob, exam) == 9

    @pytest.mark.parametrize("exam_year,expected_age", [
        (2023, 10),
        (2024, 11),
        (2025, 12),
        (2026, 13),  # Confirms dynamic exam year: NOT locked to hardcoded 2024
    ])
    def test_different_examination_years(self, exam_year, expected_age):
        dob = date(2013, 7, 8)
        exam = date(exam_year, 11, 4)
        assert calculate_completed_age(dob, exam) == expected_age

    def test_exam_preceding_dob_returns_none(self):
        """Exam date before DOB is logically impossible; returns None."""
        dob = date(2013, 7, 8)
        exam = date(2010, 1, 1)
        assert calculate_completed_age(dob, exam) is None

    def test_missing_inputs_return_none(self):
        assert calculate_completed_age(None, date(2024, 1, 1)) is None
        assert calculate_completed_age(date(2013, 7, 8), None) is None
        assert calculate_completed_age(None, None) is None


class TestPaediatricScreeningDispatcherWithDates:
    """Verify classify_paediatric correctly uses DOB + exam_date and handles edge cases safely."""

    def test_case_1_screening_from_dob_and_exam_date(self):
        """Case 1: Age box blank on form, derived from DOB and exam date."""
        record = {
            "dob": "08/07/2013",
            "exam_date": "4/11/24",
            "gender": "Female",
            "height_cm": "154.4",
            "weight_kg": "35.7",
            "bp": "100/60",
            "atr": "5°",
        }
        res = classify_paediatric(record)

        assert res["is_paediatric"] is True
        bmi_cls = res["paediatric_bmi_classification"]
        assert bmi_cls["age_used"] == 11.0
        assert bmi_cls["sex_used"] == "F"
        assert res["bmi"] == 15.0
        assert res["bmi_centile"] is not None
        assert res["bmi_classification"] in ("Moderately Thin", "Normal", "Severely Thin")
        assert res["atr_classification"]["alert_triggered"] is True

    def test_missing_exam_date_does_not_fabricate_age(self):
        """Missing exam date must NOT fall back to 2024 or fabricate an age."""
        record = {
            "dob": "08/07/2013",
            # exam_date missing
            "gender": "Female",
            "height_cm": "154.4",
            "weight_kg": "35.7",
        }
        res = classify_paediatric(record)

        assert res["is_paediatric"] is False
        assert res["bmi_centile"] is None
        assert res["bmi_classification"] == "Unknown"
        assert res["paediatric_bmi_classification"]["category"] == "Unknown"
        assert "Insufficient data" in res["paediatric_bmi_classification"]["staging"]
        assert res["paediatric_bp_classification"]["stage"] == "Unknown"

    def test_invalid_dob_does_not_fabricate_age(self):
        """Invalid DOB (e.g. Feb 31) must NOT crash and must NOT fabricate an age."""
        record = {
            "dob": "31/02/2013",
            "exam_date": "04/11/2024",
            "gender": "Female",
            "height_cm": "154.4",
            "weight_kg": "35.7",
        }
        res = classify_paediatric(record)

        assert res["is_paediatric"] is False
        assert res["bmi_centile"] is None
        assert res["bmi_classification"] == "Unknown"

    def test_adult_age_from_dates_uses_adult_staging(self):
        """Patient aged >= 18 from DOB/exam date uses adult classification."""
        record = {
            "dob": "01/01/2000",
            "exam_date": "01/01/2024",  # Age 24
            "gender": "Male",
            "height_cm": "175",
            "weight_kg": "70",
        }
        res = classify_paediatric(record)

        assert res["is_paediatric"] is False
        assert res["bmi_centile"] is None
        assert res["paediatric_bmi_classification"]["category"] == "Not Applicable"
        assert "adult BMI" in res["paediatric_bmi_classification"]["staging"]

    def test_explicit_age_overrides_date_derivation(self):
        """If explicit age field is already present, it is preserved and used directly."""
        record = {
            "age": "10",
            "dob": "08/07/2013",
            "exam_date": "04/11/2026",  # Would be 13 from dates
            "gender": "Female",
            "height_cm": "140",
            "weight_kg": "32",
        }
        res = classify_paediatric(record)

        assert res["is_paediatric"] is True
        assert res["age_years"] == 10.0
        assert res["paediatric_bmi_classification"]["age_used"] == 10.0
        assert res["has_age_discrepancy"] is True
        assert any("Demographic Discrepancy" in a for a in res["alerts"])

    def test_explicit_age_matching_dates_has_no_discrepancy(self):
        """Explicit age matching DOB + exam date does not trigger discrepancy alert."""
        record = {
            "age": "11",
            "dob": "08/07/2013",
            "exam_date": "04/11/2024",  # Exactly 11
            "gender": "Female",
            "height_cm": "154.4",
            "weight_kg": "35.7",
        }
        res = classify_paediatric(record)

        assert res["is_paediatric"] is True
        assert res["age_years"] == 11.0
        assert res["has_age_discrepancy"] is False
        assert not any("Demographic Discrepancy" in a for a in res["alerts"])

    def test_dd_mm_yyyy_convention_and_ambiguous_dates(self):
        """Verify strict DD/MM/YYYY convention and safe failure on non-DD/MM/YYYY inputs."""
        # 1. 08/07/2013 is July 8th, NOT August 7th
        d_july = parse_clinical_date("08/07/2013")
        assert d_july == date(2013, 7, 8)
        assert d_july.day == 8
        assert d_july.month == 7

        # 2. US-style month > 12 in second position (e.g. 05/13/2024) must NOT parse as Month 13
        assert parse_clinical_date("05/13/2024") is None

        # 3. Invalid calendar day (e.g. April 31st: April only has 30 days) must return None
        assert parse_clinical_date("31/04/2024") is None

        # 4. Ambiguous text dates return None
        assert parse_clinical_date("04-Nov-2024") is None

        # 5. When exam date is an invalid date string, age is not fabricated and screening is blocked
        record_invalid_exam = {
            "dob": "08/07/2013",
            "exam_date": "31/04/2024",  # Invalid
            "gender": "Female",
            "height_cm": "154.4",
            "weight_kg": "35.7",
            "bp": "100/60",
        }
        res = classify_paediatric(record_invalid_exam)
        assert res["age_years"] is None
        assert res["is_paediatric"] is False
        assert res["bmi_centile"] is None
        assert res["bmi_classification"] == "Unknown"
        assert res["bp_classification"] == "Unknown"

    def test_missing_or_invalid_age_prevents_all_age_dependent_assessments(self):
        """Missing or invalid age strictly prevents BMI centile and BP staging."""
        records_to_test = [
            {"gender": "Female", "height_cm": "150", "weight_kg": "40", "bp": "100/60"},  # Completely missing age and dates
            {"age": "unreadable", "gender": "Female", "height_cm": "150", "weight_kg": "40"},  # Unreadable age
            {"dob": "invalid-dob", "exam_date": "04/11/2024", "gender": "Female", "height_cm": "150", "weight_kg": "40"},
            {"dob": "08/07/2013", "exam_date": "invalid-exam", "gender": "Female", "height_cm": "150", "weight_kg": "40"},
        ]
        for rec in records_to_test:
            res = classify_paediatric(rec)
            assert res["is_paediatric"] is False
            assert res["age_years"] is None
            assert res["bmi_centile"] is None
            assert res["bmi_classification"] == "Unknown"
            assert res["bp_classification"] == "Unknown"
            assert res["bp_percentile"] == "Unknown"
            assert res["paediatric_bmi_classification"]["category"] == "Unknown"
            assert res["paediatric_bp_classification"]["stage"] == "Unknown"
            # Ensure no false paediatric BMI/BP alerts are triggered
            assert not any(a.startswith("BMI Alert") for a in res["alerts"])
            assert not any(a.startswith("BP Alert") for a in res["alerts"])

