"""Tests for Stage 1: Paediatric Document Recognition & Extraction Routing.

Verifies:
1. Recognition of Paediatric History form ASF-D&A-PCS-9.3.
2. Recognition of Paediatric Examination form ASF-D&A-PCS-9.7.
3. Both paediatric pages uploaded together.
4. Missing footer codes.
5. Default adult template IDs overridden by reliable paediatric document evidence.
6. Correct history (Page 1) and examination (Page 2) page-to-template mapping.
7. Detection of inverted slots (Slot 1 exam, Slot 2 history).
8. Detection of mixed form families (Adult + Paediatric).
9. Adult routing remains functional and preserved.
10. Uncertain classification does not silently fall back to adult.
11. Existing camp target-group behavior remains functional.
12. Full end-to-end routing with ai_extract and API endpoints.

All test inputs use synthetic mock data; no real patient data is used.
"""

import io
import os
import pytest
from unittest.mock import patch

from backend import database
from backend.app import create_app
from backend.services.template_engine import default_template_engine
from backend.services.ai_service import (
    extract as ai_extract,
    PAEDIATRIC_EXTRACTION_PROMPT,
    ADULT_EXTRACTION_PROMPT,
    validate_and_sanitize_extraction
)
from backend.services.auth_service import create_access_token


@pytest.fixture
def temp_db(tmp_path):
    path = str(tmp_path / "test_paed_routing.db")
    database.init_db(path)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except PermissionError:
            pass


@pytest.fixture
def client(temp_db):
    app = create_app(db_path=temp_db)
    app.config["TESTING"] = True
    token = create_access_token(user_id="doc_routing_test", role="doctor", org_id="org_default")
    with app.test_client() as c:
        c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        yield c


def _synthetic_doc(text_markers: str) -> bytes:
    """Create synthetic document bytes containing clear text markers."""
    header = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
    content = text_markers.encode("utf-8")
    footer = b"\xFF\xD9"
    return header + content + footer


# ==============================================================================
# 1. Paediatric History Form Recognition (ASF-D&A-PCS-9.3)
# ==============================================================================

def test_01_recognize_pcs_9_3_as_paediatric_history():
    """ASF-D&A-PCS-9.3 and its variations must classify as tpl_paediatric_history_v9_3."""
    engine = default_template_engine

    # Exact registered footer code
    res_exact = engine.classify_page(footer_code_detected="ASF-D&A-PCS-9.3/07/25")
    assert res_exact["matched"] is True
    assert res_exact["template_id"] == "tpl_paediatric_history_v9_3"
    assert res_exact["template"]["family"] == "paediatric"
    assert res_exact["template"]["page_type"] == "history"

    # Substring and fuzzy variations
    for code in ["ASF-D&A-PCS-9.3", "PCS-9.3", "PCS 9.3", "pcs 9.3", "asf d&a pcs 9.3"]:
        res = engine.classify_page(footer_code_detected=code)
        assert res["matched"] is True, f"Failed for {code}"
        assert res["template_id"] == "tpl_paediatric_history_v9_3", f"Failed for {code}"

    # Header text trigger fallback
    res_hdr = engine.classify_page(
        footer_code_detected="",
        header_text_detected="Paediatric mediscreening STUDENT'S PERSONAL INFORMATION Apollo Shine"
    )
    assert res_hdr["matched"] is True
    assert res_hdr["template_id"] == "tpl_paediatric_history_v9_3"


# ==============================================================================
# 2. Paediatric Examination Form Recognition (ASF-D&A-PCS-9.7)
# ==============================================================================

