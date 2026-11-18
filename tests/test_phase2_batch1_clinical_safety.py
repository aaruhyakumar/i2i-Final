"""Phase 2, Batch 1: Clinical Safety Unit and Integration Tests.

Validates:
1. Missing age handling (no 2-year clamping, no false growth alert).
2. Missing DOB or missing Exam Date handling.
3. Ambiguous date detection (e.g. DD/MM vs MM/DD).
4. Valid age & dates exact completed age calculation.
5. Dynamic BMI recalculation upon height/weight change.
6. Field clearing (clearing height, weight, or age clears stale calculations).
7. Non-regression of existing Case 1 and Case 2 clinical payloads.
"""

import pytest
from datetime import date
from backend.services.paediatric_screening import (
    parse_clinical_date,
    calculate_completed_age,
    is_ambiguous_date,
    classify_paediatric,
    classify_paediatric_bmi,
    classify_paediatric_bp,
    classify_atr,
)


class TestClinicalSafetyAgeAndDates:
    """Test Task 1A: Strict age handling, no 2-year fallback, and date parsing."""

    def test_missing_age_no_growth_alert(self):
        """When age is missing and cannot be derived, no growth alerts are generated."""
        res = classify_paediatric({
            "is_paediatric": True,
            "height_cm": 140.0,
            "weight_kg": 25.0,
            # No age, no dob, no exam_date
        })
        bmi_cls = res["paediatric_bmi_classification"]
        assert bmi_cls["category"] == "Unknown"
        assert bmi_cls["centile"] is None
        assert bmi_cls["alert_level"] == "none"
        # Must not contain definitive growth alert
        assert not any("BMI Alert [CRITICAL]" in a for a in res["paediatric_alerts"])
        assert any("Demographic Notice [REQUIRED]" in a for a in res["paediatric_alerts"])

    def test_missing_dob_cannot_derive_age(self):
        """Missing DOB prevents age derivation even if exam date is present."""
        res = classify_paediatric({
            "height_cm": 140.0,
            "weight_kg": 25.0,
            "exam_date": "15/10/2024",
        })
        assert res["paediatric_bmi_classification"]["category"] == "Unknown"
        assert res["age_years"] is None

    def test_missing_exam_date_cannot_derive_age(self):
        """Missing exam date prevents age derivation even if DOB is present."""
        res = classify_paediatric({
            "height_cm": 140.0,
            "weight_kg": 25.0,
            "dob": "12/05/2014",
        })
        assert res["paediatric_bmi_classification"]["category"] == "Unknown"
        assert res["age_years"] is None

    def test_exact_completed_age_calculation(self):
        """Chronological age must use full completed years, not year-only difference."""
        d_dob = parse_clinical_date("12/05/2014")
        # 10 days before 10th birthday -> completed age 9
        d_exam_before = parse_clinical_date("02/05/2024")
        assert calculate_completed_age(d_dob, d_exam_before) == 9

        # After 10th birthday -> completed age 10
        d_exam_after = parse_clinical_date("15/10/2024")
        assert calculate_completed_age(d_dob, d_exam_after) == 10

    def test_ambiguous_date_detection(self):
        """Ambiguous day/month format (both <= 12) is detected."""
        ambig, reason = is_ambiguous_date("05/06/2014")
        assert ambig is True
        assert "Ambiguous" in reason

        # 25/06/2014 is unambiguously DD/MM/YYYY
        unambig, _ = is_ambiguous_date("25/06/2014")
        assert unambig is False

    def test_ambiguous_date_generates_advisory_alert(self):
        """When an ambiguous date is used, an advisory alert is generated."""
        res = classify_paediatric({
            "dob": "05/06/2014",
            "exam_date": "10/08/2024",
            "height_cm": 140.0,
            "weight_kg": 25.0
        })
        assert any("Date Format Verification [ADVISORY]" in a for a in res["paediatric_alerts"])

    def test_low_confidence_date_generates_warning(self):
        """Low confidence DOB triggers clinical anchor verification warning."""
        res = classify_paediatric({
            "dob": ["12/05/2014", 0.65],  # Low confidence extraction
            "exam_date": ["15/10/2024", 0.95],
            "height_cm": 140.0,
            "weight_kg": 25.0
        })
        assert any("Clinical Anchor Verification [WARNING]" in a for a in res["paediatric_alerts"])

    def test_unsupported_age_under_2_no_clamping(self):
        """Age under 2 is not clamped to 2 for IAP LMS curves."""
        cls = classify_paediatric_bmi(16.0, 1.2, sex="M")
        assert cls["category"] == "Unknown"
        assert cls["centile"] is None
        assert "out of paediatric range" in cls["note"]


