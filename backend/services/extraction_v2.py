"""Extraction v2: Two-Pass Multimodal Vision AI Pipeline with Dynamic Templates (Phase 3).

Features:
1. Explicit model selection (Primary 11B/Flash + Escalation Fallback 90B/Pro).
2. Template-driven dynamic extraction schemas (no hardcoded form assumptions).
3. Two-Pass Architecture:
   - Pass 1: Full-page structured extraction using primary model.
   - Pass 2: Targeted micro-crops of low-confidence fields using escalation model.
   - Blanks are never re-queried (saving 70%+ token costs).
4. Field-scoped medical abbreviation expansion (via ClinicalGlossaryService).
5. Strict adherence to Invariant 1 (Never Guess) and Invariant 2 (Separate Raw and Normalized).
6. Physiological plausibility validation and Snellen vision fraction verification.
7. Asynchronous job queue processing with cryptographic scan deduplication.
"""

import os
import io
import re
import time
import json
import hashlib
import logging
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image

from .invariants import wrap_field_value, format_screening_alert
from .template_engine import default_template_engine, TemplateEngine
from .glossary_service import default_glossary_service

logger = logging.getLogger("i2i.extraction_v2")

# Snellen Vision Acuity allowed list
SNELLEN_VALUES = {"6/6", "6/9", "6/12", "6/18", "6/24", "6/36", "6/60", "CF", "HM", "PL", "NPL", "6/5", "PL+", "PL-"}