def test_02_recognize_pcs_9_7_as_paediatric_examination():
    """ASF-D&A-PCS-9.7 and its variations must classify as tpl_paediatric_examination_v9_7."""
    engine = default_template_engine

    # Exact registered footer code
    res_exact = engine.classify_page(footer_code_detected="ASF-D&A-PCS-9.7/07/26")
    assert res_exact["matched"] is True
    assert res_exact["template_id"] == "tpl_paediatric_examination_v9_7"
    assert res_exact["template"]["family"] == "paediatric"
    assert res_exact["template"]["page_type"] == "examination"

    # Substring and fuzzy variations
    for code in ["ASF-D&A-PCS-9.7", "PCS-9.7", "PCS 9.7", "pcs 9.7", "asf d&a pcs 9.7"]:
        res = engine.classify_page(footer_code_detected=code)
        assert res["matched"] is True, f"Failed for {code}"
        assert res["template_id"] == "tpl_paediatric_examination_v9_7", f"Failed for {code}"

    # Header text trigger fallback
    res_hdr = engine.classify_page(
        footer_code_detected="",
        header_text_detected="Do not write in this page-for office use only HEALTH ASSESSMENT OTOSCOPE FINDINGS"
    )
    assert res_hdr["matched"] is True
    assert res_hdr["template_id"] == "tpl_paediatric_examination_v9_7"


# ==============================================================================
# 3. Both Paediatric Pages Uploaded Together
# ==============================================================================

def test_03_both_paediatric_pages_pair_resolution():
    """Two paediatric pages resolve into unified paediatric suite."""
    engine = default_template_engine

    pair = engine.resolve_page_pair(
        p1_footer="ASF-D&A-PCS-9.3/07/25",
        p2_footer="ASF-D&A-PCS-9.7/07/26"
    )
    assert pair["resolved"] is True
    assert pair["family"] == "paediatric"
    assert pair["template_ids"] == ["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"]
    assert pair["method"] == "document_evidence_match"


# ==============================================================================
# 4. Default Adult Template IDs Overridden by Paediatric Evidence
# ==============================================================================

def test_04_default_adult_template_ids_overridden_by_paediatric_evidence():
    """Client sending default adult template IDs MUST NOT override verified paediatric document evidence."""
    engine = default_template_engine

    # Client inadvertently sent default adult template IDs
    client_ids = ["tpl_adult_history_v9_1", "tpl_adult_examination_v9_1"]

    pair = engine.resolve_page_pair(
        p1_footer="ASF-D&A-PCS-9.3",
        p2_footer="ASF-D&A-PCS-9.7",
        client_template_ids=client_ids
    )

    assert pair["resolved"] is True
    assert pair["family"] == "paediatric", "Paediatric evidence must take precedence over default adult template IDs"
    assert pair["template_ids"] == ["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"]


# ==============================================================================
# 5. Missing Footer Codes & Uncertain Classification
# ==============================================================================

def test_05_missing_footer_codes_uncertain_classification():
    """Missing or unreadable footer codes without other evidence must not silently assume adult."""
    engine = default_template_engine

    pair = engine.resolve_page_pair(
        p1_footer="",
        p2_footer="",
        camp_target_group=None,
        client_template_ids=None
    )

    assert pair["resolved"] is False
    assert pair["code"] == "CLASSIFICATION_UNCERTAIN"
    assert pair["family"] == "uncertain"
    assert "no recognizable footer code" in pair["error"].lower()


# ==============================================================================
# 6. Camp Target Group Paediatric Overrides Default Adult Template IDs
# ==============================================================================

def test_06_camp_target_group_paediatric_routing():
    """When camp is configured as paediatric, default adult template IDs must not force adult extraction."""
    engine = default_template_engine

    pair = engine.resolve_page_pair(
        p1_footer="",
        p2_footer="",
        camp_target_group="paediatric",
        client_template_ids=["tpl_adult_history_v9_1", "tpl_adult_examination_v9_1"]
    )

    assert pair["resolved"] is True
    assert pair["family"] == "paediatric"
    assert pair["template_ids"] == ["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"]
    assert pair["method"] == "camp_target_group"