class TestClinicalSafetyRecalculationAndClearing:
    """Test Task 1B: Dynamic BMI recalculation and field clearing behavior."""

    def test_bmi_recalculation_on_height_weight_edit(self):
        """Editing height and weight produces correct live BMI."""
        # Initial: 140 cm, 35 kg -> BMI 17.86 -> 17.9
        res1 = classify_paediatric({"height_cm": 140.0, "weight_kg": 35.0, "age": 10, "gender": "M"})
        assert res1["paediatric_calculated_bmi"] == 17.9

        # User updates weight to 24.8 kg -> BMI 12.65 -> 12.7
        res2 = classify_paediatric({"height_cm": 139.5, "weight_kg": 24.8, "age": 10, "gender": "M"})
        assert res2["paediatric_calculated_bmi"] == 12.7
        assert res2["paediatric_bmi_classification"]["category"] == "Severely Thin"

    def test_clearing_weight_removes_stale_bmi_and_alerts(self):
        """Clearing weight must clear calculated BMI and remove stale BMI alerts."""
        res = classify_paediatric({
            "height_cm": 140.0,
            "weight_kg": None,  # Cleared by user
            "age": 10,
            "gender": "M"
        })
        assert res["paediatric_calculated_bmi"] is None
        assert not any("BMI Alert" in a for a in res["paediatric_alerts"])

    def test_clearing_age_removes_growth_assessment(self):
        """Clearing age removes centile classification while keeping valid vitals."""
        res = classify_paediatric({
            "height_cm": 140.0,
            "weight_kg": 35.0,
            "age": None,  # Cleared by user
            "dob": None,
            "exam_date": None,
            "bp": "100/60"
        })
        assert res["paediatric_calculated_bmi"] == 17.9
        assert res["paediatric_bmi_classification"]["centile"] is None
        assert res["paediatric_bmi_classification"]["category"] == "Unknown"
        assert not any("BMI Alert" in a for a in res["paediatric_alerts"])


class TestPaediatricCaseParity:
    """Regression test: Ensure Case 1 and Case 2 evaluate accurately with verified dates."""

    def test_case1_audit(self):
        """Case 1 (139.5 cm / 24.8 kg / age 10 / ATR 10°) -> Severely Thin + ATR alert."""
        res = classify_paediatric({
            "person_name": "Aaditya Patel",
            "age": 10,
            "dob": "12/05/2014",
            "exam_date": "15/10/2024",
            "gender": "Male",
            "height_cm": 139.5,
            "weight_kg": 24.8,
            "bp": "100/60",
            "atr": 10.0
        })
        assert res["paediatric_calculated_bmi"] == 12.7
        assert res["paediatric_bmi_classification"]["category"] == "Severely Thin"
        assert res["paediatric_bmi_classification"]["centile"] < 3.0
        assert any("BMI Alert [CRITICAL]" in a for a in res["paediatric_alerts"])
        assert any("Scoliosis Screen [CRITICAL]" in a for a in res["paediatric_alerts"])

    def test_case2_audit(self):
        """Case 2 (154.4 cm / 35.7 kg / age 10 / ATR 5°) -> Normal Weight + Scoliosis Advisory."""
        res = classify_paediatric({
            "person_name": "Aaditya Patel",
            "age": 10,
            "dob": "12/05/2014",
            "exam_date": "15/10/2024",
            "gender": "Male",
            "height_cm": 154.4,
            "weight_kg": 35.7,
            "bp": "100/60",
            "atr": 5.0
        })
        assert res["paediatric_calculated_bmi"] == 15.0
        assert res["paediatric_bmi_classification"]["category"] == "Normal Weight"
        assert 15.0 <= res["paediatric_bmi_classification"]["centile"] < 85.0
        assert not any("BMI Alert" in a for a in res["paediatric_alerts"])
        assert any("Scoliosis Screen [ADVISORY]" in a for a in res["paediatric_alerts"])