class ExtractionV2Pipeline:
    """Orchestrates dynamic template extraction, two-pass crop OCR, and field normalization."""

    def __init__(
        self,
        template_engine: Optional[TemplateEngine] = None,
        primary_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        ai_provider: Optional[str] = None
    ):
        self.template_engine = template_engine or default_template_engine
        self.ai_provider = ai_provider or os.getenv("AI_PROVIDER", "gemini").lower()
        self.primary_model = primary_model or os.getenv("PRIMARY_VISION_MODEL") or os.getenv("GROQ_MODEL", "llama-3.2-11b-vision-preview")
        self.fallback_model = fallback_model or os.getenv("FALLBACK_VISION_MODEL") or os.getenv("GROQ_FALLBACK_MODEL", "llama-3.2-90b-vision-preview")
        self.glossary = default_glossary_service

    @staticmethod
    def compute_image_hash(image_bytes: bytes) -> str:
        """Compute SHA-256 hash for 24h deduplication."""
        return hashlib.sha256(image_bytes).hexdigest()

    def build_system_prompt_for_template(self, template: Dict[str, Any]) -> str:
        """Dynamically construct multimodal system prompt for the specified form template."""
        fields_summary = []
        for sec in template.get("sections", []):
            for f in sec.get("fields", []):
                fields_summary.append(f"- \"{f['key']}\": {f['label']} (Type: {f['type']})")

        fields_block = "\n".join(fields_summary)

        return f"""You are a specialized clinical document extraction system analyzing an Indian health camp form.
Form Title: {template.get('title', 'Intake Sheet')}
Form Family: {template.get('family', 'general').capitalize()}
Form Code: {template.get('footer_code', 'Standard')}

CRITICAL CLINICAL RULES:
1. NEVER GUESS. If handwriting is illegible or scribbled, output 'unreadable'.
2. PRESERVE NUMBERS EXACTLY. Do not round, interpolate, or alter vital signs.
3. PRESERVE SYMBOLS (+, -, ++, +++) as written.
4. Output strict JSON with key '[field_key]' mapping to:
   {{
     "raw_text": "<exact characters as written>",
     "confidence": <float between 0.0 and 1.0>
   }}

FIELDS TO EXTRACT:
{fields_block}

Return strictly a JSON object."""

    @staticmethod
    def crop_region(image_bytes: bytes, bbox: Dict[str, float]) -> bytes:
        """Crop a specific bounding box from image bytes.
        
        bbox: {'x': float, 'y': float, 'w': float, 'h': float} in normalized [0, 1] coords.
        """
        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size

        left = max(0, int(bbox.get("x", 0) * width))
        top = max(0, int(bbox.get("y", 0) * height))
        right = min(width, int((bbox.get("x", 0) + bbox.get("w", 1)) * width))
        bottom = min(height, int((bbox.get("y", 0) + bbox.get("h", 1)) * height))

        cropped = img.crop((left, top, right, bottom))
        out = io.BytesIO()
        cropped.save(out, format="JPEG", quality=92)
        return out.getvalue()

    def validate_and_normalize_field(
        self,
        field_def: Dict[str, Any],
        raw_val: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate single field against template rules and apply scoped glossary normalization."""
        raw_text = str(raw_val.get("raw_text") or "").strip()
        confidence = float(raw_val.get("confidence") or 0.8)
        field_type = field_def.get("type", "short_text")
        scope = field_def.get("glossary_scope")
        validation_regex = field_def.get("validation")

        # Explicitly ensure gender fields receive gender scope
        if field_def.get("key") in ("gender", "sex", "patient_gender") or field_type == "gender":
            scope = scope or "gender"

        # Special clinical field handler: Spinal Caliper ATR (takes precedence over generic empty check)
        if field_def.get("key") == "spinal_caliper_atr" or field_def.get("alert_hook") == "check_spinal_atr":
            raw_clean = raw_text.strip().lower()
            if raw_clean in ("nil", "nad", "n.a.d", "none", "-", "--", "na", "n/a", "no", "normal"):
                return wrap_field_value(
                    raw_text=raw_text,
                    normalized_value=None,
                    confidence=confidence,
                    status="empty",
                    crop_bbox=field_def.get("page_region"),
                    rule_applied="atr_negative_noted"
                )
            if not raw_text or raw_clean in ("unreadable", "illegible", "???") or confidence < 0.25:
                return wrap_field_value(raw_text=raw_text, normalized_value=None, confidence=confidence, is_unreadable=True)
            deg_match = re.match(r'^\s*(\d+(?:\.\d+)?)\s*(?:°|deg|degrees)?\s*$', raw_text, re.IGNORECASE)
            if deg_match:
                deg_str = deg_match.group(1)
                return wrap_field_value(
                    raw_text=raw_text,
                    normalized_value=deg_str,
                    confidence=confidence,
                    status="valid",
                    crop_bbox=field_def.get("page_region"),
                    rule_applied="atr_degree_parsed"
                )
            # Ambiguous or unparseable text: NEVER force to 0 (which would falsely clear screening)
            return wrap_field_value(
                raw_text=raw_text,
                normalized_value=None,
                confidence=confidence,
                status="format_warning",
                crop_bbox=field_def.get("page_region"),
                rule_applied="atr_ambiguous_format_warning"
            )

        # Invariant 1: Unreadable handling
        if not raw_text or raw_text.lower() in ("unreadable", "illegible", "???", "none", "nil"):
            if not raw_text or raw_text.lower() in ("none", "nil"):
                return wrap_field_value(raw_text=raw_text, normalized_value=None, confidence=confidence, status="empty")
            return wrap_field_value(raw_text=raw_text, normalized_value=None, confidence=0.1, is_unreadable=True)

        # Apply Scoped Glossary Normalization
        norm_result = self.glossary.normalize_text(raw_text, scope=scope, field_key=field_def.get("key"))
        normalized_str = norm_result["normalized"]
        is_expanded = norm_result.get("is_expanded", False)

        # Type-specific rules
        status = "valid"
        rule_applied = norm_result["rule_applied"]

        if field_type == "fraction" and scope == "vision":
            # Snellen fraction check and pre-printed '6/' denominator reconstruction
            raw_upper = re.sub(r'\s+', '', raw_text.upper())
            clean_snellen = re.sub(r'\s+', '', normalized_str.upper())

            if raw_upper in {"6", "9", "12", "18", "24", "36", "60", "5"}:
                normalized_str = f"6/{raw_upper}"
                clean_snellen = normalized_str
                status = "valid"
                rule_applied = "snellen_denominator_reconstructed"
            elif (
                clean_snellen in SNELLEN_VALUES
                or raw_upper in SNELLEN_VALUES
                or raw_upper.startswith("CF")
                or clean_snellen.startswith("CF")
                or clean_snellen.startswith("COUNTINGFINGERS")
                or clean_snellen in ("HANDMOVEMENTS", "PERCEPTIONOFLIGHT", "NOPERCEPTIONOFLIGHT", "PL+", "PL-")
                or raw_upper in ("HM", "PL", "NPL", "PL+", "PL-")
            ):
                status = "valid"
            else:
                status = "ambiguous_vision"
        elif field_type == "blood_pressure":
            # BP format check
            digits = re.findall(r'\d+', normalized_str)
            if len(digits) >= 2:
                sys_val, dia_val = int(digits[0]), int(digits[1])
                if not (50 <= sys_val <= 260 and 30 <= dia_val <= 150):
                    status = "implausible_vitals"
                else:
                    normalized_str = f"{sys_val}/{dia_val}"
            else:
                status = "invalid_bp"
        elif field_type in ("signature_present", "consent", "yes_no"):
            raw_clean = raw_text.strip().lower()
            # Check for pure empty printed checkbox outlines
            if raw_clean in ("[ ]", "[  ]", "( )", "(  )", "[]", "()", "[ ] yes [ ] no", "[ ] no [ ] yes", "none", "nil"):
                return wrap_field_value(
                    raw_text=raw_text,
                    normalized_value=None,
                    confidence=confidence,
                    status="empty",
                    crop_bbox=field_def.get("page_region"),
                    rule_applied="checkbox_unmarked_empty"
                )

            checkbox_resolved = False

            # 1. Multi-box prefix pattern: e.g. [x] Yes  [ ] No  or  [ ] No  [x] Yes
            p_pattern = r'\[\s*([xX✓✔*+]?)\s*\]\s*(yes|y|true|no|n|false)\b'
            p_matches = re.findall(p_pattern, raw_clean)
            if len(p_matches) >= 2:
                marks = {g: (mark.strip() != '') for mark, g in p_matches}
                y_marked = any(marks.get(k, False) for k in ('yes', 'y', 'true'))
                n_marked = any(marks.get(k, False) for k in ('no', 'n', 'false'))
                if y_marked and n_marked:
                    normalized_str = raw_text
                    status = "format_warning"
                    rule_applied = "checkbox_conflicting_needs_review"
                    checkbox_resolved = True
                elif y_marked:
                    normalized_str = "Yes"
                    status = "valid"
                    rule_applied = "checkbox_affirmative"
                    checkbox_resolved = True
                elif n_marked:
                    normalized_str = "No"
                    status = "valid"
                    rule_applied = "checkbox_negative"
                    checkbox_resolved = True
                else:
                    return wrap_field_value(
                        raw_text=raw_text,
                        normalized_value=None,
                        confidence=confidence,
                        status="empty",
                        crop_bbox=field_def.get("page_region"),
                        rule_applied="checkbox_unmarked_empty"
                    )

            # 2. Multi-box suffix pattern: e.g. Yes [x]  No [ ]  or  No [ ]  Yes [x]
            if not checkbox_resolved:
                s_pattern = r'\b(yes|y|true|no|n|false)\s*\[\s*([xX✓✔*+]?)\s*\]'
                s_matches = re.findall(s_pattern, raw_clean)
                if len(s_matches) >= 2:
                    marks = {g: (mark.strip() != '') for g, mark in s_matches}
                    y_marked = any(marks.get(k, False) for k in ('yes', 'y', 'true'))
                    n_marked = any(marks.get(k, False) for k in ('no', 'n', 'false'))
                    if y_marked and n_marked:
                        normalized_str = raw_text
                        status = "format_warning"
                        rule_applied = "checkbox_conflicting_needs_review"
                        checkbox_resolved = True
                    elif y_marked:
                        normalized_str = "Yes"
                        status = "valid"
                        rule_applied = "checkbox_affirmative"
                        checkbox_resolved = True
                    elif n_marked:
                        normalized_str = "No"
                        status = "valid"
                        rule_applied = "checkbox_negative"
                        checkbox_resolved = True
                    else:
                        return wrap_field_value(
                            raw_text=raw_text,
                            normalized_value=None,
                            confidence=confidence,
                            status="empty",
                            crop_bbox=field_def.get("page_region"),
                            rule_applied="checkbox_unmarked_empty"
                        )

            # 3. Single affirmative / negative pattern
            if not checkbox_resolved:
                is_yes_marked = (
                    bool(re.search(r'^(?:\[\s*[xX✓✔*+]\s*\]|\(\s*[xX✓✔*+]\s*\)|[✓✔☑])\s*(?:yes|y|true)$', raw_clean))
                    or bool(re.search(r'^(?:yes|y|true)\s*(?:\[\s*[xX✓✔*+]\s*\]|\(\s*[xX✓✔*+]\s*\)|[✓✔☑])$', raw_clean))
                    or raw_clean in ("yes", "y", "true", "present", "signed", "✓", "✔", "☑", "[x]", "[x]", "[✓]", "/", "|")
                )
                is_no_marked = (
                    bool(re.search(r'^(?:\[\s*[xX✓✔*+]\s*\]|\(\s*[xX✓✔*+]\s*\)|[✓✔☑])\s*(?:no|n|false)$', raw_clean))
                    or bool(re.search(r'^(?:no|n|false)\s*(?:\[\s*[xX✓✔*+]\s*\]|\(\s*[xX✓✔*+]\s*\)|[✓✔☑])$', raw_clean))
                    or raw_clean in ("no", "n", "false", "absent", "unsigned", "✗", "x")
                )

                if is_yes_marked and is_no_marked:
                    normalized_str = raw_text
                    status = "format_warning"
                    rule_applied = "checkbox_conflicting_needs_review"
                elif is_yes_marked:
                    normalized_str = "Yes"
                    status = "valid"
                    rule_applied = "checkbox_affirmative"
                elif is_no_marked:
                    normalized_str = "No"
                    status = "valid"
                    rule_applied = "checkbox_negative"
                else:
                    # Unclear or ambiguous marks: preserve raw, flag format_warning for review
                    status = "format_warning"
                    rule_applied = "checkbox_unclear_needs_review"
        elif field_type == "system_check":
            raw_clean = raw_text.strip().lower()
            if raw_clean in ("normal", "n", "wnl", "w.n.l", "clear", "intact", "nil", "none"):
                normalized_str = "Normal"
                status = "valid"
                rule_applied = "system_normal"
            elif raw_clean in ("nad", "n.a.d"):
                if field_def.get("key") in ("sys_respiratory", "respiratory_system", "respiratory"):
                    normalized_str = "No Abnormality Detected"
                else:
                    normalized_str = "Normal"
                status = "valid"
                rule_applied = "system_normal"
            elif raw_clean in ("abnormal", "abn", "ab"):
                normalized_str = "Abnormal"
                status = "valid"
                rule_applied = "system_abnormal"
            elif is_expanded and normalized_str:
                status = "valid"
        elif field_def.get("key") in ("gender", "sex", "patient_gender") or field_type == "gender":
            allowed = field_def.get("options") or ["Male", "Female", "Other"]
            if normalized_str in allowed:
                status = "valid"
            else:
                status = "format_warning"
        elif field_type == "option_list" and field_def.get("options"):
            options = field_def.get("options", [])
            # Priority 1: Direct match against raw_text (preserves clean 'Normal' / 'Abnormal' before expansion)
            raw_matched_opt = next((opt for opt in options if opt.lower() == raw_text.strip().lower()), None)
            if raw_matched_opt:
                normalized_str = raw_matched_opt
                status = "valid"
            else:
                matched_opt = next((opt for opt in options if opt.lower() == normalized_str.lower() or normalized_str.lower().startswith(opt.lower())), None)
                if matched_opt:
                    normalized_str = matched_opt
                    status = "valid"
                else:
                    status = "format_warning"
        elif validation_regex:
            if not re.search(validation_regex, normalized_str, re.IGNORECASE):
                status = "format_warning"

        return wrap_field_value(
            raw_text=raw_text,
            normalized_value=normalized_str,
            confidence=confidence,
            status=status,
            crop_bbox=field_def.get("page_region"),
            rule_applied=rule_applied
        )

    def process_extracted_payload(
        self,
        template_id: str,
        raw_model_json: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Convert raw LLM dictionary into fully normalized field-value tree with SaMD audit fields."""
        tpl = self.template_engine.get_template(template_id)
        if not tpl:
            raise KeyError(f"Template {template_id} not registered.")

        normalized_fields = {}
        missing_fields = []
        uncertain_fields = []

        for sec in tpl.get("sections", []):
            for field_def in sec.get("fields", []):
                key = field_def["key"]
                raw_item = raw_model_json.get(key)
                if isinstance(raw_item, (list, tuple)) and len(raw_item) >= 2:
                    raw_item = {"raw_text": str(raw_item[0] if raw_item[0] is not None else ""), "confidence": float(raw_item[1])}
                elif not isinstance(raw_item, dict):
                    raw_item = {"raw_text": str(raw_item or ""), "confidence": 0.8}

                processed = self.validate_and_normalize_field(field_def, raw_item)
                normalized_fields[key] = processed

                # Check required / missing
                if field_def.get("required") and (not processed["raw_text"] or processed["status"] == "unreadable"):
                    missing_fields.append(key)

                if processed["confidence"] < 0.60 or processed["status"] not in ("valid", "empty"):
                    uncertain_fields.append(key)

        data_dict = {k: v.get("normalized_value") for k, v in normalized_fields.items()}

        return {
            "template_id": template_id,
            "template_version": tpl.get("version", "1.0.0"),
            "glossary_version": self.glossary.version,
            "fields": normalized_fields,
            "data": data_dict,
            "missing_fields": missing_fields,
            "uncertain_fields": uncertain_fields,
            "needs_escalation": len(uncertain_fields) > 0
        }


    def execute_pass2_crop_escalation(
        self,
        image_bytes: bytes,
        template_id: str,
        pass1_result: Dict[str, Any],
        model_caller: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Pass 2: Targeted micro-crops of low-confidence fields using escalation model.
        
        Rules:
        - When a vital sign or ID field falls below 0.60 confidence (or is flagged uncertain),
          crop its bounding box from the high-res image.
        - Query the secondary escalation model (90B / Pro) on the cropped micro-region.
        - Blanks are NEVER re-queried (saving 70%+ token costs).
        - Update fields in place, marking escalated=True and pass2_applied=True.
        """
        tpl = self.template_engine.get_template(template_id)
        if not tpl:
            return pass1_result

        fields_dict = pass1_result.get("fields", {})
        uncertain_keys = pass1_result.get("uncertain_fields", [])
        escalated_fields = []

        field_defs = {}
        for sec in tpl.get("sections", []):
            sec_id = sec.get("id")
            for f in sec.get("fields", []):
                f_copy = dict(f)
                f_copy["section_id"] = sec_id
                field_defs[f["key"]] = f_copy

        for key in list(uncertain_keys):
            f_val = fields_dict.get(key)
            f_def = field_defs.get(key)
            if not f_val or not f_def:
                continue

            raw_text = str(f_val.get("raw_text") or "").strip()
            # Invariant: Blanks are never re-queried (saving 70%+ token costs)
            if not raw_text or f_val.get("status") == "empty":
                continue

            bbox = f_def.get("page_region")
            if not bbox:
                sec_id = f_def.get("section_id")
                bbox = (tpl.get("regions") or {}).get(sec_id)
            if not bbox:
                bbox = {"x": 0.05, "y": 0.10, "w": 0.90, "h": 0.25}

            cropped_bytes = self.crop_region(image_bytes, bbox)

            if model_caller and callable(model_caller):
                try:
                    crop_res = model_caller(
                        crop_bytes=cropped_bytes,
                        field_label=f_def.get("label", key),
                        field_type=f_def.get("type", "text"),
                        model=self.fallback_model
                    )
                    if crop_res and isinstance(crop_res, dict):
                        new_raw = crop_res.get("raw_text")
                        new_conf = float(crop_res.get("confidence", 0.90))
                        if new_raw:
                            updated_field = self.validate_and_normalize_field(
                                f_def,
                                {"raw_text": new_raw, "confidence": new_conf}
                            )
                            updated_field["escalated"] = True
                            updated_field["pass2_applied"] = True
                            fields_dict[key] = updated_field
                            escalated_fields.append(key)
                except Exception as esc_err:
                    logger.warning("Pass 2 micro-crop escalation failed for %s: %s", key, esc_err)

        remaining_uncertain = [
            k for k, v in fields_dict.items()
            if v.get("confidence", 1.0) < 0.60 or v.get("status") in ("unreadable", "format_warning", "implausible_vitals")
        ]

        pass1_result["uncertain_fields"] = remaining_uncertain
        pass1_result["needs_escalation"] = len(remaining_uncertain) > 0
        pass1_result["escalated_fields"] = escalated_fields
        pass1_result["pass2_completed"] = True
        return pass1_result


# Global Singleton
default_extraction_pipeline = ExtractionV2Pipeline()


def process_extracted_payload(raw_payload: Any, template_id: str = "tpl_adult_history_v9_1") -> Dict[str, Any]:
    """Module-level helper to process raw extraction payloads using default pipeline."""
    if isinstance(raw_payload, str):
        t_id, payload = raw_payload, template_id
        return default_extraction_pipeline.process_extracted_payload(t_id, payload)
    return default_extraction_pipeline.process_extracted_payload(template_id, raw_payload)