# ==============================================================================
# 7. Correct History / Examination Mapping & Slot Inversion Detection
# ==============================================================================

def test_07_page_to_template_mapping_and_slot_inversion():
    """Verify Page 1 maps to History and Page 2 to Exam. Inverted order triggers clear error."""
    engine = default_template_engine

    # Inverted: Page 1 has Examination, Page 2 has History
    pair_inverted = engine.resolve_page_pair(
        p1_footer="ASF-D&A-PCS-9.7",
        p2_footer="ASF-D&A-PCS-9.3"
    )

    assert pair_inverted["resolved"] is False
    assert pair_inverted["code"] == "SLOTS_INVERTED"
    assert "inverted" in pair_inverted["error"].lower()


# ==============================================================================
# 8. Mixed Family Mismatch Detection
# ==============================================================================

def test_08_mixed_family_mismatch_detected():
    """Slot 1 from Adult family and Slot 2 from Paediatric family must be rejected safely."""
    engine = default_template_engine

    pair_mixed = engine.resolve_page_pair(
        p1_footer="ASF-D&A-ACS-9.1",
        p2_footer="ASF-D&A-PCS-9.7"
    )

    assert pair_mixed["resolved"] is False
    assert pair_mixed["code"] == "FAMILY_MISMATCH"
    assert "mixed" in pair_mixed["error"].lower()


# ==============================================================================
# 9. Adult Screening Forms Rejected as Unsupported
# ==============================================================================

def test_09_adult_routing_preserved():
    """Adult document pairs (ACS-9.1 + AC 9.1) are safely rejected with DOCUMENT_UNSUPPORTED."""
    engine = default_template_engine

    pair_adult = engine.resolve_page_pair(
        p1_footer="ASF-D&A-ACS-9.1/03/25",
        p2_footer="ASF-D&A-AC 9.1/03/25"
    )

    assert pair_adult["resolved"] is False
    assert pair_adult["family"] == "unsupported"
    assert pair_adult["code"] == "DOCUMENT_UNSUPPORTED"
    assert "Adult screening forms are no longer supported" in pair_adult["error"]


# ==============================================================================
# 10. End-to-End AI Extraction Routing with Paediatric Document Evidence
# ==============================================================================

def test_10_ai_extract_routes_to_paediatric_with_document_evidence(temp_db):
    """ai_extract uses PAEDIATRIC_EXTRACTION_PROMPT when paediatric evidence is present."""
    p1_bytes = _synthetic_doc("ASF-D&A-PCS-9.3/07/25 Paediatric mediscreening")
    p2_bytes = _synthetic_doc("ASF-D&A-PCS-9.7/07/26 HEALTH ASSESSMENT OTOSCOPE")

    camp = database.create_camp("General Camp", 100, target_group="adult", db_path=temp_db)

    mock_res = {
        "success": True,
        "data": {
            "person_name": ["Ananya Kumar", 0.95],
            "screening_type": ["General Health", 0.95],
            "school_name": ["St. Marys School", 0.95],
            "spinal_caliper_atr": ["5", 0.90]
        }
    }

    captured_prompt = {}

    def mock_extract_images(*args, **kwargs):
        captured_prompt["prompt"] = kwargs.get("prompt")
        captured_prompt["target_group"] = kwargs.get("target_group")
        return mock_res

    with patch("backend.services.claude_service.ClaudeService.extract_from_images", side_effect=mock_extract_images):
        res = ai_extract(
            student_form_bytes=p1_bytes,
            health_assessment_bytes=p2_bytes,
            camp_id=camp["id"],
            template_ids=["tpl_adult_history_v9_1", "tpl_adult_examination_v9_1"],  # Inadvertent client default
            db_path=temp_db
        )

        assert res["success"] is True
        assert res["form_family"] == "paediatric"
        assert res["template_ids"] == ["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"]
        # Must have used paediatric prompt
        assert captured_prompt["prompt"] == PAEDIATRIC_EXTRACTION_PROMPT
        assert captured_prompt["target_group"] == "paediatric"


