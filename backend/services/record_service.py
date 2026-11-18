import io
import csv
import sys
import os
import re
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import xlsxwriter

logger = logging.getLogger("doc_scanner.record_service")

# Ensure project root in path for flexible execution
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from backend import database
    from backend.services.paediatric_screening import (
        classify_paediatric,
        classify_paediatric_bmi,
        classify_paediatric_bp,
        classify_atr,
    )
except ImportError:
    import database
    from services.paediatric_screening import (
        classify_paediatric,
        classify_paediatric_bmi,
        classify_paediatric_bp,
        classify_atr,
    )

DANGEROUS_CSV_PREFIXES = ('=', '+', '-', '@', '\t', '\r')

def sanitize_csv_cell(value: Any) -> str:
    """Sanitize cell against CSV / Spreadsheet formula injection.
    If a string starts with =, +, -, @, tab, or carriage return,
    prepend a single quote (') so spreadsheet engines treat it strictly as text.
    """
    if value is None:
        return ""
    s = str(value)
    if s.startswith(DANGEROUS_CSV_PREFIXES) or s.lstrip().startswith(('=', '+', '-', '@')):
        return f"'{s}"
    return s.strip()

class RecordService:
    """Business logic for camp records."""

    def __init__(self, db_path: str = database.DEFAULT_DB_PATH):
        self.db_path = db_path

    @staticmethod
    def validate_record_fields(data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate clinical fields according to medical norms.
        Returns: { 'is_valid': bool, 'errors': dict, 'warnings': dict }
        """
        errors = {}
        warnings = {}

        # 1. Person Name
        name = (data.get("person_name") or "").strip()
        if not name:
            errors["person_name"] = "Patient name is required."

        # 2. Age: Numeric, 0-150
        age = data.get("age")
        if age not in (None, ""):
            age_str = str(age).strip()
            digits = re.findall(r'\d+', age_str)
            if digits:
                val = int(digits[0])
                if val < 0 or val > 150:
                    errors["age"] = f"Age must be between 0 and 150 (got {val})."
            else:
                warnings["age"] = f"Could not determine numeric age from '{age_str}'."

        # 3. Blood Pressure: XXX/XX format & realistic systolic/diastolic ranges
        bp = data.get("blood_pressure")
        if bp not in (None, ""):
            bp_str = str(bp).strip()
            if "/" in bp_str:
                parts = bp_str.split("/")
                s_digits = re.findall(r'\d+', parts[0])
                d_digits = re.findall(r'\d+', parts[1])
                if s_digits and d_digits:
                    systolic = int(s_digits[0])
                    diastolic = int(d_digits[0])
                    if systolic < 50 or systolic > 260:
                        warnings["blood_pressure"] = f"Systolic pressure {systolic} is outside standard clinical range (50-260 mmHg)."
                    if diastolic < 30 or diastolic > 160:
                        warnings["blood_pressure"] = f"Diastolic pressure {diastolic} is outside standard clinical range (30-160 mmHg)."
                    if systolic <= diastolic:
                        errors["blood_pressure"] = f"Systolic ({systolic}) must be greater than diastolic ({diastolic})."
                else:
                    warnings["blood_pressure"] = "BP format should be systolic/diastolic like 120/80."
            else:
                warnings["blood_pressure"] = "BP should be in format XXX/XX like 120/80."

        # 4. Height: 30-250 cm
        height = data.get("height")
        if height not in (None, ""):
            h_str = str(height).strip()
            h_digits = re.findall(r'\d+', h_str)
            if h_digits:
                h_val = int(h_digits[0])
                if h_val < 30 or h_val > 250:
                    warnings["height"] = f"Height {h_val} cm seems outside typical range (30-250 cm)."

        # 5. Weight: 0.5-300 kg
        weight = data.get("weight")
        if weight not in (None, ""):
            w_str = str(weight).strip()
            w_digits = re.findall(r'[\d.]+', w_str)
            if w_digits:
                try:
                    w_val = float(w_digits[0])
                    if w_val < 0.5 or w_val > 300:
                        warnings["weight"] = f"Weight {w_val} kg seems outside typical range (0.5-300 kg)."
                except ValueError:
                    pass

        # 6. Gender: Male, Female, Other
        gender = data.get("gender")
        if gender not in (None, ""):
            g_clean = str(gender).strip().capitalize()
            if not any(k in g_clean for k in ("Male", "Female", "Other")):
                warnings["gender"] = f"Unusual gender value '{gender}' (expected Male, Female, or Other)."

        return {
            "is_valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }

    def validate_camp(self, camp_id: str) -> Optional[Dict[str, Any]]:
        """Verify camp existence."""
        camp = database.get_camp_by_id(camp_id, self.db_path)
        return camp

    def create_record(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and create a new scanned record in 'needs_review' state."""
        camp_id = data.get("camp_id")
        if not camp_id:
            raise ValueError("Camp ID is required.")

        camp = self.validate_camp(camp_id)
        if not camp:
            raise ValueError(f"Camp '{camp_id}' does not exist.")

        person_name = (data.get("person_name") or "").strip()
        if not person_name:
            raise ValueError("Person's name is required to create a patient record.")

        # Ensure status is 'needs_review' initially
        record_payload = {
            "id": data.get("id"),
            "camp_id": camp_id,
            "person_name": person_name,
            "age": data.get("age"),
            "gender": data.get("gender"),
            "height": data.get("height"),
            "weight": data.get("weight"),
            "blood_pressure": data.get("blood_pressure"),
            "examination_details": data.get("examination_details"),
            "referral_details": data.get("referral_details"),
            "missing_fields": data.get("missing_fields", []),
            "uncertain_fields": data.get("uncertain_fields", []),
            "extraction_notes": data.get("extraction_notes"),
            "structured_record": data.get("structured_record", {}),
            "status": "needs_review",
            "page1_preview": data.get("page1_preview"),
            "page2_preview": data.get("page2_preview"),
            "original_ai_extraction": data.get("original_ai_extraction") or data.get("structured_record", {}),
            "quality_warning_bypassed": data.get("quality_warning_bypassed", []),
            "quality_bypassed": 1 if data.get("quality_bypassed") else 0,
            "target_group": data.get("target_group") or (camp.get("target_group") if camp else "adult"),
            "is_paediatric": 1 if (data.get("is_paediatric") or (camp and camp.get("target_group") == "paediatric")) else 0,
            "scanned_by": data.get("scanned_by"),
            "community_id": data.get("community_id") or (camp.get("community_id") if camp else None),
            "template_id": data.get("template_id"),
            "template_version": data.get("template_version"),
            "form_family": data.get("form_family"),
            "reviewer_notes": None,
            "reviewed_by": None,
            "reviewed_at": None
        }

        # Check for duplicate submission if an explicit ID was passed
        if record_payload["id"]:
            existing = database.get_record_by_id(record_payload["id"], self.db_path)
            if existing:
                return existing

        created = database.insert_record(record_payload, self.db_path)
        return created

    def review_record(self, record_id: str, updates: Dict[str, Any], reviewer: str) -> Dict[str, Any]:
        """Explicitly approve/review a record with updated fields."""
        existing = database.get_record_by_id(record_id, self.db_path)
        if not existing:
            try:
                from backend.services.supabase_service import supabase_service
                if supabase_service.is_available():
                    sup_rec = supabase_service.get_record_by_id(record_id)
                    if sup_rec:
                        database.insert_record(sup_rec, self.db_path)
                        existing = database.get_record_by_id(record_id, self.db_path)
            except Exception:
                pass

        if not existing and updates.get("person_name"):
            # Self-healing synthesis if record was in ephemeral state
            synth_payload = dict(updates)
            synth_payload["id"] = record_id
            synth_payload["status"] = "needs_review"
            if not synth_payload.get("camp_id"):
                synth_payload["camp_id"] = "camp_default"
            database.insert_record(synth_payload, self.db_path)
            existing = database.get_record_by_id(record_id, self.db_path)

        if not existing:
            raise ValueError(f"Record with ID '{record_id}' not found.")

        person_name = updates.get("person_name", existing["person_name"]).strip()
        if not person_name:
            raise ValueError("Person's name cannot be blank upon review.")

        if not reviewer or not reviewer.strip():
            reviewer = "Camp Doctor (Reviewer)"

        updated = database.update_reviewed_record(record_id, updates, reviewer.strip(), self.db_path)
        if not updated:
            raise RuntimeError(f"Failed to update record '{record_id}'.")
        return updated

    def generate_csv_export(
        self,
        camp_id: Optional[str] = None,
        only_reviewed: bool = False,
        community_id: Optional[str] = None,
        org_id: Optional[str] = None
    ) -> str:
        """Generate RFC 4180 compliant CSV of records with formula injection protection."""
        status_filter = "reviewed" if only_reviewed else None
        target_org = org_id or community_id
        local_records = database.list_records(
            camp_id=camp_id,
            status=status_filter,
            community_id=target_org,
            limit=10000,
            db_path=self.db_path
        ) or []
        remote_records = []
        try:
            from backend.services.supabase_service import supabase_service
            if supabase_service.is_available():
                sup_recs = supabase_service.list_records(
                    camp_id=camp_id,
                    status=status_filter,
                    community_id=target_org,
                    limit=10000
                )
                if sup_recs:
                    remote_records = sup_recs
        except Exception:
            pass
        merged_map = {r["id"]: r for r in remote_records}
        for r in local_records:
            merged_map[r["id"]] = r
        records = list(merged_map.values())

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")

        headers = [
            "Record ID",
            "Camp ID",
            "Camp Name",
            "Document ID",
            "Admission ID",
            "School / College",
            "Staff / Student",
            "Person Name",
            "Age",
            "Gender",
            "DOB",
            "Blood Group",
            "Mobile Number",
            "Address",
            "Height",
            "Weight",
            "Blood Pressure",
            "Pulse (bpm)",
            "RBS (mg/dL)",
            "Waist Circumference (cm)",
            "Known Allergies",
            "Previous Surgery / Hospitalization",
            "Physical Activity",
            "Social History",
            "Vision Right Eye",
            "Vision Left Eye",
            "Examination Details",
            "Referral Details",
            "Status",
            "Reviewed By",
            "Reviewed At",
            "Reviewer Notes",
            "Extraction Notes",
            "Created At"
        ]
        writer.writerow(headers)

        for r in records:
            sr = r.get("structured_record") or {}
            if isinstance(sr, str):
                try:
                    sr = json.loads(sr)
                except Exception:
                    sr = {}
            p_info = sr.get("personal_information") if isinstance(sr.get("personal_information"), dict) else {}
            m_hist = sr.get("medical_history") if isinstance(sr.get("medical_history"), dict) else {}
            h_asmt = sr.get("health_assessment") if isinstance(sr.get("health_assessment"), dict) else {}
            vis = sr.get("vision") if isinstance(sr.get("vision"), dict) else {}

            def _get_f(obj, k):
                val = obj.get(k)
                if isinstance(val, dict):
                    return val.get("value")
                return val

            def _get_d(obj, k):
                val = obj.get(k)
                if isinstance(val, dict):
                    return val.get("details") or val.get("value")
                return val

            doc_id = _get_f(p_info, "doc_id")
            adm_id = _get_f(p_info, "admission_id")
            school = _get_f(p_info, "school_college_name")
            staff_student = _get_f(p_info, "staff_student_type")
            dob = _get_f(p_info, "dob")
            b_group = _get_f(p_info, "blood_group")
            mobile = _get_f(p_info, "mobile_number")
            address = _get_f(p_info, "address")

            pulse = _get_f(h_asmt, "pulse_bpm")
            rbs = _get_f(h_asmt, "random_blood_sugar_mgdl")
            waist = _get_f(h_asmt, "waist_circumference_cm")

            allergies = _get_d(m_hist, "known_allergies")
            surgery = _get_d(m_hist, "previous_surgery_hospitalization")
            activity = _get_d(m_hist, "physical_activity")
            social = _get_d(m_hist, "social_history")

            va_right = _get_f(vis, "distant_vision_right")
            va_left = _get_f(vis, "distant_vision_left")

            row = [
                sanitize_csv_cell(r.get("id")),
                sanitize_csv_cell(r.get("camp_id")),
                sanitize_csv_cell(r.get("camp_name")),
                sanitize_csv_cell(doc_id),
                sanitize_csv_cell(adm_id),
                sanitize_csv_cell(school),
                sanitize_csv_cell(staff_student),
                sanitize_csv_cell(r.get("person_name")),
                sanitize_csv_cell(r.get("age")),
                sanitize_csv_cell(r.get("gender")),
                sanitize_csv_cell(dob),
                sanitize_csv_cell(b_group),
                sanitize_csv_cell(mobile),
                sanitize_csv_cell(address),
                sanitize_csv_cell(r.get("height")),
                sanitize_csv_cell(r.get("weight")),
                sanitize_csv_cell(r.get("blood_pressure")),
                sanitize_csv_cell(pulse),
                sanitize_csv_cell(rbs),
                sanitize_csv_cell(waist),
                sanitize_csv_cell(allergies),
                sanitize_csv_cell(surgery),
                sanitize_csv_cell(activity),
                sanitize_csv_cell(social),
                sanitize_csv_cell(va_right),
                sanitize_csv_cell(va_left),
                sanitize_csv_cell(r.get("examination_details")),
                sanitize_csv_cell(r.get("referral_details")),
                sanitize_csv_cell(r.get("status")),
                sanitize_csv_cell(r.get("reviewed_by")),
                sanitize_csv_cell(r.get("reviewed_at")),
                sanitize_csv_cell(r.get("reviewer_notes")),
                sanitize_csv_cell(r.get("extraction_notes")),
                sanitize_csv_cell(r.get("created_at")),
            ]
            writer.writerow(row)

        return output.getvalue()

    @staticmethod
    def _write_attributes_sheet(wb, form_id: str, tab_color: str, header_bg: str, sheet_title: Optional[str] = None) -> None:
        """Write declarative Form Structure & Attributes Dictionary as Sheet for clinical auditability."""
        from backend.services.template_engine import default_template_engine

        title = sheet_title or "Form Attributes"
        ws_attrs = wb.add_worksheet(title)
        ws_attrs.set_tab_color(tab_color)
        ws_attrs.freeze_panes(1, 2)

        attr_hdr_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': header_bg, 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#E2E8F0', 'text_wrap': True
        })
        attr_cell_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9,
            'valign': 'vcenter', 'border': 1, 'border_color': '#E2E8F0'
        })
        attr_cell_alt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'valign': 'vcenter',
            'bg_color': '#F8FAFC', 'border': 1, 'border_color': '#E2E8F0'
        })
        badge_req_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#FEF2F2', 'font_color': '#DC2626',
            'border': 1, 'border_color': '#FECACA'
        })
        badge_opt_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#F3F4F6', 'font_color': '#6B7280',
            'border': 1, 'border_color': '#E5E7EB'
        })

        cols = [
            ("Attribute Key", 22),
            ("Clinical Label", 34),
            ("Page", 10),
            ("Page Type", 14),
            ("Section", 26),
            ("Data Type", 16),
            ("Recorded By", 16),
            ("Mandatory", 12),
            ("Excel Export Header", 24),
            ("Validation Rule", 26)
        ]
        ws_attrs.set_row(0, 32)
        for c_idx, (col_name, col_width) in enumerate(cols):
            ws_attrs.set_column(c_idx, c_idx, col_width)
            ws_attrs.write_string(0, c_idx, col_name, attr_hdr_fmt)

        attributes = default_template_engine.get_form_attributes(form_id)
        for row_idx, a in enumerate(attributes, start=1):
            r_fmt = attr_cell_alt if row_idx % 2 == 0 else attr_cell_fmt
            ws_attrs.set_row(row_idx, 20)
            ws_attrs.write_string(row_idx, 0, sanitize_csv_cell(a.get("key") or ""), r_fmt)
            ws_attrs.write_string(row_idx, 1, sanitize_csv_cell(a.get("label") or ""), r_fmt)
            ws_attrs.write_number(row_idx, 2, int(a.get("page_number") or 1), r_fmt)
            ws_attrs.write_string(row_idx, 3, sanitize_csv_cell(str(a.get("page_type") or "").capitalize()), r_fmt)
            ws_attrs.write_string(row_idx, 4, sanitize_csv_cell(a.get("section_title") or ""), r_fmt)
            ws_attrs.write_string(row_idx, 5, sanitize_csv_cell(a.get("type") or ""), r_fmt)
            ws_attrs.write_string(row_idx, 6, sanitize_csv_cell(str(a.get("writer") or "").capitalize()), r_fmt)
            is_req = bool(a.get("required"))
            ws_attrs.write_string(row_idx, 7, "Required" if is_req else "Optional", badge_req_fmt if is_req else badge_opt_fmt)
            ws_attrs.write_string(row_idx, 8, sanitize_csv_cell(a.get("export_column") or a.get("key") or ""), r_fmt)
            ws_attrs.write_string(row_idx, 9, sanitize_csv_cell(a.get("validation") or ""), r_fmt)

    def generate_excel_export(
        self,
        camp_id: Optional[str] = None,
        only_reviewed: bool = False,
        form_type: Optional[str] = None,
        community_id: Optional[str] = None,
        org_id: Optional[str] = None,
        record_ids: Optional[List[str]] = None
    ) -> bytes:
        """Generate formatted Excel (.xlsx) workbook with columns in exact form field order (Page 1 → Page 2)."""
        status_filter = "reviewed" if only_reviewed else None
        target_org = org_id or community_id
        local_records = database.list_records(
            camp_id=camp_id,
            status=status_filter,
            community_id=target_org,
            limit=10000,
            db_path=self.db_path
        ) or []
        local_camps = database.list_camps_with_stats(self.db_path, community_id=target_org) or []
        remote_records = []
        camps = None
        try:
            from backend.services.supabase_service import supabase_service
            if supabase_service.is_available():
                sup_recs = supabase_service.list_records(
                    camp_id=camp_id,
                    status=status_filter,
                    community_id=target_org,
                    limit=10000
                )
                if sup_recs:
                    remote_records = sup_recs
                c_list = supabase_service.list_camps_with_stats(community_id=target_org)
                if c_list is not None:
                    camps = c_list
        except Exception as sup_exp_err:
            logger.warning("Supabase export record retrieval notice: %s", sup_exp_err)

        merged_map = {r["id"]: r for r in remote_records}
        for r in local_records:
            merged_map[r["id"]] = r
        records = list(merged_map.values())
        if camps is None:
            camps = local_camps
        if record_ids and records:
            rec_id_set = {str(x).strip() for x in record_ids if str(x).strip()}
            records = [r for r in records if str(r.get("id")) in rec_id_set or str(r.get("record_id")) in rec_id_set]
        selected_camp = None
        if camp_id:
            matching = [c for c in camps if c["id"] == camp_id]
            if matching:
                selected_camp = matching[0]
            camps = matching

        # Route to segregated paediatric export if explicitly requested or camp target_group is paediatric
        norm_type = (form_type or "").strip().lower()
        if norm_type in ("paediatric", "pediatric", "school"):
            return self._generate_paediatric_excel_export(camp_id=camp_id, camp=selected_camp, records=records, only_reviewed=only_reviewed)
        elif norm_type in ("adult", "university", "general"):
            return self._generate_adult_excel_export(camp_id=camp_id, camp=selected_camp, records=records, camps=camps, only_reviewed=only_reviewed)
        else:
            def _is_paed_rec(rec: Dict[str, Any]) -> bool:
                if not rec:
                    return False
                tg = str(rec.get("target_group") or "").lower()
                if tg in ("paediatric", "pediatric", "school"):
                    return True
                if bool(rec.get("is_paediatric")) or rec.get("is_paediatric") in (1, True, "1"):
                    return True
                tpl = str(rec.get("template_id") or rec.get("form_type") or "").lower()
                if any(k in tpl for k in ("paediatric", "pediatric", "school")):
                    return True
                return False

            def _is_paed_camp(c: Optional[Dict[str, Any]]) -> bool:
                if not c:
                    return False
                tg = str(c.get("target_group") or "").lower()
                if tg in ("paediatric", "pediatric", "school"):
                    return True
                c_name = str(c.get("name") or "").lower()
                if any(k in c_name for k in ("school", "paediatric", "pediatric", "vidyalaya", "academy", "matriculation")):
                    return True
                return False

            camp_is_paed = _is_paed_camp(selected_camp)
            has_adult = any(not _is_paed_rec(r) and not camp_is_paed for r in records) if records else (not camp_is_paed)
            has_paediatric = any(_is_paed_rec(r) or camp_is_paed for r in records) if records else camp_is_paed

            if has_adult and has_paediatric and not camp_is_paed:
                return self._generate_multi_family_excel_export(camp_id=camp_id, camp=selected_camp, records=records, camps=camps, only_reviewed=only_reviewed)

            if has_paediatric or camp_is_paed:
                return self._generate_paediatric_excel_export(camp_id=camp_id, camp=selected_camp, records=records, only_reviewed=only_reviewed)

            return self._generate_adult_excel_export(camp_id=camp_id, camp=selected_camp, records=records, camps=camps, only_reviewed=only_reviewed)

    def generate_adult_excel_export(
        self,
        camp_id: Optional[str] = None,
        only_reviewed: bool = False,
        community_id: Optional[str] = None,
        org_id: Optional[str] = None,
        record_ids: Optional[List[str]] = None
    ) -> bytes:
        """Explicitly generate Adult Health Screening (.xlsx) workbook."""
        return self.generate_excel_export(
            camp_id=camp_id,
            only_reviewed=only_reviewed,
            form_type="adult",
            community_id=community_id,
            org_id=org_id,
            record_ids=record_ids
        )

    def generate_paediatric_excel_export(
        self,
        camp_id: Optional[str] = None,
        only_reviewed: bool = False,
        community_id: Optional[str] = None,
        org_id: Optional[str] = None,
        record_ids: Optional[List[str]] = None
    ) -> bytes:
        """Explicitly generate Paediatric School Health Screening (.xlsx) workbook."""
        return self.generate_excel_export(
            camp_id=camp_id,
            only_reviewed=only_reviewed,
            form_type="paediatric",
            community_id=community_id,
            org_id=org_id,
            record_ids=record_ids
        )

    def _populate_adult_records_worksheet(
        self,
        wb,
        ws,
        records: List[Dict[str, Any]]
    ) -> None:
        """Write columns and rows for adult health records into given worksheet."""
        # ── Formats ──────────────────────────────────────────────────────────
        header_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': '#D81B60', 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#F8BBD0', 'text_wrap': True
        })
        section_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': '#4A0E2A', 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#7B1FA2', 'text_wrap': True
        })
        cell_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9,
            'valign': 'vcenter', 'border': 1, 'border_color': '#F1F4F7'
        })
        cell_alt_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'valign': 'vcenter',
            'bg_color': '#FDF9FB', 'border': 1, 'border_color': '#F1F4F7'
        })
        status_reviewed_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#ECFDF5', 'font_color': '#059669',
            'border': 1, 'border_color': '#A7F3D0'
        })
        status_to_check_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#FFFBEB', 'font_color': '#D97706',
            'border': 1, 'border_color': '#FDE68A'
        })
        alert_row_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'bg_color': '#FFEBEE', 'font_color': '#C62828',
            'border': 1, 'border_color': '#FFCDD2'
        })

        ws.set_tab_color('#D81B60')
        ws.set_row(0, 40)
        ws.freeze_panes(1, 3)

        # ── Column definitions in EXACT form order ───────────────────────────
        # Format: (header_label, col_width, is_section_divider)
        # Section dividers are written with section_fmt to visually separate form pages
        columns = [
            # ── Admin / ID columns (operational) ────────────────────────────
            ("Record ID",                      18, False),
            ("Camp Name",                      24, False),
            ("Review Status",                  14, False),

            # ── PAGE 1 — STUDENT'S PERSONAL INFORMATION ──────────────────────
            ("Moc Doc ID",                     16, True),
            ("Admission ID",                   16, False),
            ("Name of School / College",       28, False),
            ("Staff / Student",                16, False),
            ("Full Name",                      26, False),
            ("Gender",                         10, False),
            ("Year of Study",                  14, False),
            ("Course / Dept",                  20, False),
            ("Section",                        10, False),
            ("Shift",                          10, False),
            ("Blood Group",                    12, False),
            ("DOB",                            14, False),
            ("Age",                            10, False),
            ("Mobile",                         16, False),
            ("Marital Status",                 14, False),
            ("Parent / Spouse Name",           22, False),
            ("No of Children",                 14, False),
            ("Address",                        32, False),

            # ── PAGE 1 — MEDICAL HISTORY ─────────────────────────────────────
            ("Any Known Allergy",              18, True),
            ("Allergy Details",                24, False),
            ("Abdominal Issues",               18, False),
            ("Abdominal Details",              24, False),
            ("Irregular / PCOD / Menopause",   20, False),
            ("PCOD / Menopause Details",       24, False),
            ("Skin / Hair / ENT Problem",      20, False),
            ("Skin / ENT Details",             24, False),
            ("Current / Previous Medications", 20, False),
            ("Medication Details",             24, False),
            ("Previous Surgery / Hospitalization", 22, False),
            ("Surgery / Hospitalization Details", 24, False),
            ("Social History",                 18, False),
            ("Social History Details",         22, False),
            ("Physical Activity",              18, False),
            ("Physical Activity Details",      22, False),
            ("Any Other Medical Issues",       26, False),
            ("No of Immediate Family Members", 20, False),
            ("Personal Physician",             22, False),

            # ── PAGE 1 — FAMILY HISTORY ───────────────────────────────────────
            ("Family Thyroid",                 18, True),
            ("Family Diabetes",                18, False),
            ("Family Heart Disease",           18, False),
            ("Family Hypertension",            18, False),
            ("Family Cancer",                  18, False),
            ("Family Epilepsy",                18, False),
            ("Family Asthma",                  18, False),
            ("Family Others",                  18, False),

            # ── PAGE 2 — HEALTH ASSESSMENT ───────────────────────────────────
            ("Health Assessment Name",         22, True),
            ("Date of Examination",            18, False),
            ("Height",                         12, False),
            ("Weight",                         12, False),
            ("BMI",                            12, False),
            ("Pulse",                          12, False),
            ("Blood Pressure",                 16, False),
            ("RBS",                            14, False),
            ("Waist Circumference",            18, False),
            ("ECG",                            24, False),
            ("ECHO",                           24, False),

            # ── General Signs ─────────────────────────────────────────────────
            ("Pallor",                         14, True),
            ("Lymphadenopathy",                16, False),
            ("Deformity",                      14, False),
            ("Icterus",                        14, False),
            ("Cyanosis",                       14, False),
            ("Edema",                          14, False),
            ("Clubbing",                       14, False),

            # ── System Examination ────────────────────────────────────────────
            ("Ear",                            14, True),
            ("Ear Details",                    24, False),
            ("Nose",                           14, False),
            ("Nose Details",                   24, False),
            ("Throat",                         14, False),
            ("Throat Details",                 24, False),
            ("RS",                             14, False),
            ("RS Details",                     24, False),
            ("CVS",                            14, False),
            ("CVS Details",                    24, False),
            ("Abdomen",                        14, False),
            ("Abdomen Details",                24, False),
            ("CNS",                            14, False),
            ("CNS Details",                    24, False),
            ("Skin",                           14, False),
            ("Skin Details",                   24, False),
            ("Hair",                           14, False),
            ("Hair Details",                   24, False),
            ("Musculoskeletal System",          20, False),
            ("Musculoskeletal Details",        24, False),
            ("Others",                         14, False),
            ("Others Details",                 24, False),
            ("Psychological Consultation",     22, False),
            ("Psychological Details",          24, False),

            # ── Vision ────────────────────────────────────────────────────────
            ("Vision Category",                18, True),
            ("Distant Vision Right Eye",       18, False),
            ("Distant Vision Left Eye",        18, False),

            # ── Dental ────────────────────────────────────────────────────────
            ("Dental Status",                  16, True),
            ("Dental Abnormality",             22, False),
            ("Dental Advice",                  22, False),

            # ── Referral ──────────────────────────────────────────────────────
            ("Specialist Referred To",         22, True),
            ("Doctor Signature Present",       18, False),

            # ── Operational / Admin ───────────────────────────────────────────
            ("Camp ID",                         18, True),
            ("Created At",                      20, False),
            ("Reviewed By",                     18, False),
            ("Reviewed At",                     20, False),
            ("Reviewer Notes",                  28, False),
            ("Extraction Notes",                28, False),

            # ── Paediatric Screening & Consent (Phase 6 & 7) ──────────────────
            ("Consent Status",                  18, True),
            ("Paediatric BMI Centile",          22, False),
            ("Paediatric BMI Staging",          24, False),
            ("Paediatric BP Centile",           22, False),
            ("Paediatric BP Staging",           24, False),
            ("ATR Caliper Angle",               18, False),
            ("Paediatric Screening Alerts",     28, False),
        ]

        for col_idx, (col_name, col_width, is_section) in enumerate(columns):
            fmt = section_fmt if is_section else header_fmt
            ws.write_string(0, col_idx, col_name, fmt)
            ws.set_column(col_idx, col_idx, col_width)

        # ── Helper functions ──────────────────────────────────────────────────
        def _val(obj: Any) -> str:
            if obj is None: return ""
            if isinstance(obj, dict):
                v = obj.get("value")
                return str(v) if v is not None else ""
            return str(obj).strip()

        def _details(obj: Any) -> str:
            if isinstance(obj, dict):
                return str(obj.get("details") or obj.get("relationship") or obj.get("notes") or "")
            return ""

        def _sys_normal(obj: Any) -> str:
            """Return Normal/Abnormal status for a system exam entry."""
            if isinstance(obj, dict):
                val = str(obj.get("value") or obj.get("status") or obj.get("normal") or "").strip()
                if val: return val
            elif isinstance(obj, str):
                return obj.strip()
            return ""

        def _sys_details(obj: Any) -> str:
            """Return abnormal details for a system exam entry."""
            if isinstance(obj, dict):
                return str(obj.get("details") or obj.get("abnormal_details") or obj.get("notes") or "")
            return ""

        def _calc_bmi(h_str: Any, w_str: Any) -> str:
            try:
                h_m = re.search(r"(\d+(?:\.\d+)?)", str(h_str or ""))
                w_m = re.search(r"(\d+(?:\.\d+)?)", str(w_str or ""))
                if h_m and w_m:
                    h = float(h_m.group(1))
                    w = float(w_m.group(1))
                    if h > 30: h = h / 100.0
                    if h > 0: return str(round(w / (h * h), 1))
            except Exception: pass
            return ""

        # ── Write rows ────────────────────────────────────────────────────────
        for row_idx, r in enumerate(records, start=1):
            row_fmt = cell_alt_fmt if row_idx % 2 == 0 else cell_fmt
            sr = r.get("structured_record") or {}
            if isinstance(sr, str):
                try:
                    sr = json.loads(sr)
                except Exception:
                    sr = {}
            p  = sr.get("personal_information") or {}
            m  = sr.get("medical_history") or {}
            f  = sr.get("family_history") or {}
            h  = sr.get("health_assessment") or {}
            s  = sr.get("system_examination") or {}
            v  = sr.get("vision") or {}
            d  = sr.get("dental") or {}
            ref = sr.get("referral") or {}

            person_name = _val(p.get("full_name")) or r.get("person_name") or ""
            age    = _val(p.get("age"))    or r.get("age")    or ""
            gender = _val(p.get("gender")) or r.get("gender") or ""
            height = _val(h.get("height_cm")) or r.get("height") or ""
            weight = _val(h.get("weight_kg")) or r.get("weight") or ""
            bp     = _val(h.get("blood_pressure_mmhg")) or r.get("blood_pressure") or ""
            bmi    = _calc_bmi(height, weight)
            status = r.get("status") or "needs_review"
            st_fmt = status_reviewed_fmt if status == "reviewed" else status_to_check_fmt

            row_data = [
                # Admin
                str(r.get("id") or ""),
                str(r.get("camp_name") or ""),
                status,

                # PAGE 1 — Personal Info (form order)
                _val(p.get("doc_id")),
                _val(p.get("admission_id")),
                _val(p.get("school_college_name")),
                _val(p.get("staff_student_type")),
                person_name,
                gender,
                _val(p.get("year_of_study")),
                _val(p.get("course_dept")),
                _val(p.get("section")),
                _val(p.get("shift")),
                _val(p.get("blood_group")),
                _val(p.get("dob")),
                age,
                _val(p.get("mobile_number")),
                _val(p.get("marital_status")),
                _val(p.get("parent_spouse_name")),
                _val(p.get("number_of_children")),
                _val(p.get("address")),

                # PAGE 1 — Medical History (Y/N + Details pairs in form order)
                _val(m.get("known_allergies")),
                _details(m.get("known_allergies")),
                _val(m.get("abdominal_pain_indigestion_constipation")),
                _details(m.get("abdominal_pain_indigestion_constipation")),
                _val(m.get("irregular_pcod_menopause")),
                _details(m.get("irregular_pcod_menopause")),
                _val(m.get("skin_hair_ear_nose_problems")),
                _details(m.get("skin_hair_ear_nose_problems")),
                _val(m.get("current_previous_medications")),
                _details(m.get("current_previous_medications")),
                _val(m.get("previous_surgery_hospitalization")),
                _details(m.get("previous_surgery_hospitalization")),
                _val(m.get("social_history")),
                _details(m.get("social_history")),
                _val(m.get("physical_activity")),
                _details(m.get("physical_activity")),
                _val(m.get("other_medical_issues")),
                _val(m.get("immediate_family_count")),
                _val(m.get("personal_physician")),

                # PAGE 1 — Family History (form order: Thyroid, Diabetes, Heart, Hypertension, Cancer, Epilepsy, Asthma, Others)
                _details(f.get("thyroid"))       or _val(f.get("thyroid")),
                _details(f.get("diabetes"))      or _val(f.get("diabetes")),
                _details(f.get("heart_disease")) or _val(f.get("heart_disease")),
                _details(f.get("hypertension"))  or _val(f.get("hypertension")),
                _details(f.get("cancer"))        or _val(f.get("cancer")),
                _details(f.get("epilepsy"))      or _val(f.get("epilepsy")),
                _details(f.get("asthma"))        or _val(f.get("asthma")),
                _details(f.get("other_conditions")) or _val(f.get("other_conditions")),

                # PAGE 2 — Health Assessment (form order: Ht, Wt, BMI, Pulse, BP, RBS, Waist, ECG, ECHO)
                person_name,           # Name (P2) — repeated from header of page 2
                _val(h.get("examination_date")),
                height,
                weight,
                bmi,
                _val(h.get("pulse_bpm")),
                bp,
                _val(h.get("random_blood_sugar_mgdl")),
                _val(h.get("waist_circumference_cm")),
                _val(h.get("ecg_notes")),
                _val(h.get("echo_notes")),

                # General Signs (form order: Pallor, Lymphadenopathy, Deformity, Icterus, Cyanosis, Edema, Clubbing)
                _val(h.get("pallor")),
                _val(h.get("lymphadenopathy")),
                _val(h.get("deformity")),
                _val(h.get("icterus")),
                _val(h.get("cyanosis")),
                _val(h.get("edema")),
                _val(h.get("clubbing")),

                # System Examination (Normal/Details pairs in form order)
                _sys_normal(s.get("ear")),          _sys_details(s.get("ear")),
                _sys_normal(s.get("nose")),         _sys_details(s.get("nose")),
                _sys_normal(s.get("throat")),       _sys_details(s.get("throat")),
                _sys_normal(s.get("respiratory_system")),    _sys_details(s.get("respiratory_system")),
                _sys_normal(s.get("cardiovascular_system")), _sys_details(s.get("cardiovascular_system")),
                _sys_normal(s.get("abdomen")),      _sys_details(s.get("abdomen")),
                _sys_normal(s.get("central_nervous_system")), _sys_details(s.get("central_nervous_system")),
                _sys_normal(s.get("skin")),         _sys_details(s.get("skin")),
                _sys_normal(s.get("hair")),         _sys_details(s.get("hair")),
                _sys_normal(s.get("musculoskeletal_system")), _sys_details(s.get("musculoskeletal_system")),
                _sys_normal(s.get("others")),       _sys_details(s.get("others")),
                _sys_normal(s.get("psychological_consultation")), _sys_details(s.get("psychological_consultation")),

                # Vision
                _val(v.get("vision_category")),
                _val(v.get("distant_vision_right")),
                _val(v.get("distant_vision_left")),

                # Dental
                _val(d.get("normal") or d.get("dental_status") or d.get("status")),
                _val(d.get("abnormality") or d.get("dental_abnormality")),
                _val(d.get("advice") or d.get("dental_advice")),

                # Referral
                _val(ref.get("specialist_referred_to")) or r.get("referral_details") or "",
                _val(ref.get("doctor_signature_present")),

                # Operational
                str(r.get("camp_id") or ""),
                str(r.get("created_at") or ""),
                str(r.get("reviewed_by") or ""),
                str(r.get("reviewed_at") or ""),
                str(r.get("reviewer_notes") or ""),
                str(r.get("extraction_notes") or ""),

                # Paediatric Screening & Consent (Phase 6 & 7)
                "Consent Withheld" if (r.get("consent_given") is False or _val(p.get("consent_screening")).lower() in ("no", "withheld")) else "Given",
                str((sr.get("paediatric_screening") or {}).get("bmi_centile") or r.get("paediatric_bmi_centile") or ""),
                str((sr.get("paediatric_screening") or {}).get("bmi_classification") or r.get("paediatric_bmi_classification") or ""),
                str((sr.get("paediatric_screening") or {}).get("bp_percentile") or r.get("paediatric_bp_percentile") or ""),
                str((sr.get("paediatric_screening") or {}).get("bp_classification") or r.get("paediatric_bp_classification") or ""),
                str(((sr.get("paediatric_screening") or {}).get("atr_classification") or {}).get("atr_value") or _val(h.get("adams_forward_bend_test_atr")) or ""),
                ", ".join(r.get("paediatric_alerts") or (sr.get("paediatric_screening") or {}).get("paediatric_alerts") or []),
            ]

            ws.set_row(row_idx, 20)
            for c_idx, val in enumerate(row_data):
                col_name = columns[c_idx][0] if c_idx < len(columns) else ""
                if c_idx == 2:
                    fmt_to_use = st_fmt
                elif self._is_adult_field_abnormal(col_name, val, r):
                    fmt_to_use = alert_row_fmt
                else:
                    fmt_to_use = row_fmt
                ws.write_string(row_idx, c_idx, sanitize_csv_cell(val), fmt_to_use)



    def _populate_camp_summary_worksheet(
        self,
        wb,
        ws_camps,
        camps: List[Dict[str, Any]]
    ) -> None:
        """Write camp summary metrics into given worksheet."""
        header_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': '#D81B60', 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#F8BBD0', 'text_wrap': True
        })
        cell_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9,
            'valign': 'vcenter', 'border': 1, 'border_color': '#F1F4F7'
        })
        cell_alt_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'valign': 'vcenter',
            'bg_color': '#FDF9FB', 'border': 1, 'border_color': '#F1F4F7'
        })
        # ── Sheet 3: Camp Summary ─────────────────────────────────────────────
        # ws_camps passed in as parameter
        ws_camps.set_tab_color('#C2185B')
        camp_cols = [
            ("Camp ID", 20), ("Camp Name", 30), ("Camp Date", 14),
            ("Target Records", 15), ("Total Scanned", 14), ("To Check", 12),
            ("Reviewed", 12), ("Progress (%)", 14),
            ("Hypertension Suspects (>=130/80)", 22),
            ("Vision Deficits / Cataract", 20), ("Dental Issues / Caries", 18),
            ("Pallor / Anemia Suspects", 18), ("Hospital Referrals", 18), ("Created At", 22)
        ]
        ws_camps.set_row(0, 32)
        for c_idx, (col_name, col_width) in enumerate(camp_cols):
            ws_camps.write_string(0, c_idx, col_name, header_fmt)
            ws_camps.set_column(c_idx, c_idx, col_width)

        for row_idx, c in enumerate(camps, start=1):
            r_fmt = cell_alt_fmt if row_idx % 2 == 0 else cell_fmt
            ws_camps.set_row(row_idx, 20)
            camp_identifier = str(c.get("id"))
            stats = database.get_camp_analytics(camp_identifier, self.db_path)
            abnormal_bp  = stats.get("abnormal_bp_count", 0)
            vis_data     = stats.get("vision_screening", {})
            vision_issues = vis_data.get("refraction_deficit", 0) + vis_data.get("cataract_suspect", 0)
            dent_data    = stats.get("dental_screening", {})
            dental_issues = dent_data.get("caries_identified", 0)
            pallor_data  = stats.get("pallor_indicators", {})
            pallor_pos   = pallor_data.get("pallor_positive", 0)
            ref_data     = stats.get("referral_breakdown", {})
            total_referrals = sum(v for k, v in ref_data.items() if k != "No Referral Required")

            ws_camps.write_string(row_idx, 0,  camp_identifier, r_fmt)
            ws_camps.write_string(row_idx, 1,  str(c.get("name")), r_fmt)
            ws_camps.write_string(row_idx, 2,  str(c.get("date_str")), r_fmt)
            ws_camps.write_number(row_idx, 3,  int(c.get("expected_records") or 0), r_fmt)
            ws_camps.write_number(row_idx, 4,  int(c.get("total_scanned") or 0), r_fmt)
            ws_camps.write_number(row_idx, 5,  int(c.get("to_check") or 0), r_fmt)
            ws_camps.write_number(row_idx, 6,  int(c.get("reviewed") or 0), r_fmt)
            ws_camps.write_number(row_idx, 7,  float(c.get("progress_pct") or 0.0), r_fmt)
            ws_camps.write_number(row_idx, 8,  abnormal_bp, r_fmt)
            ws_camps.write_number(row_idx, 9,  vision_issues, r_fmt)
            ws_camps.write_number(row_idx, 10, dental_issues, r_fmt)
            ws_camps.write_number(row_idx, 11, pallor_pos, r_fmt)
            ws_camps.write_number(row_idx, 12, total_referrals, r_fmt)
            ws_camps.write_string(row_idx, 13, str(c.get("created_at") or ""), r_fmt)



    def _generate_adult_excel_export(
        self,
        camp_id: Optional[str],
        camp: Optional[Dict[str, Any]],
        records: List[Dict[str, Any]],
        camps: List[Dict[str, Any]],
        only_reviewed: bool = False
    ) -> bytes:
        """Generate formatted Adult Health (.xlsx) workbook with Form Attributes sheet and Camp Summary."""
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True, 'remove_timezone': True})

        sheet_name = "Patient Records"
        if camp_id and camps:
            c_name = re.sub(r'[:/\\?*\[\]]', '', str(camps[0].get("name", "Records")))[:30].strip()
            sheet_name = c_name or "Patient Records"

        ws = wb.add_worksheet(sheet_name)
        self._populate_adult_records_worksheet(wb, ws, records)
        self._write_attributes_sheet(wb, "adult", tab_color='#AD1457', header_bg='#880E4F')

        ws_camps = wb.add_worksheet("Camp Summary")
        self._populate_camp_summary_worksheet(wb, ws_camps, camps)

        wb.close()
        output.seek(0)
        return output.getvalue()

    @staticmethod
    def _is_adult_field_abnormal(col_name: str, val: Any, record: Dict[str, Any]) -> bool:
        """Evaluate if an adult field value is clinically abnormal / flagged.

        Follows existing adult clinical classification rules:
        - Normal/absent/unassessed/blank values are NOT abnormal.
        - BP: systolic >= 130 or diastolic >= 80 (prehypertension/hypertension).
        - BMI: < 18.5 (underweight) or >= 23.0 (overweight/obese by Asian-Indian criteria).
        - Pulse: > 100 (tachycardia) or < 50 (bradycardia).
        - RBS: >= 140 or < 70.
        - Waist Circumference: > 90.
        - Allergies, surgeries, medications, PCOD/menopause, medical history: positive findings.
        - General signs (pallor, icterus, cyanosis, edema, clubbing, lymphadenopathy, deformity): positive findings.
        - Systemic exam: abnormal findings.
        - Vision: defective (<6/6).
        - Dental: caries, cavity, defective, referral.
        - Referral: specialist referral present.
        """
        if val is None:
            return False
        val_str = str(val).strip()
        if not val_str:
            return False
        low = val_str.lower()

        # Handle systemic normal/abnormal status columns (where "No" means not normal -> abnormal)
        sys_status_cols = (
            "Ear", "Nose", "Throat", "RS", "CVS", "Abdomen",
            "CNS", "Skin", "Hair", "Musculoskeletal System", "Others", "Psychological Consultation"
        )
        if col_name in sys_status_cols:
            if low in ("no", "abnormal", "abn", "defective", "n"):
                return True
            return False

        if low in ("none", "nil", "n/a", "na", "-", "--", "missing", "unassessed", "not assessed", "normal", "nad", "wnl", "absent", "absent (nad)", "clear", "intact", "healthy", "no", "regular", "given", "given / positive"):
            return False

        # 1. Blood Pressure
        if col_name == "Blood Pressure":
            bp_parts = re.findall(r'\d+', val_str)
            if len(bp_parts) >= 2:
                sys_v, dia_v = int(bp_parts[0]), int(bp_parts[1])
                if sys_v >= 130 or dia_v >= 80:
                    return True
            return False

        # 2. BMI
        elif col_name == "BMI":
            nums = re.findall(r'[\d.]+', val_str)
            if nums:
                try:
                    bmi_f = float(nums[0])
                    if bmi_f < 18.5 or bmi_f >= 23.0:
                        return True
                except Exception:
                    pass
            return False

        # 3. Pulse
        elif col_name == "Pulse":
            nums = re.findall(r'\d+', val_str)
            if nums:
                try:
                    p_val = int(nums[0])
                    if p_val > 100 or p_val < 50:
                        return True
                except Exception:
                    pass
            return False

        # 4. RBS
        elif col_name == "RBS":
            nums = re.findall(r'[\d.]+', val_str)
            if nums:
                try:
                    rbs_f = float(nums[0])
                    if rbs_f >= 140.0 or rbs_f < 70.0:
                        return True
                except Exception:
                    pass
            return False

        # 5. Waist Circumference
        elif col_name == "Waist Circumference":
            nums = re.findall(r'[\d.]+', val_str)
            if nums:
                try:
                    wc_f = float(nums[0])
                    if wc_f > 90.0:
                        return True
                except Exception:
                    pass
            return False

        # 6. Allergies
        elif col_name in ("Any Known Allergy", "Allergy Details"):
            if any(n in low for n in ("no known", "nil", "none", "nad", "no", "na", "no allergies", "nil known", "absent")):
                return False
            return True

        # 7. Irregular / PCOD / Menopause
        elif col_name in ("Irregular / PCOD / Menopause", "PCOD / Menopause Details"):
            if low in ("no", "nil", "none", "nad", "na", "regular", "normal", "absent"):
                return False
            if any(k in low for k in ("yes", "irregular", "pcod", "pcos", "menopause", "amenorrhea", "dysmenorrhea", "positive")):
                return True
            return False

        # 8. Medical History & Details
        elif col_name in (
            "Abdominal Issues", "Abdominal Details",
            "Skin / Hair / ENT Problem", "Skin / ENT Details",
            "Current / Previous Medications", "Medication Details",
            "Previous Surgery / Hospitalization", "Surgery / Hospitalization Details",
            "Any Other Medical Issues"
        ):
            if low in ("no", "nil", "none", "nad", "na", "normal", "absent", "no surgery", "no medications", "none taken"):
                return False
            return True

        # 9. Family History
        elif col_name.startswith("Family "):
            if low in ("no", "nil", "none", "nad", "na", "absent", "negative"):
                return False
            return True

        # 10. General Signs
        elif col_name in ("Pallor", "Lymphadenopathy", "Deformity", "Icterus", "Cyanosis", "Edema", "Clubbing"):
            if any(n in low for n in ("absent", "nad", "wnl", "no", "nil", "none", "na", "normal")):
                return False
            return True

        # 11. System Examination
        elif col_name in (
            "Ear", "Ear Details", "Nose", "Nose Details", "Throat", "Throat Details",
            "RS", "RS Details", "CVS", "CVS Details", "Abdomen", "Abdomen Details",
            "CNS", "CNS Details", "Skin", "Skin Details", "Hair", "Hair Details",
            "Musculoskeletal System", "Musculoskeletal Details", "Others", "Others Details",
            "Psychological Consultation", "Psychological Details"
        ):
            if col_name in ("Ear", "Nose", "Throat", "RS", "CVS", "Abdomen", "CNS", "Skin", "Hair", "Musculoskeletal System", "Others", "Psychological Consultation"):
                if low in ("abnormal", "no", "abn", "defective"):
                    return True
                return False
            else:
                low_cleaned = re.sub(r'\b(non-?tender|not\s+tender|no\s+organomegaly|no\s+hepatomegaly|no\s+splenomegaly|no\s+mass|no\s+murmur[s]?|no\s+added\s+sounds?)\b', '', low)
                normal_words = ("normal", "nad", "wnl", "clear", "intact", "unremarkable", "nil", "none", "healthy", "patent", "s1s2 heard", "no abnormality", "vesicular", "soft", "conscious", "oriented")
                abn_words = ("murmur", "wheeze", "rhonchi", "crepitation", "creps", "abnormal", "hepatomegaly", "splenomegaly", "mass", "tender", "organomegaly", "wax", "csom", "aom", "dull", "retracted", "perforation", "discharge", "congestion", "infection", "impaction", "cerumen", "dns", "polyp", "tonsill", "pharyng", "eczema", "scabies", "rash", "lice", "nits", "alopecia")
                if (any(n in low for n in normal_words) or any(n in low_cleaned for n in normal_words)) and not any(a in low_cleaned for a in abn_words):
                    return False
                if any(a in low_cleaned for a in abn_words):
                    return True
                if not any(n in low for n in normal_words):
                    return True
                return False

        # 12. Vision
        elif col_name in ("Distant Vision Right Eye", "Distant Vision Left Eye"):
            cleaned = re.sub(r'\s+', '', val_str.upper())
            if cleaned in ("6/6", "6/5", "6/6UNAIDED", "6/6AIDED", "6/6WITHGLASSES"):
                return False
            if any(cleaned.startswith(k) or k in cleaned for k in ("6/9", "6/12", "6/18", "6/24", "6/36", "6/60", "CF", "HM", "PL", "DEFECTIVE", "REDUCED")):
                return True
            return False

        # 13. Dental
        elif col_name == "Dental Status":
            if low in ("normal", "nad", "wnl", "healthy"):
                return False
            if any(k in low for k in ("abnormal", "caries", "defective", "cavity", "stains")):
                return True
            return False

        elif col_name == "Dental Abnormality":
            if low in ("none", "nil", "no", "nad", "normal", "healthy"):
                return False
            if any(k in low for k in ("caries", "cavity", "cavities", "malocclusion", "fluorosis", "gingivitis", "tartar", "calculus", "stains", "crowding")):
                return True
            return False

        elif col_name == "Dental Advice":
            if low in ("none", "nil", "no", "routine hygiene", "brush twice daily", "routine", "healthy"):
                return False
            if any(k in low for k in ("refer", "dentist", "filling", "extraction", "scaling", "consult", "opd", "treatment", "ortho")):
                return True
            return False

        # 14. Referral
        elif col_name == "Specialist Referred To":
            if low in ("none", "nil", "no", "no referral", "not required"):
                return False
            return True

        # 15. Paediatric Columns in Adult Sheet
        elif col_name in ("Paediatric BMI Classification", "Paediatric BP Classification", "Adams Forward Bend Test ATR", "Paediatric Alerts"):
            if col_name == "Paediatric BMI Classification" and val_str in ("Severely Thin", "Moderately Thin", "Overweight", "Obese"):
                return True
            if col_name == "Paediatric BP Classification" and val_str in ("Elevated", "Stage 1 HTN", "Stage 2 HTN", "Hypertension", "Prehypertension"):
                return True
            if col_name == "Adams Forward Bend Test ATR":
                nums = re.findall(r'[\d.]+', val_str)
                if nums and float(nums[0]) >= 5.0:
                    return True
            if col_name == "Paediatric Alerts" and val_str:
                return True
            return False

        return False

    @staticmethod
    def _is_paediatric_field_abnormal(col_key: str, val: Any, record: Dict[str, Any], screening: Dict[str, Any]) -> bool:
        """Evaluate if a recorded paediatric field value is clinically abnormal.

        Rules:
        - Normal values are NOT abnormal.
        - Missing, blank, or unassessed values are NOT abnormal.
        - Reuses existing clinical classification logic (classify_paediatric, classify_paediatric_bmi, classify_paediatric_bp, classify_atr).
        - Doctor-recorded abnormal terms (e.g. 'Abnormal', 'Wax', 'Caries', 'DNS') are highlighted.
        - Never highlights unrecorded/missing entries or unassessed findings.
        """
        if val is None:
            return False
        val_str = str(val).strip()
        if not val_str or val_str.lower() in ("none", "nil", "n/a", "na", "-", "--", "missing", "unassessed", "not assessed", "not recorded"):
            return False

        low = val_str.lower()

        # 1. Measurements
        if col_key == "bmi":
            bmi_cls = screening.get("paediatric_bmi_classification") or {}
            cat = str(bmi_cls.get("category") or record.get("bmi_classification") or "").strip()
            if cat in ("Severely Thin", "Moderately Thin", "Overweight", "Obese"):
                return True
            if bmi_cls.get("is_high_risk") or bmi_cls.get("alert_level") in ("warning", "critical", "advisory"):
                return True
            if not cat or cat == "Unknown":
                try:
                    nums = re.findall(r'[\d.]+', val_str)
                    if nums:
                        bmi_f = float(nums[0])
                        sr = record.get("structured_record") or {}
                        age_raw = record.get("age") or sr.get("age") or (sr.get("personal_information") or {}).get("age")
                        sex_raw = record.get("gender") or sr.get("gender") or (sr.get("personal_information") or {}).get("gender")
                        if age_raw is not None:
                            direct_cls = classify_paediatric_bmi(bmi_f, float(age_raw), sex_raw)
                            if direct_cls.get("category") in ("Severely Thin", "Moderately Thin", "Overweight", "Obese") or direct_cls.get("is_high_risk"):
                                return True
                except Exception:
                    pass
            return False

        elif col_key == "blood_pressure":
            bp_cls = screening.get("paediatric_bp_classification") or {}
            stg = str(bp_cls.get("stage") or record.get("bp_classification") or "").strip()
            if stg in ("Elevated", "Stage 1 HTN", "Stage 2 HTN", "Hypertension", "Prehypertension"):
                return True
            if bp_cls.get("is_high_risk") or bp_cls.get("alert_level") in ("warning", "critical", "advisory"):
                return True
            if not stg or stg == "Unknown":
                try:
                    bp_parts = re.findall(r'\d+', val_str)
                    if len(bp_parts) >= 2:
                        s_val, d_val = int(bp_parts[0]), int(bp_parts[1])
                        sr = record.get("structured_record") or {}
                        age_raw = record.get("age") or sr.get("age") or (sr.get("personal_information") or {}).get("age")
                        sex_raw = record.get("gender") or sr.get("gender") or (sr.get("personal_information") or {}).get("gender")
                        ht_raw = record.get("height") or sr.get("height") or (sr.get("vitals") or {}).get("height_cm")
                        direct_bp = classify_paediatric_bp(s_val, d_val, float(age_raw) if age_raw else None, sex_raw, float(ht_raw) if ht_raw else None)
                        if direct_bp.get("stage") in ("Elevated", "Stage 1 HTN", "Stage 2 HTN", "Hypertension", "Prehypertension") or direct_bp.get("is_high_risk"):
                            return True
                except Exception:
                    pass
            return False

        elif col_key == "pulse":
            # In accordance with paediatric clinical safety: do not use fixed adult-style thresholds
            # for children. Pulse is recorded faithfully without artificial automatic highlighting.
            return False

        elif col_key == "spinal_caliper_atr":
            atr_cls = screening.get("atr_classification") or {}
            if atr_cls.get("alert_triggered"):
                return True
            try:
                direct_atr = classify_atr(val_str)
                if direct_atr.get("alert_triggered"):
                    return True
            except Exception:
                pass
            return False

        # 2. General Physical & Systemic Examinations
        elif col_key in ("vital_signs_status", "gait_status"):
            if "abnormal" in low or low in ("abn", "ab"):
                return True
            return False

        elif col_key in ("vital_signs_details", "gait_details"):
            if low not in ("normal", "nad", "wnl", "none", "nil", "no", "-", "--", "clear", "intact"):
                return True
            return False

        elif col_key in ("otoscope_right_ear", "otoscope_left_ear"):
            if any(n in low for n in ("normal", "nad", "wnl", "intact", "clear", "healthy", "tm intact", "intact tm")):
                if not any(k in low for k in ("wax", "csom", "aom", "dull", "retracted", "perforation", "discharge", "abnormal", "congestion", "infection", "impaction", "cerumen", "fluid")):
                    return False
            if any(k in low for k in ("wax", "csom", "aom", "dull", "retracted", "perforation", "discharge", "abnormal", "congestion", "infection", "impaction", "cerumen", "fluid")):
                return True
            return False

        elif col_key == "sys_nose":
            if any(n in low for n in ("normal", "nad", "wnl", "clear", "intact", "patent")):
                if not any(k in low for k in ("dns", "deviated", "polyp", "discharge", "abnormal", "congestion", "rhinitis", "obstruction")):
                    return False
            if any(k in low for k in ("dns", "deviated", "polyp", "discharge", "abnormal", "congestion", "rhinitis", "obstruction")):
                return True
            return False

        elif col_key == "sys_throat":
            throat_abn = ("congest", "enlarg", "grade", "erythem", "tonsill", "pharyng", "hypertroph", "exudat", "abnormal", "inflam", "red", "swollen", "infection")
            if any(n in low for n in ("normal", "nad", "wnl", "clear", "healthy")):
                if not any(k in low for k in throat_abn):
                    return False
            if any(k in low for k in throat_abn):
                return True
            return False

        elif col_key == "sys_skin":
            if any(n in low for n in ("normal", "nad", "wnl", "clear", "intact")):
                if not any(k in low for k in ("pityriasis", "eczema", "scabies", "rash", "infection", "lesion", "abnormal", "hypopigmented", "dermatitis")):
                    return False
            if any(k in low for k in ("pityriasis", "eczema", "scabies", "rash", "infection", "lesion", "abnormal", "hypopigmented", "dermatitis")):
                return True
            return False

        elif col_key == "sys_hair":
            if any(n in low for n in ("normal", "nad", "wnl", "clear", "healthy")):
                if not any(k in low for k in ("pediculosis", "lice", "nits", "alopecia", "abnormal")):
                    return False
            if any(k in low for k in ("pediculosis", "lice", "nits", "alopecia", "dandruff", "abnormal", "thinning")):
                return True
            return False

        elif col_key == "sys_other_systems":
            low_cleaned = re.sub(r'\b(non-?tender|not\s+tender|no\s+organomegaly|no\s+hepatomegaly|no\s+splenomegaly|no\s+mass|no\s+murmur[s]?|no\s+added\s+sounds?)\b', '', low)
            if any(n in low for n in ("normal", "nad", "wnl", "clear", "nil", "none", "unremarkable", "s1s2 heard", "no abnormality", "vesicular", "soft", "conscious", "oriented")):
                if not any(k in low_cleaned for k in ("murmur", "wheeze", "rhonchi", "crepitation", "creps", "abnormal", "hepatomegaly", "splenomegaly", "mass", "tender", "organomegaly")):
                    return False
            if any(k in low_cleaned for k in ("murmur", "wheeze", "rhonchi", "crepitation", "creps", "abnormal", "hepatomegaly", "splenomegaly", "mass", "tender", "organomegaly")):
                return True
            return False

        # 3. Vision Screening (Distant visual acuity; Color Vision not present on paediatric form)
        elif col_key in ("vision_distant_right", "vision_distant_left"):
            cleaned = re.sub(r'\s+', '', val_str.upper())
            if cleaned in ("6/6", "6/5", "6/6UNAIDED", "6/6AIDED", "6/6WITHGLASSES"):
                return False
            if any(cleaned.startswith(k) or k in cleaned for k in ("6/9", "6/12", "6/18", "6/24", "6/36", "6/60", "CF", "HM", "PL", "DEFECTIVE", "REDUCED")):
                return True
            return False

        # 4. Dental Screening
        elif col_key == "dental_status":
            if low in ("normal", "nad", "wnl", "healthy"):
                return False
            if any(k in low for k in ("abnormal", "caries", "defective", "cavity", "stains")):
                return True
            return False

        elif col_key == "dental_abnormality":
            if low in ("none", "nil", "no", "nad", "normal", "healthy"):
                return False
            if any(k in low for k in ("caries", "cavity", "cavities", "malocclusion", "fluorosis", "gingivitis", "tartar", "calculus", "stains", "crowding")):
                return True
            return False

        elif col_key == "dental_advice":
            if low in ("none", "nil", "no", "routine hygiene", "brush twice daily", "routine", "healthy"):
                return False
            if any(k in low for k in ("refer", "dentist", "filling", "extraction", "scaling", "consult", "opd", "treatment", "ortho")):
                return True
            return False

        # 5. Referral
        elif col_key == "specialist_referred_to":
            if low in ("none", "nil", "no", "no referral", "not required"):
                return False
            return True

        return False

    def _populate_paediatric_records_worksheet(
        self,
        wb,
        ws,
        records: List[Dict[str, Any]]
    ) -> None:
        """Write columns and rows for paediatric health records into given worksheet matching ASF-D&A-PCS-9.3 and 9.7."""
        # Formats
        header_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': '#0288D1', 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#81D4FA', 'text_wrap': True
        })
        section_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': '#01579B', 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#0277BD', 'text_wrap': True
        })
        cell_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9,
            'valign': 'vcenter', 'border': 1, 'border_color': '#E1F5FE'
        })
        cell_alt_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'valign': 'vcenter',
            'bg_color': '#F1F8E9', 'border': 1, 'border_color': '#E1F5FE'
        })
        alert_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'bg_color': '#FFEBEE', 'font_color': '#C62828',
            'border': 1, 'border_color': '#FFCDD2'
        })
        alert_alt_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'bg_color': '#FFEBEE', 'font_color': '#C62828',
            'border': 1, 'border_color': '#FFCDD2'
        })
        status_reviewed_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#E8F5E9', 'font_color': '#2E7D32',
            'border': 1, 'border_color': '#C8E6C9'
        })
        status_to_check_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9, 'bold': True,
            'valign': 'vcenter', 'align': 'center',
            'bg_color': '#FFF8E1', 'font_color': '#F57F17',
            'border': 1, 'border_color': '#FFECB3'
        })

        ws.set_tab_color('#0288D1')
        ws.set_row(0, 42)
        ws.freeze_panes(1, 6)

        # Official 59-Column Paediatric Schema matching ASF-D&A-PCS-9.3 and ASF-D&A-PCS-9.7
        columns = [
            # col_key, header_label, col_width, is_divider
            ("record_id",               "Record ID",                         18, False),
            ("school_name",             "School / Camp Name",                26, False),
            ("screening_type",          "Screening Type",                    16, False),
            ("moc_doc_id",              "Moc Doc ID",                        14, False),
            ("admission_id",            "Admission ID / Roll No",            16, False),
            ("person_name",             "Student Name",                      24, True),
            ("class_and_section",       "Class & Section",                   14, False),
            ("dob",                     "Date of Birth",                     14, False),
            ("age",                     "Age (Years)",                       12, False),
            ("gender",                  "Gender",                            10, False),
            ("blood_group",             "Blood Group",                       12, False),
            ("parent_name",             "Parent / Guardian Name",            22, False),
            ("mobile_number",           "Mobile Number",                     16, False),
            ("email",                   "Email ID",                          22, False),
            ("address",                 "Residential Address",               30, False),
            ("known_allergies",         "Known Allergies",                   22, False),
            ("current_medication",      "Current Medications",               22, False),
            ("past_surgery",            "Past Surgery",                      18, False),
            ("vaccinated_for_age",      "Vaccinated for Age",                18, False),
            ("behaviour_concerns",      "Behaviour Concerns",                22, False),
            ("other_medical_issues",    "Other Medical Issues",              22, False),
            ("family_adults_count",     "Family Adults Count",               16, False),
            ("family_minors_count",     "Family Minors Count",               16, False),
            ("pediatrician_name",       "Family Pediatrician",               20, False),
            ("consent_screening",       "Consent to School Health Screening", 24, False),
            ("consent_first_aid",       "Consent to First Aid",              18, False),
            ("parent_signature",        "Parent Signature",                  16, False),
            ("exam_date",               "Date of Examination",               16, True),
            ("height",                  "Height (cm)",                       12, False),
            ("weight",                  "Weight (kg)",                       12, False),
            ("waist_circumference",     "Waist Circumference (cm)",          20, False),
            ("bmi",                     "BMI (kg/m²)",                       12, False),
            ("blood_pressure",          "Blood Pressure (mmHg)",             18, False),
            ("pulse",                   "Pulse (bpm)",                       12, False),
            ("spinal_caliper_atr",      "Spinal Caliper ATR (°)",            18, False),
            ("vital_signs_status",      "Vital Signs Status",                16, False),
            ("vital_signs_details",     "Vital Signs Abnormal Details",      24, False),
            ("gait_status",             "Gait Status",                       14, False),
            ("gait_details",            "Gait Abnormality Details",          24, False),
            ("otoscope_right_ear",      "Otoscopy Right Ear",                18, False),
            ("otoscope_left_ear",       "Otoscopy Left Ear",                 18, False),
            ("sys_nose",                "Nose",                              16, False),
            ("sys_throat",              "Throat",                            16, False),
            ("sys_skin",                "Skin",                              16, False),
            ("sys_hair",                "Hair",                              16, False),
            ("sys_other_systems",       "Other Systems Examination",         26, False),
            ("vision_aid_status",       "Visual Aid Status",                 16, False),
            ("vision_distant_right",    "Vision Distant Right (OD)",         18, False),
            ("vision_distant_left",     "Vision Distant Left (OS)",          18, False),
            ("dental_status",           "Dental Status",                     14, False),
            ("dental_abnormality",      "Dental Abnormality",                20, False),
            ("dental_advice",           "Dental Advice",                     20, False),
            ("specialist_referred_to",  "Specialist Referred To",            20, False),
            ("doctor_name",             "Screening Doctor Name",             22, False),
            ("doctor_signature",        "Doctor Signature",                  16, False),
            ("status",                  "Review Status",                     14, True),
            ("reviewer_notes",          "Reviewer Notes",                    24, False),
            ("reviewed_by",             "Reviewed By",                       16, False),
            ("reviewed_at",             "Reviewed At",                       18, False)
        ]

        for col_idx, (col_key, col_name, col_width, is_divider) in enumerate(columns):
            ws.set_column(col_idx, col_idx, col_width)
            fmt = section_fmt if is_divider else header_fmt
            ws.write_string(0, col_idx, col_name, fmt)

        def _get_val(rec: Dict[str, Any], *paths: Any) -> str:
            sr = rec.get("structured_record") or {}
            for path in paths:
                val = None
                if isinstance(path, (list, tuple)):
                    curr = sr
                    for p in path:
                        if isinstance(curr, dict):
                            curr = curr.get(p)
                        else:
                            curr = None
                            break
                    val = curr
                elif isinstance(path, str):
                    val = rec.get(path)
                    if val is None and isinstance(sr, dict):
                        val = sr.get(path)

                if val is not None:
                    if isinstance(val, dict):
                        v = val.get("normalized_value") if val.get("normalized_value") is not None else val.get("value")
                        if v is not None and str(v).strip():
                            return str(v).strip()
                    elif isinstance(val, (list, tuple)) and len(val) >= 1:
                        if val[0] is not None and str(val[0]).strip():
                            return str(val[0]).strip()
                    else:
                        s = str(val).strip()
                        if s:
                            return s
            return ""

        for row_idx, r in enumerate(records, start=1):
            row_fmt = cell_alt_fmt if row_idx % 2 == 0 else cell_fmt
            alert_row_fmt = alert_alt_fmt if row_idx % 2 == 0 else alert_fmt

            # Build record dictionary mapped to columns
            row_dict = {
                "record_id": r.get("id") or r.get("record_id") or "",
                "school_name": _get_val(r, "school_name", ["personal_information", "school_name"], ["personal_information", "institution"], "institution") or (r.get("camp_name") or ""),
                "screening_type": _get_val(r, "screening_type", ["personal_information", "screening_type"]) or "School Health Screening",
                "moc_doc_id": _get_val(r, "moc_doc_id", ["personal_information", "moc_doc_id"]),
                "admission_id": _get_val(r, "admission_id", ["personal_information", "admission_id"]),
                "person_name": _get_val(r, "person_name", "full_name", ["personal_information", "person_name"], ["personal_information", "full_name"]),
                "class_and_section": _get_val(r, "class_and_section", ["personal_information", "class_and_section"]) or f"{_get_val(r, ['personal_information', 'course'])} {_get_val(r, ['personal_information', 'section'])}".strip(),
                "dob": _get_val(r, "dob", "date_of_birth", ["personal_information", "dob"]),
                "age": _get_val(r, "age", "age_years", ["personal_information", "age"]),
                "gender": _get_val(r, "gender", "sex", ["personal_information", "gender"]),
                "blood_group": _get_val(r, "blood_group", ["personal_information", "blood_group"]),
                "parent_name": _get_val(r, "parent_name", "parent_guardian_name", "parent_or_spouse", ["personal_information", "parent_name"], ["personal_information", "parent_or_spouse"]),
                "mobile_number": _get_val(r, "mobile_number", "mobile", "emergency_contact_phone", ["personal_information", "mobile_number"], ["personal_information", "mobile"]),
                "email": _get_val(r, "email", ["personal_information", "email"]),
                "address": _get_val(r, "address", ["personal_information", "address"]),
                "known_allergies": _get_val(r, "known_allergies", ["medical_history", "known_allergies"], ["medical_history", "allergy"]),
                "current_medication": _get_val(r, "current_medication", "current_medications", ["medical_history", "current_medication"], ["medical_history", "medications"]),
                "past_surgery": _get_val(r, "past_surgery", "past_surgeries", ["medical_history", "past_surgery"], ["medical_history", "surgeries"]),
                "vaccinated_for_age": _get_val(r, "vaccinated_for_age", ["medical_history", "vaccinated_for_age"], "immunization_status"),
                "behaviour_concerns": _get_val(r, "behaviour_concerns", ["medical_history", "behaviour_concerns"]),
                "other_medical_issues": _get_val(r, "other_medical_issues", ["medical_history", "other_medical_issues"]),
                "family_adults_count": _get_val(r, "family_adults_count", ["family_history", "family_adults_count"]),
                "family_minors_count": _get_val(r, "family_minors_count", ["family_history", "family_minors_count"]),
                "pediatrician_name": _get_val(r, "pediatrician_name", ["personal_information", "pediatrician_name"], "personal_physician"),
                "consent_screening": _get_val(r, "consent_screening", ["consent", "consent_screening"]) or ("Yes" if r.get("consent_given") is True else ""),
                "consent_first_aid": _get_val(r, "consent_first_aid", ["consent", "consent_first_aid"]),
                "parent_signature": _get_val(r, "parent_signature", ["consent", "parent_signature"]) or ("Present" if r.get("consent_signature") else ""),
                "exam_date": _get_val(r, "exam_date", "examination_date", ["health_assessment", "exam_date"]),
                "height": _get_val(r, "height", "height_cm", ["vitals", "height_cm"], ["health_assessment", "height_cm"]),
                "weight": _get_val(r, "weight", "weight_kg", ["vitals", "weight_kg"], ["health_assessment", "weight_kg"]),
                "waist_circumference": _get_val(r, "waist_circumference", "waist", ["vitals", "waist_circumference"], ["health_assessment", "waist_circumference"]),
                "bmi": _get_val(r, "bmi", ["paediatric_growth", "bmi"], ["vitals", "bmi"], ["health_assessment", "bmi"]),
                "blood_pressure": _get_val(r, "blood_pressure", "bp", ["vitals", "bp"], ["health_assessment", "blood_pressure_mmhg"]),
                "pulse": _get_val(r, "pulse", "pulse_bpm", ["vitals", "pulse"], ["health_assessment", "pulse"]),
                "spinal_caliper_atr": _get_val(r, "spinal_caliper_atr", "atr", ["paediatric_growth", "spinal_caliper_atr"], ["vitals", "spinal_caliper_atr"]),
                "vital_signs_status": _get_val(r, "vital_signs_status", ["vitals", "vital_signs_status"]),
                "vital_signs_details": _get_val(r, "vital_signs_details", ["vitals", "vital_signs_details"]),
                "gait_status": _get_val(r, "gait_status", ["systemic_examination", "gait_status"]),
                "gait_details": _get_val(r, "gait_details", ["systemic_examination", "gait_details"]),
                "otoscope_right_ear": _get_val(r, "otoscope_right_ear", "otoscopy_right_ear", ["systemic_examination", "otoscope_right_ear"]),
                "otoscope_left_ear": _get_val(r, "otoscope_left_ear", "otoscopy_left_ear", ["systemic_examination", "otoscope_left_ear"]),
                "sys_nose": _get_val(r, "sys_nose", "nose", ["systemic_examination", "sys_nose"], ["systemic_examination", "nose"]),
                "sys_throat": _get_val(r, "sys_throat", "throat", ["systemic_examination", "sys_throat"], ["systemic_examination", "throat"]),
                "sys_skin": _get_val(r, "sys_skin", "skin", ["systemic_examination", "sys_skin"], ["systemic_examination", "skin"]),
                "sys_hair": _get_val(r, "sys_hair", "hair", ["systemic_examination", "sys_hair"], ["systemic_examination", "hair"]),
                "sys_other_systems": _get_val(r, "sys_other_systems", "other_systems", ["systemic_examination", "sys_other_systems"], "systemic_findings"),
                "vision_aid_status": _get_val(r, "vision_aid_status", ["vision_assessment", "vision_aid_status"], "vision_category"),
                "vision_distant_right": _get_val(r, "vision_distant_right", "distant_vision_right", ["vision_assessment", "right_eye"], ["vision", "right_eye"]),
                "vision_distant_left": _get_val(r, "vision_distant_left", "distant_vision_left", ["vision_assessment", "left_eye"], ["vision", "left_eye"]),
                "dental_status": _get_val(r, "dental_status", ["dental_assessment", "dental_status"], ["dental", "status"]),
                "dental_abnormality": _get_val(r, "dental_abnormality", ["dental_assessment", "dental_abnormality"], ["dental", "abnormality"]),
                "dental_advice": _get_val(r, "dental_advice", ["dental_assessment", "dental_advice"], ["dental", "advice"]),
                "specialist_referred_to": _get_val(r, "specialist_referred_to", "referral_specialist", ["referral", "specialist"]),
                "doctor_name": _get_val(r, "doctor_name", "screening_doctor", ["health_assessment", "doctor_name"]) or (r.get("scanned_by") or ""),
                "doctor_signature": _get_val(r, "doctor_signature", ["health_assessment", "doctor_signature"]) or ("Present" if r.get("doctor_signature") else ""),
                "status": r.get("status") or "needs_review",
                "reviewer_notes": r.get("reviewer_notes") or "",
                "reviewed_by": r.get("reviewed_by") or "",
                "reviewed_at": r.get("reviewed_at") or ""
            }

            # 1. Fallback BMI resolution (from examination_details or height & weight)
            bmi_val = row_dict.get("bmi")
            if not bmi_val or str(bmi_val).strip() in ("", "-", "None", "null", "undefined"):
                exam_det = str(r.get("examination_details") or "")
                m_bmi = re.search(r'BMI:\s*([\d.]+)', exam_det, re.IGNORECASE)
                if m_bmi:
                    row_dict["bmi"] = m_bmi.group(1)
                else:
                    try:
                        h_f = float(str(row_dict.get("height", "")).strip())
                        w_f = float(str(row_dict.get("weight", "")).strip())
                        if h_f > 0 and w_f > 0:
                            row_dict["bmi"] = str(round(w_f / ((h_f / 100.0) ** 2), 1))
                    except (ValueError, TypeError):
                        pass

            # 2. ENT findings normalization (Blank -> "Not Recorded", Unclear -> "Needs Review", text preserved)
            def _clean_ent_val(val: Any) -> str:
                if val is None or str(val).strip() in ("", "-", "--", "None", "null"):
                    return "Not Recorded"
                s = str(val).strip()
                s_low = s.lower()
                if s_low in ("unclear", "needs review", "ambiguous", "?", "???"):
                    return "Needs Review"
                if s_low in ("normal", "nad", "wnl", "clear", "intact"):
                    return "Normal"
                if s_low in ("abnormal", "abn"):
                    return "Abnormal"
                return s

            row_dict["otoscope_right_ear"] = _clean_ent_val(row_dict.get("otoscope_right_ear"))
            row_dict["otoscope_left_ear"] = _clean_ent_val(row_dict.get("otoscope_left_ear"))
            row_dict["sys_nose"] = _clean_ent_val(row_dict.get("sys_nose"))
            row_dict["sys_throat"] = _clean_ent_val(row_dict.get("sys_throat"))

            # 3. Systemic examination: ensure Abdomen & CNS are preserved in sys_other_systems if entered
            abd_val = _get_val(r, "abdomen", "sys_abdomen", ["systemic_examination", "abdomen"])
            cns_val = _get_val(r, "cns", "sys_cns", ["systemic_examination", "central_nervous_system"])
            if (abd_val or cns_val) and (not row_dict["sys_other_systems"] or row_dict["sys_other_systems"] in ("Normal", "NAD")):
                sys_parts = []
                if row_dict["sys_other_systems"]:
                    sys_parts.append(row_dict["sys_other_systems"])
                if abd_val:
                    sys_parts.append(f"Abdomen: {abd_val}")
                if cns_val:
                    sys_parts.append(f"CNS: {cns_val}")
                row_dict["sys_other_systems"] = "; ".join(sys_parts)

            # Evaluate clinical screening using existing engine
            screening_fields = {
                "age": row_dict["age"],
                "gender": row_dict["gender"],
                "height_cm": row_dict["height"],
                "weight_kg": row_dict["weight"],
                "bp": row_dict["blood_pressure"],
                "atr": row_dict["spinal_caliper_atr"],
                "dob": row_dict["dob"],
                "exam_date": row_dict["exam_date"],
            }
            screening_res = classify_paediatric(screening_fields)

            # If record already had computed screening, merge it
            if r.get("paediatric_bmi_classification"):
                screening_res["paediatric_bmi_classification"] = r["paediatric_bmi_classification"]
            if r.get("paediatric_bp_classification"):
                screening_res["paediatric_bp_classification"] = r["paediatric_bp_classification"]

            status = row_dict["status"]
            st_fmt = status_reviewed_fmt if status in ("reviewed", "approved") else status_to_check_fmt

            ws.set_row(row_idx, 20)
            for c_idx, (col_key, col_name, col_width, is_divider) in enumerate(columns):
                val = row_dict.get(col_key, "")
                is_abn = self._is_paediatric_field_abnormal(col_key, val, r, screening_res)

                if col_key == "status":
                    fmt_to_use = st_fmt
                elif is_abn:
                    fmt_to_use = alert_row_fmt
                else:
                    fmt_to_use = row_fmt

                ws.write_string(row_idx, c_idx, sanitize_csv_cell(val), fmt_to_use)



    def _populate_paediatric_summary_worksheet(
        self,
        wb,
        ws_summary,
        camp: Optional[Dict[str, Any]],
        records: List[Dict[str, Any]]
    ) -> None:
        """Write paediatric camp summary metrics into given worksheet."""
        header_fmt = wb.add_format({
            'bold': True, 'font_name': 'Calibri', 'font_size': 10,
            'bg_color': '#0288D1', 'font_color': '#FFFFFF',
            'valign': 'vcenter', 'align': 'center',
            'border': 1, 'border_color': '#81D4FA', 'text_wrap': True
        })
        cell_fmt = wb.add_format({
            'font_name': 'Calibri', 'font_size': 9,
            'valign': 'vcenter', 'border': 1, 'border_color': '#E1F5FE'
        })
        # Sheet 3: Paediatric Camp Summary
        # ws_summary passed in as parameter
        ws_summary.set_tab_color('#01579B')
        summary_cols = [
            ("School / Camp Name", 28),
            ("Camp Date", 14),
            ("Total Screened", 14),
            ("Needs Review", 12),
            ("Reviewed & Approved", 16),
            ("Underweight (<3rd %ile)", 18),
            ("Overweight/Obese (>85th %ile)", 20),
            ("Hypertension Suspects", 18),
            ("Scoliosis Alerts (ATR >= 5°)", 20),
            ("Dental Caries Cases", 16),
            ("Vision Impairment Cases", 18),
            ("Parental Consent Withheld", 18)
        ]
        for col_idx, (col_name, col_width) in enumerate(summary_cols):
            ws_summary.set_column(col_idx, col_idx, col_width)
            ws_summary.write_string(0, col_idx, col_name, header_fmt)

        c_name = camp.get("name") if camp else "School Screening Camp"
        c_date = camp.get("date_str") if camp else "Today"
        total_scanned = len(records)
        needs_review = sum(1 for r in records if r.get("status") == "needs_review")
        reviewed = sum(1 for r in records if r.get("status") in ("reviewed", "approved"))
        underweight = sum(1 for r in records if "underweight" in str(r.get("bmi_classification", "")).lower())
        overweight = sum(1 for r in records if any(k in str(r.get("bmi_classification", "")).lower() for k in ("overweight", "obese")))
        htn = sum(1 for r in records if any(k in str(r.get("bp_classification", "")).lower() for k in ("stage 1", "stage 2", "hypertension")))
        scoliosis = sum(1 for r in records if any("scoliosis" in str(a).lower() for a in r.get("paediatric_alerts", [])))
        caries = sum(1 for r in records if "caries" in str(r.get("dental_caries", "")).lower())
        vision_issues = sum(1 for r in records if str(r.get("vision_distant_right", "")).strip() not in ("", "6/6") or str(r.get("vision_distant_left", "")).strip() not in ("", "6/6"))
        consent_withheld = sum(1 for r in records if r.get("consent_given") is False)

        sum_row = [
            sanitize_csv_cell(c_name),
            sanitize_csv_cell(c_date),
            total_scanned,
            needs_review,
            reviewed,
            underweight,
            overweight,
            htn,
            scoliosis,
            caries,
            vision_issues,
            consent_withheld
        ]
        ws_summary.set_row(1, 20)
        for c_idx, val in enumerate(sum_row):
            if isinstance(val, (int, float)):
                ws_summary.write_number(1, c_idx, val, cell_fmt)
            else:
                ws_summary.write_string(1, c_idx, str(val), cell_fmt)



    def _generate_paediatric_excel_export(
        self,
        camp_id: Optional[str],
        camp: Optional[Dict[str, Any]],
        records: List[Dict[str, Any]],
        only_reviewed: bool = False
    ) -> bytes:
        """Generate specialized Paediatric Health Screening (.xlsx) workbook for school camps."""
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True, 'remove_timezone': True})

        sheet_name = "Paediatric Records"
        if camp:
            c_name = re.sub(r'[:/\\?*\[\]]', '', str(camp.get("name", "School Records")))[:30].strip()
            sheet_name = c_name or "Paediatric Records"

        ws = wb.add_worksheet(sheet_name)
        self._populate_paediatric_records_worksheet(wb, ws, records)
        self._write_attributes_sheet(wb, "paediatric", tab_color='#0277BD', header_bg='#01579B')

        ws_summary = wb.add_worksheet("Paediatric Summary")
        self._populate_paediatric_summary_worksheet(wb, ws_summary, camp, records)

        wb.close()
        output.seek(0)
        return output.getvalue()

    def _generate_multi_family_excel_export(
        self,
        camp_id: Optional[str],
        camp: Optional[Dict[str, Any]],
        records: List[Dict[str, Any]],
        camps: List[Dict[str, Any]],
        only_reviewed: bool = False
    ) -> bytes:
        """Generate multi-family Excel (.xlsx) workbook with separate sheets for Adult and Paediatric records."""
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True, 'remove_timezone': True})

        adult_records = [r for r in records if r.get("target_group") != "paediatric" and not r.get("is_paediatric")]
        paed_records = [r for r in records if r.get("target_group") == "paediatric" or r.get("is_paediatric")]

        # Sheet 1: Adult Health Records
        ws_adult = wb.add_worksheet("Adult Health Records")
        self._populate_adult_records_worksheet(wb, ws_adult, adult_records)
        self._write_attributes_sheet(wb, "adult", tab_color='#AD1457', header_bg='#880E4F', sheet_title="Adult Form Attributes")

        # Sheet 2: Paediatric Records
        ws_paed = wb.add_worksheet("Paediatric Records")
        self._populate_paediatric_records_worksheet(wb, ws_paed, paed_records)
        self._write_attributes_sheet(wb, "paediatric", tab_color='#0277BD', header_bg='#01579B', sheet_title="Paediatric Form Attributes")

        # Sheet 3: Camp Summary
        ws_camps = wb.add_worksheet("Camp Summary")
        self._populate_camp_summary_worksheet(wb, ws_camps, camps)

        wb.close()
        output.seek(0)
        return output.getvalue()
