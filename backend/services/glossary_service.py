"""Clinical Glossary and Medical Abbreviation Disambiguation Service (Phase 3).

Implements Appendix B of the Ink 2 Insight Master Plan:
1. Field-scoped expansion for doctor short forms (148 forms across 8 groups).
2. Controlled medical terminology fuzzy matching (277 terms across 15 clinical topics).
3. Context-aware disambiguation:
   - 'OD' in medication -> 'Once Daily'; in vision -> 'Right Eye (Oculus Dexter)'.
   - 'OS' in medication -> 'Left Eye (Oculus Sinister)'.
   - 'RE' in vision -> 'Right Eye' (or 'Refractive Error' with diopter context).
   - 'Ref' -> 'Referred'; 'R/V' -> 'Review Advised'.
4. Client Safety Invariants:
   - Never invent a diagnosis from a single measurement.
   - Numbers are preserved exactly as written.
   - Unrecognized abbreviations are kept raw and flagged 'new_term'.
"""

import os
import re
import json
import difflib
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("i2i.glossary")

GLOSSARY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "clinical_glossary.json"
)

# Field-scoped explicit overrides
SCOPED_DISAMBIGUATION = {
    "history": {
        "c/o": "Complaints of",
        "co": "Complaints of",
        "no c/o": "No complaints",
        "no co": "No complaints",
        "h/o": "History of",
        "ho": "History of",
        "no h/o": "No history of",
        "no ho": "No history of",
        "k/c/o": "Known case of",
        "kco": "Known case of",
        "n/k/c/o": "Not a known case of",
        "nkco": "Not a known case of",
        "p/h": "Past History",
        "ph": "Past History",
        "f/h": "Family History",
        "fh": "Family History",
        "fh/o": "Family history of",
        "fho": "Family history of",
        "nad": "No Abnormality Detected",
        "wnl": "Within Normal Limits",
        "nil": "Nil significant"
    },
    "medications": {
        "od": "Once Daily (omni die)",
        "o.d": "Once Daily (omni die)",
        "bd": "Twice Daily (bis die)",
        "b.d": "Twice Daily (bis die)",
        "bid": "Twice Daily (bis in die)",
        "tds": "Three Times Daily",
        "t.d.s": "Three Times Daily",
        "tid": "Three Times Daily",
        "qid": "Four Times Daily",
        "sos": "As Needed (si opus sit)",
        "s.o.s": "As Needed (si opus sit)",
        "stat": "Immediately (statim)",
        "hs": "At Bedtime (hora somni)",
        "h.s": "At Bedtime (hora somni)",
        "pr": "Per Rectum",
        "po": "By Mouth (per os)",
        "tab": "Tablet",
        "cap": "Capsule",
        "syr": "Syrup",
        "inj": "Injection"
    },
    "vision": {
        "od": "Right Eye (Oculus Dexter)",
        "o.d": "Right Eye (Oculus Dexter)",
        "os": "Left Eye (Oculus Sinister)",
        "o.s": "Left Eye (Oculus Sinister)",
        "ou": "Both Eyes (Oculus Uterque)",
        "o.u": "Both Eyes (Oculus Uterque)",
        "re": "Right Eye",
        "r.e": "Right Eye",
        "le": "Left Eye",
        "l.e": "Left Eye",
        "va": "Visual Acuity",
        "dva": "Distant Visual Acuity",
        "nva": "Near Visual Acuity",
        "cf": "Counting Fingers",
        "hm": "Hand Movements",
        "pl": "Perception of Light"
    },
    "vitals": {
        "bp": "Blood Pressure",
        "b.p": "Blood Pressure",
        "pr": "Pulse Rate",
        "p.r": "Pulse Rate",
        "hr": "Heart Rate",
        "rr": "Respiratory Rate",
        "wt": "Weight",
        "ht": "Height",
        "bmi": "Body Mass Index",
        "wc": "Waist Circumference",
        "cbg": "Capillary Blood Glucose",
        "rbs": "Random Blood Sugar",
        "fbs": "Fasting Blood Sugar",
        "t": "Temperature",
        "temp": "Temperature"
    },
    "examination": {
        "nad": "No Abnormality Detected",
        "n.a.d": "No Abnormality Detected",
        "wnl": "Within Normal Limits",
        "w.n.l": "Within Normal Limits",
        "rs": "Respiratory System",
        "cvs": "Cardiovascular System",
        "cns": "Central Nervous System",
        "pa": "Per Abdomen",
        "p/a": "Per Abdomen",
        "ent": "Ear, Nose and Throat",
        "nv": "Normal Vesicular Breath Sounds",
        "s1s2": "Normal Heart Sounds (S1, S2 Present)",
        "s1 s2": "Normal Heart Sounds (S1, S2 Present)",
        "s1, s2": "Normal Heart Sounds (S1, S2 Present)",
        "dns": "Deviated Nasal Septum (DNS)",
        "d.n.s": "Deviated Nasal Septum (DNS)",
        "csom": "Chronic Suppurative Otitis Media (CSOM)",
        "c.s.o.m": "Chronic Suppurative Otitis Media (CSOM)",
        "aom": "Acute Otitis Media (AOM)",
        "a.o.m": "Acute Otitis Media (AOM)",
        "tm intact": "Tympanic Membrane Intact",
        "tm dull": "Tympanic Membrane Dull",
        "tm retracted": "Tympanic Membrane Retracted",
        "b/l wax": "Bilateral Wax Impaction",
        "bl wax": "Bilateral Wax Impaction",
        "b/l": "Bilateral",
        "bl": "Bilateral",
        "u/l": "Unilateral",
        "ul": "Unilateral",
        "urti": "Upper Respiratory Tract Infection",
        "lrti": "Lower Respiratory Tract Infection",
        "gerd": "Gastroesophageal Reflux Disease",
        "rct": "Root Canal Treatment",
        "caries": "Dental Caries",
        "wax": "Wax Impaction",
        "cerumen": "Cerumen Impaction",
        "pediculosis": "Pediculosis (Head Lice)",
        "pityriasis alba": "Pityriasis Alba"
    },
    "dental": {
        "caries": "Dental Caries",
        "cavity": "Dental Caries",
        "cavities": "Dental Caries",
        "malocclusion": "Malocclusion",
        "gingivitis": "Gingivitis",
        "stains": "Extrinsic Dental Stains",
        "calculus": "Dental Calculus (Tartar)",
        "fluorosis": "Dental Fluorosis",
        "nad": "Normal Dental Status (NAD)",
        "wnl": "Within Normal Limits"
    },
    "referral": {
        "ref": "Referred to Specialist",
        "refer": "Referred to Specialist",
        "r/v": "Review Advised",
        "rv": "Review Advised",
        "f/u": "Follow-Up Advised",
        "fu": "Follow-Up Advised",
        "sos": "Review SOS if symptoms worsen"
    },
    "gender": {
        "f": "Female",
        "female": "Female",
        "fem": "Female",
        "girl": "Female",
        "woman": "Female",
        "m": "Male",
        "male": "Male",
        "boy": "Male",
        "man": "Male",
        "o": "Other",
        "other": "Other"
    }
}