# ==============================================================================
# 11. End-to-End AI Extraction Rejects Adult Documents with HTTP 422
# ==============================================================================

def test_11_ai_extract_routes_to_adult_with_adult_document_evidence(temp_db):
    """ai_extract returns HTTP 422 DOCUMENT_UNSUPPORTED when adult evidence is present."""
    p1_bytes = _synthetic_doc("ASF-D&A-ACS-9.1/03/25 Comprehensive Health Checkup")
    p2_bytes = _synthetic_doc("ASF-D&A-AC 9.1/03/25 Physician Examination")

    camp = database.create_camp("Adult Health Camp", 100, target_group="adult", db_path=temp_db)

    res = ai_extract(
        student_form_bytes=p1_bytes,
        health_assessment_bytes=p2_bytes,
        camp_id=camp["id"],
        db_path=temp_db
    )

    assert res["success"] is False
    assert res["code"] == "DOCUMENT_UNSUPPORTED"
    assert res.get("http_status") == 422
    assert "Adult screening forms are no longer supported" in res["error"]


# ==============================================================================
# 12. Uncertain Classification Fails Safely with HTTP 422
# ==============================================================================

def test_12_uncertain_classification_fails_safely(temp_db):
    """Blank/unidentified documents with unknown camp return HTTP 422 without silent adult guess."""
    blank_p1 = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xD9"
    blank_p2 = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xD9"

    res = ai_extract(
        student_form_bytes=blank_p1,
        health_assessment_bytes=blank_p2,
        camp_id=None,
        template_ids=None,
        db_path=temp_db
    )

    assert res["success"] is False
    assert res["code"] == "CLASSIFICATION_UNCERTAIN"
    assert res["http_status"] == 422


# ==============================================================================
# 13. API Endpoint Classification for Document Pairs
# ==============================================================================

