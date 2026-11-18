import pytest
from backend.services.glossary_service import ClinicalGlossaryService
from backend.services.extraction_v2 import ExtractionV2Pipeline

def test_gender_variations():
    glossary = ClinicalGlossaryService()

    # Female variations
    female_cases = ["[x] F", "[X] Female", "✓ Female", "F [x]", "Female [X]", "female", "f", "girl", "[✓] F", "F [✓]"]
    for case in female_cases:
        res = glossary.normalize_text(case, scope="gender")
        assert res["normalized"] == "Female", f"Failed for {case}: {res}"
        assert res["status"] in ("exact", "scoped_expansion")

    # Male variations
    male_cases = ["M [x]", "[x] M", "[X] Male", "Male [x]", "✓ Male", "M", "Male", "boy", "[✓] M", "M [✓]"]
    for case in male_cases:
        res = glossary.normalize_text(case, scope="gender")
        assert res["normalized"] == "Male", f"Failed for {case}: {res}"
        assert res["status"] in ("exact", "scoped_expansion")

    # Dual checkbox lines
    res = glossary.normalize_text("[ ] M [x] F", scope="gender")
    assert res["normalized"] == "Female"
    assert res["status"] == "scoped_expansion"

    res = glossary.normalize_text("[x] M [ ] F", scope="gender")
    assert res["normalized"] == "Male"
    assert res["status"] == "scoped_expansion"

    res = glossary.normalize_text("M [ ] F [x]", scope="gender")
    assert res["normalized"] == "Female"

    # Conflicting / ambiguous both marked
    res = glossary.normalize_text("[x] M [x] F", scope="gender")
    assert res["status"] == "ambiguous_gender"

    # Empty printed checkbox outlines
    res = glossary.normalize_text("[ ]", scope="gender")
    assert res["status"] == "unmarked_checkbox"
    assert res["normalized"] == ""

    res = glossary.normalize_text("[ ] M [ ] F", scope="gender")
    assert res["status"] == "unmarked_checkbox"
    assert res["normalized"] == ""


def test_yes_no_checkbox_variations():
    engine = ExtractionV2Pipeline()
    field_def = {"key": "vaccinated_for_age", "type": "yes_no", "label": "Vaccinated for Age"}

    # Affirmative variations
    affirmative = ["Yes", "yes", "Y", "✓", "✔", "[x]", "[X]", "[✓]", "[x] Yes", "Yes [x]", "✓ Yes", "[x] Yes [ ] No", "[ ] No [x] Yes"]
    for val in affirmative:
        raw_val = {"raw_text": val, "confidence": 0.95}
        res = engine.validate_and_normalize_field(field_def, raw_val)
        assert res["normalized_value"] == "Yes", f"Failed affirmative for {val}: {res}"
        assert res["status"] == "valid"

    # Negative variations
    negative = ["No", "no", "N", "✗", "Absent", "[x] No", "No [x]", "✓ No", "[ ] Yes [x] No", "[x] No [ ] Yes"]
    for val in negative:
        raw_val = {"raw_text": val, "confidence": 0.95}
        res = engine.validate_and_normalize_field(field_def, raw_val)
        assert res["normalized_value"] == "No", f"Failed negative for {val}: {res}"
        assert res["status"] == "valid"

    # Empty / blank outlines
    empty_cases = ["[ ]", "[  ]", "[ ] Yes [ ] No", "none", "nil"]
    for val in empty_cases:
        raw_val = {"raw_text": val, "confidence": 0.95}
        res = engine.validate_and_normalize_field(field_def, raw_val)
        assert res["status"] == "empty", f"Failed empty for {val}: {res}"
        assert res["normalized_value"] is None

    # Conflicting / unclear
    raw_val = {"raw_text": "[x] Yes [x] No", "confidence": 0.95}
    res = engine.validate_and_normalize_field(field_def, raw_val)
    assert res["status"] == "format_warning"
    assert res["rule_applied"] == "checkbox_conflicting_needs_review"

    raw_val = {"raw_text": "maybe/unclear squiggle", "confidence": 0.95}
    res = engine.validate_and_normalize_field(field_def, raw_val)
    assert res["status"] == "format_warning"
    assert res["rule_applied"] == "checkbox_unclear_needs_review"
