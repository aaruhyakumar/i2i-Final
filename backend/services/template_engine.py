"""Template Engine for Ink 2 Insight (I2I).

Decouples intake forms from hardcoded schemas into declarative versioned templates.
Supports:
1. Template loading and strict schema validation
2. Optical page classification via footer version codes and title heuristics
3. Case Pairing Engine (pairing history + examination into a unified Case)
4. Dynamic UI schema generation for frontend rendering
5. Dynamic extraction schema generation for vision AI prompts
6. Multi-template export column mapping
"""

import os
import re
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("i2i.template_engine")

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


class TemplateValidationError(Exception):
    """Raised when a template definition violates the specification schema."""
    pass


class TemplateEngine:
    """Manages declarative clinical form templates, page classification, and case pairing."""

    def __init__(self, templates_dir: str = TEMPLATES_DIR):
        self.templates_dir = templates_dir
        self.templates: Dict[str, Dict[str, Any]] = {}
        self.footer_index: Dict[str, str] = {}
        self.load_all_templates()

    def load_all_templates(self) -> int:
        """Scan directory and load all valid template JSON definitions."""
        self.templates.clear()
        self.footer_index.clear()
        
        if not os.path.exists(self.templates_dir):
            os.makedirs(self.templates_dir, exist_ok=True)
            return 0

        loaded_count = 0
        for filename in sorted(os.listdir(self.templates_dir)):
            if filename.endswith(".json"):
                filepath = os.path.join(self.templates_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        tpl_data = json.load(f)
                    self.validate_template_schema(tpl_data)
                    from backend.services.field_permissions import is_identity_field
                    for sec in tpl_data.get("sections", []):
                        for fld in sec.get("fields", []):
                            if "group" not in fld:
                                fld["group"] = "identity" if is_identity_field(fld.get("key")) else "clinical"
                            if "writer" not in fld:
                                fld["writer"] = "field_worker" if fld["group"] == "identity" else "doctor"

                    tpl_id = tpl_data["id"]
                    self.templates[tpl_id] = tpl_data
                    
                    footer = tpl_data.get("footer_code", "").strip()
                    if footer:
                        self.footer_index[footer.lower()] = tpl_id
                        
                    loaded_count += 1
                except Exception as e:
                    logger.error("Failed to load template %s: %s", filename, e)
                    raise TemplateValidationError(f"Invalid template {filename}: {e}")

        logger.info("Loaded %d declarative templates successfully.", loaded_count)
        return loaded_count

    @staticmethod
    def validate_template_schema(tpl: Dict[str, Any]) -> None:
        """Validate that template satisfies the canonical specification."""
        required_keys = ["id", "family", "page_type", "footer_code", "version", "sections"]
        for key in required_keys:
            if key not in tpl:
                raise TemplateValidationError(f"Missing mandatory template key: '{key}'")

        if tpl["family"] not in ("adult", "paediatric", "custom"):
            raise TemplateValidationError(f"Invalid family '{tpl['family']}' in template {tpl.get('id')}")

        if tpl["page_type"] not in ("history", "examination", "consent", "screening"):
            raise TemplateValidationError(f"Invalid page_type '{tpl['page_type']}' in template {tpl.get('id')}")

        if not isinstance(tpl["sections"], list) or len(tpl["sections"]) == 0:
            raise TemplateValidationError(f"Template {tpl.get('id')} must contain at least one section.")

        for sec in tpl["sections"]:
            if "id" not in sec or "title" not in sec or "fields" not in sec:
                raise TemplateValidationError(f"Section missing id, title, or fields in {tpl.get('id')}")
            for field in sec["fields"]:
                if "key" not in field or "label" not in field or "type" not in field:
                    raise TemplateValidationError(f"Field missing key, label, or type in {tpl.get('id')}:{sec.get('id')}")

    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve template by unique identifier."""
        return self.templates.get(template_id)

    def list_templates(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """List summary of loaded declarative templates.
        
        When active_only is True (default), lists only active screening templates (paediatric).
        Retired adult templates remain loaded internally for historical schema lookups.
        """
        templates = self.templates.values()
        if active_only:
            templates = [t for t in templates if t.get("family") == "paediatric"]
        return [
            {
                "id": t["id"],
                "family": t["family"],
                "page_type": t["page_type"],
                "footer_code": t["footer_code"],
                "version": t["version"],
                "title": t.get("title", ""),
                "fields_count": sum(len(s.get("fields", [])) for s in t.get("sections", []))
            }
            for t in templates
        ]

    def classify_page(
        self,
        footer_code_detected: Optional[str] = None,
        header_text_detected: Optional[str] = None
    ) -> Dict[str, Any]:
        """Classify page template from footer code and header text triggers.
        
        Returns:
            {
                'matched': bool,
                'template_id': str or None,
                'template': dict or None,
                'confidence': float,
                'method': 'footer_exact' | 'footer_fuzzy' | 'title_keyword' | 'unsupported',
                'warning': str or None
            }
        """
        footer = (footer_code_detected or "").strip().lower()
        
        # 1. Exact footer match
        if footer and footer in self.footer_index:
            tpl_id = self.footer_index[footer]
            tpl = self.templates[tpl_id]
            if tpl.get("family") == "adult":
                return {
                    "matched": False,
                    "template_id": None,
                    "template": None,
                    "family": "unsupported_adult",
                    "confidence": 0.0,
                    "method": "unsupported",
                    "warning": f"Adult screening form '{footer_code_detected}' is not supported. Ink2Insights is exclusively for paediatric screening (ASF-D&A-PCS-9.3 / 9.7)."
                }
            return {
                "matched": True,
                "template_id": tpl_id,
                "template": tpl,
                "confidence": 1.0,
                "method": "footer_exact",
                "warning": None
            }

        # 2. Normalized / Token footer match (handles spaces, dashes, OCR variations e.g. "PCS 9.3", "PCS-9.7", "asf d&a pcs 9.3")
        if footer:
            clean_norm = re.sub(r'[^a-z0-9]', '', footer)
            if "pcs93" in clean_norm or "pcs9.3" in footer:
                tpl_id = "tpl_paediatric_history_v9_3"
                if tpl_id in self.templates:
                    return {
                        "matched": True,
                        "template_id": tpl_id,
                        "template": self.templates[tpl_id],
                        "confidence": 0.95,
                        "method": "footer_fuzzy",
                        "warning": None
                    }
            elif "pcs97" in clean_norm or "pcs9.7" in footer:
                tpl_id = "tpl_paediatric_examination_v9_7"
                if tpl_id in self.templates:
                    return {
                        "matched": True,
                        "template_id": tpl_id,
                        "template": self.templates[tpl_id],
                        "confidence": 0.95,
                        "method": "footer_fuzzy",
                        "warning": None
                    }
            elif (
                "acs91" in clean_norm
                or "acs9.1" in footer
                or "ac91" in clean_norm
                or "ac9.1" in footer
                or "acs-9.1" in footer
                or "ac-9.1" in footer
            ):
                return {
                    "matched": False,
                    "template_id": None,
                    "template": None,
                    "family": "unsupported_adult",
                    "confidence": 0.0,
                    "method": "unsupported",
                    "warning": "Adult screening form (ASF-D&A-ACS-9.1 / AC-9.1) is not supported. Ink2Insights is exclusively for paediatric screening (ASF-D&A-PCS-9.3 / 9.7)."
                }

            # General fuzzy match across registered footers (non-adult)
            for registered_footer, tpl_id in self.footer_index.items():
                tpl = self.templates.get(tpl_id)
                if not tpl or tpl.get("family") == "adult":
                    continue
                core_sig = registered_footer.replace("asf-d&a-", "").strip()
                if core_sig in footer or footer in registered_footer:
                    return {
                        "matched": True,
                        "template_id": tpl_id,
                        "template": tpl,
                        "confidence": 0.88,
                        "method": "footer_fuzzy",
                        "warning": f"Fuzzy matched footer code: '{footer}' -> '{registered_footer}'"
                    }

        # 3. Title / Header keyword heuristic fallback
        header = (header_text_detected or "").lower()
        if header:
            # Paediatric Examination cues (Health assessment, otoscope, spinal caliper, office use only)
            if any(k in header for k in ["otoscope", "spinal caliper", "caliper atr", "for office use only", "office use only"]):
                tpl_id = "tpl_paediatric_examination_v9_7"
                if tpl_id in self.templates:
                    return {
                        "matched": True,
                        "template_id": tpl_id,
                        "template": self.templates[tpl_id],
                        "confidence": 0.85,
                        "method": "title_keyword",
                        "warning": "Classified via paediatric examination keywords."
                    }
            # Paediatric History cues (Student info, mediscreening, parents, pediatrician)
            if any(k in header for k in ["paediatric mediscreening", "pediatric mediscreening", "student's personal information", "student personal information", "to be filled in by parents", "please mention pediatrician"]):
                tpl_id = "tpl_paediatric_history_v9_3"
                if tpl_id in self.templates:
                    return {
                        "matched": True,
                        "template_id": tpl_id,
                        "template": self.templates[tpl_id],
                        "confidence": 0.85,
                        "method": "title_keyword",
                        "warning": "Classified via paediatric history keywords."
                    }
            if "paediatric" in header or "pediatric" in header or "school health" in header:
                if "examination" in header or "doctor" in header or "office use" in header or "otoscope" in header:
                    tpl_id = "tpl_paediatric_examination_v9_7"
                else:
                    tpl_id = "tpl_paediatric_history_v9_3"
                if tpl_id in self.templates:
                    return {
                        "matched": True,
                        "template_id": tpl_id,
                        "template": self.templates[tpl_id],
                        "confidence": 0.75,
                        "method": "title_keyword",
                        "warning": "Classified via header keywords; footer code was unreadable."
                    }
            elif "adult" in header or "staff" in header or "college" in header:
                return {
                    "matched": False,
                    "template_id": None,
                    "template": None,
                    "family": "unsupported_adult",
                    "confidence": 0.0,
                    "method": "unsupported",
                    "warning": "Adult screening documents are not supported. Ink2Insights is exclusively for paediatric screening."
                }

        # 4. Unknown / Unsupported form
        return {
            "matched": False,
            "template_id": None,
            "template": None,
            "confidence": 0.0,
            "method": "unsupported",
            "warning": f"Unsupported or unknown intake form. Detected footer: '{footer_code_detected}'."
        }

    @staticmethod
    def detect_evidence_from_bytes(data: Optional[bytes]) -> Dict[str, Optional[str]]:
        """Fast binary metadata and synthetic test fixture scanner.
        
        NOTE: This scans raw bytes/text streams (such as synthetic test payloads or embedded
        EXIF/text headers). It does NOT perform optical character recognition (OCR) on compressed
        raster pixels (JPEG/PNG). Real camera photographs require OCR or external form codes.
        """
        if not data or not isinstance(data, (bytes, bytearray)):
            return {"footer": None, "header": None}

        sample = data if len(data) <= 131072 else (data[:65536] + data[-65536:])
        try:
            text = sample.decode("utf-8", errors="ignore")
        except Exception:
            text = ""

        detected_footer = None
        detected_header = None
        t_clean = re.sub(r'[^a-zA-Z0-9]', '', text).lower()
        t_lower = text.lower()

        if "asfdapcs93" in t_clean or "pcs93" in t_clean:
            detected_footer = "ASF-D&A-PCS-9.3/07/25"
        elif "asfdapcs97" in t_clean or "pcs97" in t_clean:
            detected_footer = "ASF-D&A-PCS-9.7/07/26"
        elif "asfdaacs91" in t_clean or "acs91" in t_clean:
            detected_footer = "ASF-D&A-ACS-9.1/03/25"
        elif "asfdaac91" in t_clean or "ac91" in t_clean:
            detected_footer = "ASF-D&A-AC 9.1/03/25"

        if "paediatric mediscreening" in t_lower or "pediatric mediscreening" in t_lower or "student's personal information" in t_lower:
            detected_header = "Paediatric mediscreening"
        elif "otoscope" in t_lower or "spinal caliper" in t_lower or "caliper atr" in t_lower or "for office use only" in t_lower:
            detected_header = "Paediatric Examination Health Assessment"

        return {"footer": detected_footer, "header": detected_header}

    def resolve_page_pair(
        self,
        p1_footer: Optional[str] = None,
        p1_header: Optional[str] = None,
        p2_footer: Optional[str] = None,
        p2_header: Optional[str] = None,
        camp_target_group: Optional[str] = None,
        client_template_ids: Optional[List[str]] = None,
        p1_bytes: Optional[bytes] = None,
        p2_bytes: Optional[bytes] = None
    ) -> Dict[str, Any]:
        """Resolve document classification and template routing for a two-page intake pair.
        
        Guarantees:
        1. Recognize ASF-D&A-PCS-9.3 as paediatric history.
        2. Recognize ASF-D&A-PCS-9.7 as paediatric examination.
        3. Prevent default adult template IDs from overriding reliable paediatric document evidence.
        4. Avoid relying exclusively on camp_target_group or client-supplied template_ids.
        5. Return explicit classification uncertainty rather than silently defaulting to adult.
        """
        # Step A: Extract byte cues if bytes provided
        b1 = self.detect_evidence_from_bytes(p1_bytes)
        b2 = self.detect_evidence_from_bytes(p2_bytes)

        eff_p1_footer = (p1_footer or "").strip() or b1["footer"]
        eff_p1_header = (p1_header or "").strip() or b1["header"]
        eff_p2_footer = (p2_footer or "").strip() or b2["footer"]
        eff_p2_header = (p2_header or "").strip() or b2["header"]

        # Step B: Classify individual pages
        c1 = self.classify_page(footer_code_detected=eff_p1_footer, header_text_detected=eff_p1_header)
        c2 = self.classify_page(footer_code_detected=eff_p2_footer, header_text_detected=eff_p2_header)

        t1_id = c1.get("template_id")
        t2_id = c2.get("template_id")
        t1_fam = c1.get("template", {}).get("family") if c1.get("template") else c1.get("family")
        t2_fam = c2.get("template", {}).get("family") if c2.get("template") else c2.get("family")

        # Step C: Check for Inverted Slots (Examination in Slot 1, History in Slot 2)
        if t1_id == "tpl_paediatric_examination_v9_7" and t2_id == "tpl_paediatric_history_v9_3":
            return {
                "resolved": False,
                "family": "paediatric",
                "template_ids": None,
                "confidence": 0.0,
                "code": "SLOTS_INVERTED",
                "error": "Inverted page order detected: Slot 1 contains Examination form (ASF-D&A-PCS-9.7) and Slot 2 contains History form (ASF-D&A-PCS-9.3). Please swap the page slots.",
                "page1": c1,
                "page2": c2
            }

        # Step D: Check for Mixed Families (e.g. Adult on one page, Paediatric on other)
        t1_norm_fam = "adult" if t1_fam in ("adult", "unsupported_adult") else t1_fam
        t2_norm_fam = "adult" if t2_fam in ("adult", "unsupported_adult") else t2_fam
        if t1_norm_fam and t2_norm_fam and t1_norm_fam != t2_norm_fam:
            return {
                "resolved": False,
                "family": "uncertain",
                "template_ids": None,
                "confidence": 0.0,
                "code": "FAMILY_MISMATCH",
                "error": f"Mixed form families detected: Page 1 is {t1_fam} ({t1_id}) but Page 2 is {t2_fam} ({t2_id}). Both pages must belong to the same form suite.",
                "page1": c1,
                "page2": c2
            }

        # Step E: Reliable Document Evidence on Both Pages (Paediatric)
        if t1_id == "tpl_paediatric_history_v9_3" and t2_id == "tpl_paediatric_examination_v9_7":
            return {
                "resolved": True,
                "family": "paediatric",
                "template_ids": ["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"],
                "confidence": min(c1.get("confidence", 1.0), c2.get("confidence", 1.0)),
                "method": "document_evidence_match",
                "reason": "Both pages verified by reliable paediatric document evidence (ASF-D&A-PCS-9.3 / ASF-D&A-PCS-9.7).",
                "page1": c1,
                "page2": c2
            }

        # Step F: Document Pages are Adult Forms (Reject adult screening forms)
        if (
            t1_norm_fam == "adult"
            or t2_norm_fam == "adult"
            or (c1.get("method") == "unsupported" and "Adult" in str(c1.get("warning", "")))
            or (c2.get("method") == "unsupported" and "Adult" in str(c2.get("warning", "")))
        ):
            return {
                "resolved": False,
                "family": "unsupported",
                "template_ids": None,
                "confidence": 0.0,
                "code": "DOCUMENT_UNSUPPORTED",
                "error": "Adult screening forms are no longer supported. Ink2Insights is exclusively for paediatric screening (ASF-D&A-PCS-9.3 History and ASF-D&A-PCS-9.7 Examination).",
                "page1": c1,
                "page2": c2
            }

        # Step G: Partial Document Evidence (One page confirmed paediatric, other unreadable/unmarked)
        if t1_id == "tpl_paediatric_history_v9_3" or t2_id == "tpl_paediatric_examination_v9_7":
            return {
                "resolved": True,
                "family": "paediatric",
                "template_ids": ["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"],
                "confidence": 0.85,
                "method": "partial_document_evidence",
                "reason": "Classified as paediatric suite based on verified paediatric form code.",
                "page1": c1,
                "page2": c2
            }

        # Step H: Footers unreadable / missing on both pages.
        # Evaluate client-supplied templates and camp target group safeguards.
        camp_norm = (camp_target_group or "").strip().lower()
        is_camp_paediatric = camp_norm in ("paediatric", "pediatric", "school", "child")

        client_list = client_template_ids or []
        is_client_paediatric = any("paediatric" in str(tid).lower() for tid in client_list)
        is_client_adult = any("adult" in str(tid).lower() or "acs91" in str(tid).lower() or "ac91" in str(tid).lower() for tid in client_list)

        # 1. Camp target group is paediatric: default adult client IDs MUST NOT override paediatric camp
        if is_camp_paediatric:
            return {
                "resolved": True,
                "family": "paediatric",
                "template_ids": ["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"],
                "confidence": 0.80,
                "method": "camp_target_group",
                "reason": "Classified as paediatric suite based on camp target group configuration.",
                "page1": c1,
                "page2": c2
            }

        # 2. Client explicitly selected paediatric templates
        if is_client_paediatric:
            return {
                "resolved": True,
                "family": "paediatric",
                "template_ids": ["tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"],
                "confidence": 0.80,
                "method": "client_explicit_selection",
                "reason": "Classified as paediatric suite based on explicit template selection.",
                "page1": c1,
                "page2": c2
            }

        # 3. Client explicitly selected adult templates (Reject unsupported adult request)
        if is_client_adult:
            return {
                "resolved": False,
                "family": "unsupported",
                "template_ids": None,
                "confidence": 0.0,
                "code": "DOCUMENT_UNSUPPORTED",
                "error": "Adult screening forms are no longer supported. Ink2Insights is exclusively for paediatric screening (ASF-D&A-PCS-9.3 History and ASF-D&A-PCS-9.7 Examination).",
                "page1": c1,
                "page2": c2
            }

        # 4. Classification is UNCERTAIN: Missing footers, unknown camp, and default or missing templates.
        # DO NOT SILENTLY FALL BACK TO ADULT!
        return {
            "resolved": False,
            "family": "uncertain",
            "template_ids": None,
            "confidence": 0.0,
            "code": "CLASSIFICATION_UNCERTAIN",
            "error": "Document classification uncertain: no recognizable footer code (e.g. ASF-D&A-PCS-9.3/9.7) or template evidence found. Please provide clear form footers or explicitly select the form template.",
            "page1": c1,
            "page2": c2
        }

    @staticmethod
    def _normalize_name(name: Optional[str]) -> str:
        """Helper to normalize names for fuzzy pairing."""
        if not name:
            return ""
        # Remove titles and noise
        s = re.sub(r'^(dr|mr|mrs|ms|master|baby)[\s.]+', '', str(name).strip().lower())
        # Keep only alphanumeric tokens
        s = re.sub(r'[^a-z0-9]', '', s)
        return s

    def pair_case(
        self,
        history_page_data: Dict[str, Any],
        exam_page_data: Dict[str, Any],
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Pair one History page and one Examination page into a unified Case.
        
        Invariants:
        1. Never merge silently if data conflicts.
        2. Must be 1 History page and 1 Examination page.
        3. Families must match (Adult with Adult, Paediatric with Paediatric).
        4. When IDs exist (Moc Doc ID, Admission ID), they MUST match.
        5. For Adult Exam (lacks ID), pair if same capture session AND name matches.
        
        Returns:
            {
                'status': 'paired' | 'needs_pairing' | 'mismatched',
                'confidence': float,
                'pairing_method': str,
                'reason': str,
                'join_key': str or None
            }
        """
        # Validate page types
        h_type = history_page_data.get("page_type") or "history"
        e_type = exam_page_data.get("page_type") or "examination"
        if h_type != "history" or e_type != "examination":
            return {
                "status": "mismatched",
                "confidence": 0.0,
                "pairing_method": "type_check",
                "reason": f"Cannot pair pages: requires 1 history and 1 examination page (got {h_type} and {e_type}).",
                "join_key": None
            }

        # Validate families
        h_family = history_page_data.get("family") or "unknown"
        e_family = exam_page_data.get("family") or "unknown"
        if h_family != e_family:
            return {
                "status": "mismatched",
                "confidence": 0.0,
                "pairing_method": "family_check",
                "reason": f"Family mismatch: cannot pair {h_family} history with {e_family} examination.",
                "join_key": None
            }

        # 1. Match by IDs (Moc Doc ID or Admission ID)
        h_moc = (history_page_data.get("moc_doc_id") or "").strip().lower()
        e_moc = (exam_page_data.get("moc_doc_id") or "").strip().lower()
        h_adm = (history_page_data.get("admission_id") or "").strip().lower()
        e_adm = (exam_page_data.get("admission_id") or "").strip().lower()

        # If both pages have Moc Doc ID
        if h_moc and e_moc:
            if h_moc == e_moc:
                return {
                    "status": "paired",
                    "confidence": 1.0,
                    "pairing_method": "moc_doc_id",
                    "reason": f"Matched by exact Moc Doc ID '{h_moc}'.",
                    "join_key": h_moc
                }
            else:
                return {
                    "status": "mismatched",
                    "confidence": 0.0,
                    "pairing_method": "moc_doc_id",
                    "reason": f"Conflicting Moc Doc IDs: history has '{h_moc}', exam has '{e_moc}'.",
                    "join_key": None
                }

        # If both pages have Admission ID
        if h_adm and e_adm:
            if h_adm == e_adm:
                return {
                    "status": "paired",
                    "confidence": 0.99,
                    "pairing_method": "admission_id",
                    "reason": f"Matched by exact Admission ID '{h_adm}'.",
                    "join_key": h_adm
                }
            else:
                return {
                    "status": "mismatched",
                    "confidence": 0.0,
                    "pairing_method": "admission_id",
                    "reason": f"Conflicting Admission IDs: history has '{h_adm}', exam has '{e_adm}'.",
                    "join_key": None
                }

        # 2. Adult Examination Special Case (No ID on Adult Exam sheet)
        # Relies on Session Capture pairing + Name verification
        h_name_norm = self._normalize_name(history_page_data.get("person_name"))
        e_name_norm = self._normalize_name(exam_page_data.get("person_name"))

        h_session = history_page_data.get("session_id") or session_id
        e_session = exam_page_data.get("session_id") or session_id

        if h_session and e_session and h_session != e_session:
            return {
                "status": "needs_pairing",
                "confidence": 0.5,
                "pairing_method": "different_sessions",
                "reason": f"Pages belong to different capture sessions ('{h_session}' vs '{e_session}'). Requires physician verification.",
                "join_key": None
            }

        is_same_session = bool(h_session and e_session and h_session == e_session)

        # Name matching
        is_name_match = False
        if h_name_norm and e_name_norm:
            if h_name_norm == e_name_norm or h_name_norm in e_name_norm or e_name_norm in h_name_norm:
                is_name_match = True

        if is_name_match:
            return {
                "status": "paired",
                "confidence": 0.95 if is_same_session else 0.90,
                "pairing_method": "session_and_name" if is_same_session else "name_match",
                "reason": f"Paired by matching name '{history_page_data.get('person_name')}'.",
                "join_key": h_adm or h_moc or h_name_norm
            }

        # Mismatch or missing link
        return {
            "status": "needs_pairing",
            "confidence": 0.1,
            "pairing_method": "unpaired",
            "reason": "Could not establish safe pairing key between history and examination pages.",
            "join_key": None
        }

    def generate_ui_schema(self, template_id: str) -> Dict[str, Any]:
        """Produce structured layout schema for dynamic frontend tab/field rendering."""
        tpl = self.get_template(template_id)
        if not tpl:
            raise KeyError(f"Template {template_id} not found.")

        tabs = []
        for sec in tpl.get("sections", []):
            fields = []
            for f in sec.get("fields", []):
                fields.append({
                    "key": f["key"],
                    "label": f["label"],
                    "type": f["type"],
                    "required": f.get("required", False),
                    "options": f.get("options", []),
                    "validation": f.get("validation"),
                    "glossary_scope": f.get("glossary_scope"),
                    "alert_hook": f.get("alert_hook")
                })
            tabs.append({
                "id": sec["id"],
                "title": sec["title"],
                "fields": fields
            })

        return {
            "template_id": tpl["id"],
            "title": tpl.get("title", ""),
            "family": tpl["family"],
            "page_type": tpl["page_type"],
            "version": tpl["version"],
            "tabs": tabs,
            "sections": tabs
        }

    def generate_extraction_prompt(self, template_id: str) -> str:
        """Construct multimodal vision AI prompt dynamically from a single template definition."""
        return self.build_extraction_prompt([template_id])

    def build_extraction_prompt(self, template_ids: List[str]) -> str:
        """Construct multimodal vision AI prompt dynamically from one or more template definitions."""
        templates = []
        for tid in template_ids:
            tpl = self.get_template(tid)
            if tpl:
                templates.append(tpl)

        titles = ", ".join(t.get("title", t["id"]) for t in templates) if templates else "Clinical Intake Sheet"

        prompt_lines = [
            f"You are reading photographed pages of a clinical health screening record ({titles}).",
            "Read printed text, handwriting, and ticked/marked boxes.",
            "Return only valid JSON. For every field return [value, confidence], where value is a string or null and confidence is a number from 0.0 to 1.0.",
            "Rules:",
            "- Never guess. If a field is blank, return [null, 1.0].",
            "- If you cannot read it or it is illegible, return [null, 0.4].",
            "- Yes or No fields return exactly 'Yes', 'No' or null.",
            "- Normal or abnormal fields return exactly 'Normal', 'Abnormal' or null, with details in the _d field.",
            "- Copy numbers exactly as written. Do not convert units.",
            "- Doctors' shorthand may appear: BP, PR, HR, Wt, Ht, RBS, NAD, WNL, HTN, T2DM, Adv, F/U, Ref, Rx, O/E, H/O, C/O.",
            "- Do not diagnose or add medical advice.",
            "",
            "Field keys to extract:"
        ]

        for tpl in templates:
            for sec in tpl.get("sections", []):
                sec_fields = []
                for f in sec.get("fields", []):
                    k = f["key"]
                    lbl = f.get("label", k)
                    hints = []
                    if f.get("writer"):
                        hints.append(f"writer: {f['writer']}")
                    if f.get("validation"):
                        hints.append(f"format: {f['validation']}")
                    hint_str = f" ({', '.join(hints)})" if hints else ""
                    sec_fields.append(f"{k} [{lbl}{hint_str}]")
                prompt_lines.append(f"{sec.get('title', sec['id'])}: {', '.join(sec_fields)}")

        return "\n".join(prompt_lines)

    def get_export_columns(self, template_id: str) -> List[Tuple[str, str]]:
        """Return list of (field_key, export_column) for spreadsheet export."""
        tpl = self.get_template(template_id)
        if not tpl:
            return []
        cols = []
        for sec in tpl.get("sections", []):
            for f in sec.get("fields", []):
                col_name = f.get("export_column") or f["key"]
                cols.append((f["key"], col_name))
        return cols

    def resolve_form_id(self, form_id: str) -> str:
        """Resolve form alias to canonical form identifier."""
        norm = (form_id or "").strip().lower()
        alias_map = {
            "adult": "adult_composite",
            "adult_composite": "adult_composite",
            "adult_history": "tpl_adult_history_v9_1",
            "tpl_adult_history_v9_1": "tpl_adult_history_v9_1",
            "adult_examination": "tpl_adult_examination_v9_1",
            "adult_exam": "tpl_adult_examination_v9_1",
            "tpl_adult_examination_v9_1": "tpl_adult_examination_v9_1",
            "paediatric": "paediatric_composite",
            "pediatric": "paediatric_composite",
            "paediatric_composite": "paediatric_composite",
            "paediatric_history": "tpl_paediatric_history_v9_3",
            "pediatric_history": "tpl_paediatric_history_v9_3",
            "tpl_paediatric_history_v9_3": "tpl_paediatric_history_v9_3",
            "paediatric_examination": "tpl_paediatric_examination_v9_7",
            "pediatric_examination": "tpl_paediatric_examination_v9_7",
            "paediatric_exam": "tpl_paediatric_examination_v9_7",
            "tpl_paediatric_examination_v9_7": "tpl_paediatric_examination_v9_7",
        }
        return alias_map.get(norm, norm)

    def list_forms(self) -> List[Dict[str, Any]]:
        """List all available form definitions (individual page sheets + composite 2-page suites)."""
        forms = [
            {
                "id": "adult_composite",
                "alias": "adult",
                "family": "adult",
                "type": "composite",
                "title": "Adult / University Health Screening Suite (2-Page Case Unit)",
                "document_codes": ["ASF-D&A-ACS-9.1/03/25", "ASF-D&A-AC 9.1/03/25"],
                "pages_count": 2,
                "description": "Comprehensive adult health camp intake covering Page 1 Personal History & Demographics and Page 2 Clinical Examination, Vitals & V/A Vision.",
                "source_documents": ["Adult History-ver 9.1.pdf", "Adult Examination-ver 9.1.pdf"]
            },
            {
                "id": "paediatric_composite",
                "alias": "paediatric",
                "family": "paediatric",
                "type": "composite",
                "title": "Paediatric / School Health Screening Suite (2-Page Case Unit)",
                "document_codes": ["ASF-D&A-PCS-9.3/07/25", "ASF-D&A-PCS-9.7/07/26"],
                "pages_count": 2,
                "description": "Comprehensive school paediatric screening covering Page 1 Child History & Parental Consent and Page 2 Biometrics, ATR Caliper, Otoscopy, Caries & Vision.",
                "source_documents": ["Paediatric-History-ver 9.3.pdf", "Paediatric-Examination-ver 9.7.pdf"]
            },
            {
                "id": "tpl_adult_history_v9_1",
                "alias": "adult_history",
                "family": "adult",
                "type": "single_page",
                "title": "Adult Health Intake Form (Page 1: History & Demographics)",
                "document_codes": ["ASF-D&A-ACS-9.1/03/25"],
                "pages_count": 1,
                "description": "Student/Staff personal info, 8-condition medical history, family risk history, and signed screening consent.",
                "source_documents": ["Adult History-ver 9.1.pdf"]
            },
            {
                "id": "tpl_adult_examination_v9_1",
                "alias": "adult_examination",
                "family": "adult",
                "type": "single_page",
                "title": "Adult Health Examination Form (Page 2: Clinical Examination & Vitals)",
                "document_codes": ["ASF-D&A-AC 9.1/03/25"],
                "pages_count": 1,
                "description": "Physician assessment covering vitals (BP/RBS/Waist), general signs, systemic exam, distant vision 6/6, dental, and referrals.",
                "source_documents": ["Adult Examination-ver 9.1.pdf"]
            },
            {
                "id": "tpl_paediatric_history_v9_3",
                "alias": "paediatric_history",
                "family": "paediatric",
                "type": "single_page",
                "title": "Paediatric Health Assessment (Page 1: Student History & Consent)",
                "document_codes": ["ASF-D&A-PCS-9.3/07/25"],
                "pages_count": 1,
                "description": "Child demographics, class/sec, allergies, immunization, behaviour/lifestyle concerns, and parental consent.",
                "source_documents": ["Paediatric-History-ver 9.3.pdf"]
            },
            {
                "id": "tpl_paediatric_examination_v9_7",
                "alias": "paediatric_examination",
                "family": "paediatric",
                "type": "single_page",
                "title": "Paediatric Clinical Examination (Page 2: Doctor Assessment)",
                "document_codes": ["ASF-D&A-PCS-9.7/07/26"],
                "pages_count": 1,
                "description": "Growth biometrics, Adams FBT ATR caliper degrees, otoscope right/left ear, ENT/skin/hair exam, distant vision, and caries/dental advice.",
                "source_documents": ["Paediatric-Examination-ver 9.7.pdf"]
            }
        ]

        # Attach attribute counts dynamically
        for item in forms:
            attrs = self.get_form_attributes(item["id"])
            item["attributes_count"] = len(attrs)
            item["sections_count"] = len(set(a["section_id"] for a in attrs))

        return forms

    def get_form_structure(self, form_id: str) -> Optional[Dict[str, Any]]:
        """Return hierarchical form structure including pages, sections, and field definitions."""
        canon_id = self.resolve_form_id(form_id)

        # Composite forms
        if canon_id == "adult_composite":
            h_tpl = self.get_template("tpl_adult_history_v9_1")
            e_tpl = self.get_template("tpl_adult_examination_v9_1")
            if not h_tpl or not e_tpl:
                return None
            return {
                "id": "adult_composite",
                "alias": "adult",
                "family": "adult",
                "type": "composite",
                "title": "Adult Health Screening Suite (2-Page Unit)",
                "footer_codes": [h_tpl.get("footer_code"), e_tpl.get("footer_code")],
                "version": "9.1",
                "source_documents": ["Adult History-ver 9.1.pdf", "Adult Examination-ver 9.1.pdf"],
                "pages": [
                    {
                        "page_number": 1,
                        "page_type": "history",
                        "title": h_tpl.get("title"),
                        "footer_code": h_tpl.get("footer_code"),
                        "sections": h_tpl.get("sections", [])
                    },
                    {
                        "page_number": 2,
                        "page_type": "examination",
                        "title": e_tpl.get("title"),
                        "footer_code": e_tpl.get("footer_code"),
                        "sections": e_tpl.get("sections", [])
                    }
                ]
            }

        if canon_id == "paediatric_composite":
            h_tpl = self.get_template("tpl_paediatric_history_v9_3")
            e_tpl = self.get_template("tpl_paediatric_examination_v9_7")
            if not h_tpl or not e_tpl:
                return None
            return {
                "id": "paediatric_composite",
                "alias": "paediatric",
                "family": "paediatric",
                "type": "composite",
                "title": "Paediatric / School Health Screening Suite (2-Page Unit)",
                "footer_codes": [h_tpl.get("footer_code"), e_tpl.get("footer_code")],
                "version": "9.3 / 9.7",
                "source_documents": ["Paediatric-History-ver 9.3.pdf", "Paediatric-Examination-ver 9.7.pdf"],
                "pages": [
                    {
                        "page_number": 1,
                        "page_type": "history",
                        "title": h_tpl.get("title"),
                        "footer_code": h_tpl.get("footer_code"),
                        "sections": h_tpl.get("sections", [])
                    },
                    {
                        "page_number": 2,
                        "page_type": "examination",
                        "title": e_tpl.get("title"),
                        "footer_code": e_tpl.get("footer_code"),
                        "sections": e_tpl.get("sections", [])
                    }
                ]
            }

        # Single page templates
        tpl = self.get_template(canon_id)
        if not tpl:
            return None

        return {
            "id": tpl["id"],
            "alias": canon_id.replace("tpl_", "").replace("_v9_1", "").replace("_v9_3", "").replace("_v9_7", ""),
            "family": tpl["family"],
            "type": "single_page",
            "title": tpl.get("title"),
            "footer_codes": [tpl.get("footer_code")],
            "version": tpl.get("version"),
            "pages": [
                {
                    "page_number": 1,
                    "page_type": tpl.get("page_type"),
                    "title": tpl.get("title"),
                    "footer_code": tpl.get("footer_code"),
                    "sections": tpl.get("sections", [])
                }
            ]
        }

    def get_form_attributes(self, form_id: str) -> List[Dict[str, Any]]:
        """Return flat list of all attributes and data dictionary properties for a form."""
        structure = self.get_form_structure(form_id)
        if not structure:
            return []

        canon_id = self.resolve_form_id(form_id)
        legacy_adult_keys = {
            "full_name", "parent_guardian_name", "emergency_contact_phone", "birth_history",
            "milestones", "immunization_status", "dietary_habits", "allergy", "allergy_d",
            "medications", "medications_d", "other_issues", "signature_present",
            "height_cm", "weight_kg", "bp", "head_circumference", "muac", "vision_type",
            "distant_vision_right", "distant_vision_left", "color_vision", "squint_strabismus",
            "dental", "dental_caries", "malocclusion", "oral_hygiene", "examination_details",
            "specialist", "referral_reason"
        }

        attributes = []
        for p in structure.get("pages", []):
            page_no = p.get("page_number", 1)
            page_type = p.get("page_type", "form")
            for sec in p.get("sections", []):
                sec_id = sec.get("id")
                sec_title = sec.get("title")
                for fld in sec.get("fields", []):
                    key = fld.get("key")
                    if canon_id in {"paediatric_composite", "tpl_paediatric_history_v9_3", "tpl_paediatric_examination_v9_7"} and key in legacy_adult_keys:
                        continue
                    attributes.append({
                        "key": key,
                        "label": fld.get("label"),
                        "type": fld.get("type"),
                        "page_number": page_no,
                        "page_type": page_type,
                        "section_id": sec_id,
                        "section_title": sec_title,
                        "writer": fld.get("writer", "unknown"),
                        "required": fld.get("required", False),
                        "options": fld.get("options", []),
                        "validation": fld.get("validation"),
                        "glossary_scope": fld.get("glossary_scope"),
                        "export_column": fld.get("export_column") or fld.get("key"),
                        "alert_hook": fld.get("alert_hook")
                    })

        return attributes


# Global Singleton
default_template_engine = TemplateEngine()