def test_13_api_classify_endpoint_supports_pair(client):
    """POST /api/templates/classify can resolve document pairs."""
    res = client.post("/api/templates/classify", json={
        "page1_footer": "ASF-D&A-PCS-9.3/07/25",
        "page2_footer": "ASF-D&A-PCS-9.7/07/26"
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["resolved"] is True
    assert data["family"] == "paediatric"
    assert data["template_ids"] == ["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"]


# ==============================================================================
# 14. Authoritative Primary Template ID Not Overwritten by Adult Defaults
# ==============================================================================

def test_14_authoritative_template_id_not_overwritten_by_client_adult_defaults(temp_db):
    """Client-passed adult defaults must not overwrite resolved paediatric templates."""
    p1_bytes = _synthetic_doc("ASF-D&A-PCS-9.3/07/25 Apollo Shine Paediatric History")
    p2_bytes = _synthetic_doc("ASF-D&A-PCS-9.7/07/26 Health Assessment Examination")

    mock_res = {
        "success": True,
        "data": {
            "person_name": ["B. Sadhana", 0.95],
            "height": ["154.4", 0.90],
            "weight": ["35.7", 0.90]
        }
    }

    with patch("backend.services.claude_service.ClaudeService.extract_from_images", return_value=mock_res):
        res = ai_extract(
            student_form_bytes=p1_bytes,
            health_assessment_bytes=p2_bytes,
            camp_id=None,
            template_ids=["tpl_adult_history_v9_1", "tpl_adult_examination_v9_1"],
            db_path=temp_db
        )

        assert res["success"] is True
        assert res["template_id"] == "tpl_paediatric_history_v9_3"
        assert res["template_ids"] == ["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"]
        assert res["form_family"] == "paediatric"


# ==============================================================================
# 15. Two-Page Paediatric Normalization Retains Both Pages' Fields
# ==============================================================================

def test_15_two_page_paediatric_normalization_retains_both_pages_fields(temp_db):
    """Both Page 1 (History) and Page 2 (Examination) fields survive post-extraction normalization."""
    p1_bytes = _synthetic_doc("ASF-D&A-PCS-9.3/07/25 Apollo Shine Paediatric History")
    p2_bytes = _synthetic_doc("ASF-D&A-PCS-9.7/07/26 Health Assessment Examination")

    mock_res = {
        "success": True,
        "data": {
            "person_name": ["B. Sadhana", 0.95],
            "dob": ["08/07/2013", 0.95],
            "blood_group": ["O+ve", 0.90],
            "personal_physician": ["Dr. Sriram", 0.90],
            "height": ["154.4", 0.92],
            "weight": ["35.7", 0.92],
            "spinal_caliper_atr": ["5", 0.88],
            "otoscope_right_ear": ["B/L Wax", 0.85],
            "distant_vision_right": ["6/6", 0.95],
            "distant_vision_left": ["6/6", 0.95]
        }
    }

    with patch("backend.services.claude_service.ClaudeService.extract_from_images", return_value=mock_res):
        res = ai_extract(
            student_form_bytes=p1_bytes,
            health_assessment_bytes=p2_bytes,
            camp_id=None,
            db_path=temp_db
        )

        assert res["success"] is True
        norm = res.get("normalized_fields") or {}

        # Page 1 (History) fields
        assert "person_name" in norm
        assert norm["person_name"]["normalized_value"] == "B. Sadhana"
        assert "dob" in norm

        # Page 2 (Examination) fields
        assert "height" in norm
        assert "weight" in norm
        assert "spinal_caliper_atr" in norm
        assert norm["spinal_caliper_atr"]["normalized_value"] == "5"


# ==============================================================================
# 16. Quota Refund on Classification Failure with record_id=None
# ==============================================================================

def test_16_quota_refunded_on_classification_failure_when_record_id_is_none(temp_db):
    """When document classification fails on a new scan (record_id=None), reserved quota is refunded."""
    org_id = "org_default"
    initial_usage = database.get_usage_summary(org_id=org_id, db_path=temp_db)
    initial_papers_used = initial_usage["papers_used"]

    blank_p1 = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xD9"
    blank_p2 = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xD9"

    res = ai_extract(
        student_form_bytes=blank_p1,
        health_assessment_bytes=blank_p2,
        record_id=None,
        camp_id=None,
        db_path=temp_db
    )

    assert res["success"] is False
    assert res["code"] == "CLASSIFICATION_UNCERTAIN"

    # Papers used must have been restored (0 net change)
    final_usage = database.get_usage_summary(org_id=org_id, db_path=temp_db)
    assert final_usage["papers_used"] == initial_papers_used


# ==============================================================================
# 17. Quota Refund on Classification Failure with Existing record_id
# ==============================================================================

def test_17_quota_refunded_on_classification_failure_with_existing_record_id(temp_db):
    """When classification fails with an existing record_id, reserved quota is refunded."""
    org_id = "org_default"
    initial_usage = database.get_usage_summary(org_id=org_id, db_path=temp_db)
    initial_papers_used = initial_usage["papers_used"]

    blank_p1 = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xD9"
    blank_p2 = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xD9"

    res = ai_extract(
        student_form_bytes=blank_p1,
        health_assessment_bytes=blank_p2,
        record_id="rec_existing_refund_test",
        camp_id=None,
        db_path=temp_db
    )

    assert res["success"] is False
    final_usage = database.get_usage_summary(org_id=org_id, db_path=temp_db)
    assert final_usage["papers_used"] == initial_papers_used


# ==============================================================================
# 18. No Duplicate Quota Refund
# ==============================================================================

def test_18_no_duplicate_quota_refund(temp_db):
    """Safe release paper helper ensures exactly one release even if multiple error paths trigger."""
    org_id = "org_default"
    initial_usage = database.get_usage_summary(org_id=org_id, db_path=temp_db)
    initial_papers_used = initial_usage["papers_used"]

    # If an exception happens inside ClaudeService
    p1_bytes = _synthetic_doc("ASF-D&A-PCS-9.3/07/25")
    p2_bytes = _synthetic_doc("ASF-D&A-PCS-9.7/07/26")

    with patch("backend.services.claude_service.ClaudeService.extract_from_images", side_effect=RuntimeError("API failure")):
        res = ai_extract(
            student_form_bytes=p1_bytes,
            health_assessment_bytes=p2_bytes,
            record_id=None,
            camp_id=None,
            db_path=temp_db
        )

        assert res["success"] is False
        final_usage = database.get_usage_summary(org_id=org_id, db_path=temp_db)
        # Net quota consumed is 0
        assert final_usage["papers_used"] == initial_papers_used


# ==============================================================================
# 19. Free Rescan Quota Preservation
# ==============================================================================

def test_19_free_rescan_quota_preserved(temp_db):
    """Recent record rescan is free: neither reserves extra quota nor decrements on failure."""
    org_id = "org_default"

    # Create camp first to satisfy FK constraint
    camp = database.create_camp("Rescan Test Camp", 50, db_path=temp_db)

    # Pre-seed an existing record so is_recent_record_rescan returns True
    database.insert_record({
        "id": "rec_rescan_test",
        "camp_id": camp["id"],
        "person_name": "Test Child",
        "status": "approved",
        "consent_given": 1
    }, db_path=temp_db)

    initial_usage = database.get_usage_summary(org_id=org_id, db_path=temp_db)
    initial_papers_used = initial_usage["papers_used"]

    blank_p1 = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xD9"
    blank_p2 = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xD9"

    res = ai_extract(
        student_form_bytes=blank_p1,
        health_assessment_bytes=blank_p2,
        record_id="rec_rescan_test",
        camp_id=None,
        db_path=temp_db
    )

    assert res["success"] is False
    final_usage = database.get_usage_summary(org_id=org_id, db_path=temp_db)
    # Free rescan must not consume or refund quota
    assert final_usage["papers_used"] == initial_papers_used


# ==============================================================================
# 20. Paediatric-Only Mode: Active Templates and Adult Rejection Verification
# ==============================================================================

def test_20_active_templates_only_paediatric():
    """Verify that active template engine listing contains exclusively paediatric templates."""
    active_templates = default_template_engine.list_templates(active_only=True)
    assert len(active_templates) == 2
    ids = {t["id"] for t in active_templates}
    assert ids == {"tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"}
    for t in active_templates:
        assert t["family"] == "paediatric"


def test_21_adult_documents_rejected_with_http_422(temp_db):
    """Confirm adult documents return DOCUMENT_UNSUPPORTED and HTTP 422."""
    p1_bytes = _synthetic_doc("ASF-D&A-ACS-9.1/03/25 Adult Medical Record")
    p2_bytes = _synthetic_doc("ASF-D&A-AC 9.1/03/25 Adult Examination")

    res = ai_extract(
        student_form_bytes=p1_bytes,
        health_assessment_bytes=p2_bytes,
        camp_id=None,
        db_path=temp_db
    )

    assert res["success"] is False
    assert res["code"] == "DOCUMENT_UNSUPPORTED"
    assert res.get("http_status") == 422
    assert "Adult screening forms are no longer supported" in res["error"]


def test_22_two_page_screening_workflow_resolves_paediatric_without_modal():
    """Normal two-page screening workflow with paediatric template IDs resolves directly to paediatric pair."""
    pair = default_template_engine.resolve_page_pair(
        p1_footer="",
        p2_footer="",
        client_template_ids=["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"]
    )
    assert pair["resolved"] is True
    assert pair["family"] == "paediatric"
    assert pair["template_ids"] == ["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"]
    assert pair["method"] == "client_explicit_selection"