class ClinicalGlossaryService:
    """Provides field-scoped abbreviation expansion and controlled medical vocabulary normalization."""

    def __init__(self, glossary_path: str = GLOSSARY_PATH):
        self.glossary_path = glossary_path
        self.version = "1.0.0"
        self.short_forms: Dict[str, Dict[str, Any]] = {}
        self.controlled_terms: List[Dict[str, Any]] = []
        self._term_lookup: Dict[str, str] = {}
        self.load_glossary()

    def load_glossary(self) -> None:
        """Load parsed clinical glossary JSON."""
        if not os.path.exists(self.glossary_path):
            logger.warning("Glossary file %s not found. Using defaults.", self.glossary_path)
            return

        with open(self.glossary_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.version = data.get("version", "1.0.0")
        self.short_forms = data.get("short_forms", {})
        self.controlled_terms = data.get("controlled_terms", [])

        # Build lowercase fast lookup for controlled terms
        for item in self.controlled_terms:
            t = item.get("term", "").strip()
            if t:
                self._term_lookup[t.lower()] = t

        logger.info(
            "Clinical Glossary loaded: %d short forms, %d controlled terms (version %s).",
            len(self.short_forms), len(self.controlled_terms), self.version
        )

    def normalize_text(
        self,
        raw_text: Optional[str],
        scope: Optional[str] = None,
        field_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Normalize a clinical text entry using field-scoped rules and controlled vocabularies.
        
        Returns:
            {
                'raw_text': str,
                'normalized': str,
                'is_expanded': bool,
                'scope_used': str or None,
                'rule_applied': str,
                'status': 'exact' | 'scoped_expansion' | 'general_expansion' | 'fuzzy_match' | 'new_term'
            }
        """
        raw = str(raw_text or "").strip()
        if not raw:
            return {
                "raw_text": "",
                "normalized": "",
                "is_expanded": False,
                "scope_used": scope,
                "rule_applied": "empty",
                "status": "exact"
            }

        # Invariant: Numbers and dates should not be altered by glossary
        if re.match(r'^\d+(\.\d+)?(\s*(cm|kg|mmhg|bpm|mg/dl|years|yrs|%))?$', raw, re.IGNORECASE):
            return {
                "raw_text": raw,
                "normalized": raw,
                "is_expanded": False,
                "scope_used": scope,
                "rule_applied": "number_preserved",
                "status": "exact"
            }

        raw_lower = raw.lower().strip()

        # 0. Gender-scoped expansion and protection
        # Prevents gender abbreviations like 'F' / 'M' from expanding to generic doctor abbreviations (e.g. 'Follow-up')
        is_gender_context = (
            scope == "gender"
            or (field_key is not None and field_key.lower() in ("gender", "sex", "patient_gender"))
        )
        if is_gender_context:
            resolved_gender = None
            gender_status = None
            rule_applied = None
            gender_map = SCOPED_DISAMBIGUATION.get("gender", {})

            # Check for pure empty printed checkbox outline
            if raw_lower in ("[ ]", "[  ]", "( )", "(  )", "[]", "()"):
                return {
                    "raw_text": raw,
                    "normalized": "",
                    "is_expanded": False,
                    "scope_used": "gender",
                    "rule_applied": "empty_checkbox_outline",
                    "status": "unmarked_checkbox"
                }

            # 1. Multi-box prefix pattern: e.g. [ ] M  [x] F
            prefix_pattern = r'\[\s*([xX✓✔*+]?)\s*\]\s*(m|male|boy|man|f|female|girl|woman)\b'
            prefix_matches = re.findall(prefix_pattern, raw_lower)
            if len(prefix_matches) >= 2:
                marks = {g: (mark.strip() != '') for mark, g in prefix_matches}
                m_marked = any(marks.get(k, False) for k in ('m', 'male', 'boy', 'man'))
                f_marked = any(marks.get(k, False) for k in ('f', 'female', 'girl', 'woman'))
                if m_marked and f_marked:
                    return {
                        "raw_text": raw,
                        "normalized": raw,
                        "is_expanded": False,
                        "scope_used": "gender",
                        "rule_applied": "ambiguous_conflicting_checkboxes",
                        "status": "ambiguous_gender"
                    }
                elif m_marked:
                    resolved_gender = "Male"
                    rule_applied = "gender_prefix_checkbox_male"
                    gender_status = "scoped_expansion"
                elif f_marked:
                    resolved_gender = "Female"
                    rule_applied = "gender_prefix_checkbox_female"
                    gender_status = "scoped_expansion"
                else:
                    return {
                        "raw_text": raw,
                        "normalized": "",
                        "is_expanded": False,
                        "scope_used": "gender",
                        "rule_applied": "unmarked_dual_checkboxes",
                        "status": "unmarked_checkbox"
                    }

            # 2. Multi-box suffix pattern: e.g. M [ ]  F [x]
            if not resolved_gender:
                suffix_pattern = r'\b(m|male|boy|man|f|female|girl|woman)\s*\[\s*([xX✓✔*+]?)\s*\]'
                suffix_matches = re.findall(suffix_pattern, raw_lower)
                if len(suffix_matches) >= 2:
                    marks = {g: (mark.strip() != '') for g, mark in suffix_matches}
                    m_marked = any(marks.get(k, False) for k in ('m', 'male', 'boy', 'man'))
                    f_marked = any(marks.get(k, False) for k in ('f', 'female', 'girl', 'woman'))
                    if m_marked and f_marked:
                        return {
                            "raw_text": raw,
                            "normalized": raw,
                            "is_expanded": False,
                            "scope_used": "gender",
                            "rule_applied": "ambiguous_conflicting_checkboxes",
                            "status": "ambiguous_gender"
                        }
                    elif m_marked:
                        resolved_gender = "Male"
                        rule_applied = "gender_suffix_checkbox_male"
                        gender_status = "scoped_expansion"
                    elif f_marked:
                        resolved_gender = "Female"
                        rule_applied = "gender_suffix_checkbox_female"
                        gender_status = "scoped_expansion"
                    else:
                        return {
                            "raw_text": raw,
                            "normalized": "",
                            "is_expanded": False,
                            "scope_used": "gender",
                            "rule_applied": "unmarked_dual_checkboxes",
                            "status": "unmarked_checkbox"
                        }

            # 3. Direct dictionary match
            if not resolved_gender and raw_lower in gender_map:
                resolved_gender = gender_map[raw_lower]
                rule_applied = f"gender_exact_{resolved_gender.lower()}"
                gender_status = "exact" if raw == resolved_gender else "scoped_expansion"

            # 4. Single token with tick/check/cross prefix or suffix
            if not resolved_gender:
                m_box = re.match(r'^(?:\[\s*[xX✓✔*+]\s*\]|\(\s*[xX✓✔*+]\s*\)|[✓✔☑])\s*(f|female|fem|girl|woman|m|male|boy|man|o|other)$', raw_lower)
                if not m_box:
                    m_box = re.match(r'^(f|female|fem|girl|woman|m|male|boy|man|o|other)\s*(?:\[\s*[xX✓✔*+]\s*\]|\(\s*[xX✓✔*+]\s*\)|[✓✔☑])$', raw_lower)
                if m_box:
                    token = m_box.group(1)
                    resolved_gender = gender_map.get(token)
                    rule_applied = f"gender_box_{resolved_gender.lower()}"
                    gender_status = "scoped_expansion"

            if resolved_gender:
                return {
                    "raw_text": raw,
                    "normalized": resolved_gender,
                    "is_expanded": (raw != resolved_gender),
                    "scope_used": "gender",
                    "rule_applied": rule_applied or f"gender_{raw_lower}_{resolved_gender.lower()}",
                    "status": gender_status or ("exact" if raw == resolved_gender else "scoped_expansion")
                }

            # Unrecognized or ambiguous in gender context: preserve raw, flag new_term, never fall through to doctor short forms
            return {
                "raw_text": raw,
                "normalized": raw,
                "is_expanded": False,
                "scope_used": "gender",
                "rule_applied": "unrecognized_gender_kept_as_written",
                "status": "new_term"
            }

        # 0.1 Clinical symbols expansion (+, -, ++, +++, ↑, ↓, −)
        CLINICAL_SYMBOLS = {
            "+": "Present / Positive (+)",
            "++": "Moderate (++)",
            "+++": "Severe / Strongly Positive (+++)",
            "-": "Absent / Negative (-)",
            "−": "Absent / Negative (-)",
            "↑": "Increased / Elevated (↑)",
            "↓": "Decreased / Low (↓)",
        }
        if raw in CLINICAL_SYMBOLS:
            return {
                "raw_text": raw,
                "normalized": CLINICAL_SYMBOLS[raw],
                "is_expanded": True,
                "scope_used": scope or "symbols",
                "rule_applied": f"clinical_symbol_{raw}",
                "status": "exact"
            }

        # 1. Field-scoped expansion (Highest Priority)
        # Prevents 'OD' in meds from becoming 'Right Eye'
        if scope and scope in SCOPED_DISAMBIGUATION:
            scope_map = SCOPED_DISAMBIGUATION[scope]
            if raw_lower in scope_map:
                return {
                    "raw_text": raw,
                    "normalized": scope_map[raw_lower],
                    "is_expanded": True,
                    "scope_used": scope,
                    "rule_applied": f"scoped_{scope}_{raw_lower}",
                    "status": "scoped_expansion"
                }

        # 1.5 Common clinical shorthand with slashes & camp abbreviations (Unscoped Fallback)
        SLASH_SHORTHANDS = {
            "c/o": "Complaints of",
            "co": "Complaints of",
            "no c/o": "No complaints",
            "no co": "No complaints",
            "h/o": "History of",
            "ho": "History of",
            "no h/o": "No history of",
            "no ho": "No history of",
            "p/h": "Past History",
            "ph": "Past History",
            "f/h": "Family History",
            "fh": "Family History",
            "fh/o": "Family history of",
            "fho": "Family history of",
            "k/c/o": "Known case of",
            "kco": "Known case of",
            "n/k/c/o": "Not a known case of",
            "nkco": "Not a known case of",
            "b/l": "Bilateral",
            "bl": "Bilateral",
            "u/l": "Unilateral",
            "ul": "Unilateral",
            "r/v": "Review Advised",
            "rv": "Review Advised",
            "f/u": "Follow-Up Advised",
            "fu": "Follow-Up Advised",
            "p/a": "Per Abdomen",
            "pa": "Per Abdomen",
            "o/e": "On Examination",
            "oe": "On Examination",
            "g/e": "General Examination",
            "ge": "General Examination",
            "dns": "Deviated Nasal Septum (DNS)",
            "csom": "Chronic Suppurative Otitis Media (CSOM)",
            "aom": "Acute Otitis Media (AOM)",
            "urti": "Upper Respiratory Tract Infection",
            "lrti": "Lower Respiratory Tract Infection",
            "gerd": "Gastroesophageal Reflux Disease",
            "rct": "Root Canal Treatment",
            "caries": "Dental Caries",
            "nad": "No Abnormality Detected",
            "wnl": "Within Normal Limits",
        }
        if raw_lower in SLASH_SHORTHANDS:
            return {
                "raw_text": raw,
                "normalized": SLASH_SHORTHANDS[raw_lower],
                "is_expanded": True,
                "scope_used": scope or "general_clinical",
                "rule_applied": f"slash_shorthand_{raw_lower}",
                "status": "general_expansion"
            }

        # 1.6 Eye/Ear Laterality shorthands (R -> Right, L -> Left)
        is_laterality_context = (
            scope in ("vision", "examination", "otoscopy")
            or (field_key is not None and any(w in field_key.lower() for w in ("ear", "eye", "otoscope", "vision", "side")))
        )
        if is_laterality_context:
            if raw_lower in ("r", "rt", "r."):
                return {
                    "raw_text": raw,
                    "normalized": "Right",
                    "is_expanded": True,
                    "scope_used": scope or "examination",
                    "rule_applied": "laterality_right",
                    "status": "scoped_expansion"
                }
            if raw_lower in ("l", "lt", "l."):
                return {
                    "raw_text": raw,
                    "normalized": "Left",
                    "is_expanded": True,
                    "scope_used": scope or "examination",
                    "rule_applied": "laterality_left",
                    "status": "scoped_expansion"
                }

        # 2. General Doctor Short Forms Lookup
        # Exact match in short forms table (guarding against single-letter slash-split noise)
        clean_token = re.sub(r'[^a-z0-9]', '', raw_lower)
        # NOTE: "f" and "m" are excluded from CORRUPTED_SINGLE_LETTERS intentionally:
        # - In gender context they are already short-circuited above (expand to Female/Male).
        # - In non-gender clinical context "F" = Follow-up / "M" = Murmur (doctor shorthand).
        # Other single-letter tokens are ambiguous noise from OCR slash-splits.
        CORRUPTED_SINGLE_LETTERS = {"r", "l", "b", "p", "k", "e", "g", "a", "u", "v", "c", "o", "h"}
        if clean_token not in CORRUPTED_SINGLE_LETTERS:
            for abbrev, entry in self.short_forms.items():
                abbrev_clean = re.sub(r'[^a-z0-9]', '', abbrev.lower())
                if abbrev_clean and clean_token == abbrev_clean:
                    return {
                        "raw_text": raw,
                        "normalized": entry["expansion"],
                        "is_expanded": True,
                        "scope_used": entry.get("group"),
                        "rule_applied": f"short_form_{abbrev}",
                        "status": "general_expansion"
                    }

        # 3. Exact match against Controlled Medical Terminology
        if raw_lower in self._term_lookup:
            return {
                "raw_text": raw,
                "normalized": self._term_lookup[raw_lower],
                "is_expanded": False,
                "scope_used": scope,
                "rule_applied": "controlled_exact",
                "status": "exact"
            }

        # 4. Fuzzy match against Controlled Medical Terminology
        # Tolerates minor OCR misspellings (e.g., 'otitis mediaa' -> 'Otitis media')
        matches = difflib.get_close_matches(raw_lower, self._term_lookup.keys(), n=1, cutoff=0.84)
        if matches:
            canonical = self._term_lookup[matches[0]]
            return {
                "raw_text": raw,
                "normalized": canonical,
                "is_expanded": True,
                "scope_used": scope,
                "rule_applied": f"fuzzy_match_{matches[0]}",
                "status": "fuzzy_match"
            }

        # 5. Unknown / Ambiguous shorthand: Keep as written, flag 'new_term'
        return {
            "raw_text": raw,
            "normalized": raw,
            "is_expanded": False,
            "scope_used": scope,
            "rule_applied": "unrecognized_kept_as_written",
            "status": "new_term"
        }

    def expand_multiple_tokens(self, text: str, scope: Optional[str] = None) -> str:
        """Tokenize text and expand known abbreviations in multi-word doctor notes."""
        if not text:
            return ""
        tokens = re.split(r'(\s+|,|\.)', text)
        expanded_tokens = []
        for tok in tokens:
            stripped = tok.strip().lower()
            if not stripped:
                expanded_tokens.append(tok)
                continue
            norm = self.normalize_text(stripped, scope=scope)
            if norm["is_expanded"]:
                expanded_tokens.append(norm["normalized"])
            else:
                expanded_tokens.append(tok)
        return "".join(expanded_tokens)


# Global Singleton
default_glossary_service = ClinicalGlossaryService()
