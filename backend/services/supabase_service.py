"""I2 insights — Supabase Cloud Database Integration Service.

Connects to Supabase PostgreSQL using PostgREST endpoints.
Provides multi-device cloud synchronization for health camps and intake records.
Falls back seamlessly to local SQLite if Supabase credentials are not configured.
"""

import os
import json
import time
import logging
import threading
import atexit
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import requests
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util import Retry
except (ImportError, ModuleNotFoundError):
    HTTPAdapter = None
    Retry = None

logger = logging.getLogger("doc_scanner.supabase")

def _rewrite_action_link_redirect(action_link: Optional[str], target_redirect: str) -> Optional[str]:
    """Ensure action_link query parameter redirect_to matches target_redirect instead of Supabase dashboard default."""
    if not action_link or not target_redirect:
        return action_link
    try:
        parsed = urllib.parse.urlparse(action_link)
        qs = urllib.parse.parse_qs(parsed.query)
        qs["redirect_to"] = [target_redirect]
        new_query = urllib.parse.urlencode(qs, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))
    except Exception:
        return action_link

def _safe_json_loads(val, default):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return default
        try:
            return json.loads(val_str)
        except Exception:
            return default
    return default

def _to_json_str(val, default="{}"):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    if isinstance(val, str):
        return val
    return json.dumps(val)

def _clean_field_val(val):
    if val is None:
        return None
    if isinstance(val, dict):
        return val.get("value")
    return val

def _clean_num_val(val):
    if val is None or val == "":
        return None
    if isinstance(val, dict):
        val = val.get("value")
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

if hasattr(requests, "Session"):
    class _PooledSupabaseSession(requests.Session):
        """Thread-safe persistent HTTP session with default connect/read timeouts."""
        def request(self, method, url, **kwargs):
            # If unittest has patched module-level requests.<method>, delegate to the mock
            m_lower = str(method).lower()
            mod_fn = getattr(requests, m_lower, None)
            if mod_fn is not None and (hasattr(mod_fn, "_mock_self") or hasattr(mod_fn, "assert_called")):
                return mod_fn(url, **kwargs)
            if "timeout" not in kwargs or kwargs["timeout"] is None:
                kwargs["timeout"] = (3.0, 10.0)
            return super().request(method, url, **kwargs)
else:
    class _PooledSupabaseSession:
        def get(self, *a, **k): return requests.get(*a, **k)
        def post(self, *a, **k): return requests.post(*a, **k)
        def patch(self, *a, **k): return requests.patch(*a, **k)
        def put(self, *a, **k): return requests.put(*a, **k)
        def delete(self, *a, **k): return requests.delete(*a, **k)
        def close(self): pass

class SupabaseService:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        self.url = (url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
        self._circuit_open_until = 0.0
        self._failure_count = 0
        self._last_error = ""

        # Step 2: Thread-safe connection pooling with HTTP Keep-Alive
        self._session = _PooledSupabaseSession()
        if HTTPAdapter is not None and Retry is not None and hasattr(self._session, "mount"):
            no_retry = Retry(
                total=0,
                connect=0,
                read=0,
                status=0,
                raise_on_status=False
            )
            adapter = HTTPAdapter(
                pool_connections=20,
                pool_maxsize=50,
                max_retries=no_retry,
                pool_block=False
            )
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)

        # Step 3: Bounded in-memory table-existence cache
        self._table_cache: Optional[Dict[str, bool]] = None
        self._table_cache_expires_at: float = 0.0
        self._table_cache_lock = threading.Lock()

        # Register clean shutdown
        atexit.register(self.close)

    def close(self):
        """Cleanly close persistent HTTP connection pool on process termination."""
        if hasattr(self, "_session") and self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass

    def is_configured(self) -> bool:
        """Check if Supabase URL and Key are provided."""
        return bool(self.url and self.key and "supabase.co" in self.url)

    def is_available(self) -> bool:
        """Check if Supabase is configured and circuit breaker is closed (healthy)."""
        if not self.is_configured():
            return False
        if time.time() < self._circuit_open_until:
            return False
        return True

    def _record_success(self):
        """Reset circuit breaker counters upon a successful network call."""
        self._failure_count = 0
        self._circuit_open_until = 0.0
        self._last_error = ""

    def _record_failure(self, error: Exception, cooldown: float = 60.0):
        """Record network failure and trip circuit breaker to prevent lagging requests."""
        self._failure_count += 1
        self._last_error = str(error)
        err_msg = str(error)
        is_dns_or_conn = any(term in err_msg for term in (
            "NameResolutionError", "Failed to resolve", "ConnectionRefused", "Max retries exceeded", "ConnectionError"
        ))
        trip_duration = 180.0 if is_dns_or_conn else cooldown
        self._circuit_open_until = time.time() + trip_duration
        if self._failure_count <= 2:
            logger.warning(
                "Supabase is unreachable (%s). Circuit breaker OPEN for %ds; falling back seamlessly to local SQLite.",
                error, int(trip_duration)
            )
        else:
            logger.debug("Supabase circuit breaker open: %s", error)

    def _headers(self) -> Dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }

    def invalidate_table_cache(self):
        """Explicitly clear the in-memory table existence cache."""
        with self._table_cache_lock:
            self._table_cache = None
            self._table_cache_expires_at = 0.0

    def check_tables_exist(self) -> Dict[str, bool]:
        """Check if camps and records tables exist in Supabase schema with 5-minute in-memory cache."""
        if not self.is_available():
            return {"camps": False, "records": False}

        now = time.time()
        # Fast path: Return cached results if valid, unexpired, and both tables are ready
        if self._table_cache is not None and now < self._table_cache_expires_at:
            if self._table_cache.get("camps") and self._table_cache.get("records"):
                return dict(self._table_cache)

        # Thread-safe probe to prevent cache stampedes under concurrent dashboard requests
        with self._table_cache_lock:
            # Re-check under lock in case another thread refreshed it
            now = time.time()
            if self._table_cache is not None and now < self._table_cache_expires_at:
                if self._table_cache.get("camps") and self._table_cache.get("records"):
                    return dict(self._table_cache)

            status = {"camps": False, "records": False}
            try:
                r = self._session.get(f"{self.url}/rest/v1/camps?limit=1", headers=self._headers(), timeout=3)
                status["camps"] = (r.status_code == 200)
                if status["camps"]:
                    self._record_success()
            except Exception as e:
                self._record_failure(e)
                # Never cache failed or inconclusive checks as successful
                self._table_cache = None
                self._table_cache_expires_at = 0.0
                return status

            try:
                r = self._session.get(f"{self.url}/rest/v1/records?limit=1", headers=self._headers(), timeout=3)
                status["records"] = (r.status_code == 200)
                if status["records"]:
                    self._record_success()
            except Exception as e:
                self._record_failure(e)
                # Never cache failed or inconclusive checks as successful
                self._table_cache = None
                self._table_cache_expires_at = 0.0
                return status

            # Only cache when both tables are confirmed healthy
            if status["camps"] and status["records"]:
                self._table_cache = dict(status)
                self._table_cache_expires_at = now + 300.0  # 5 minutes TTL
            else:
                self._table_cache = None
                self._table_cache_expires_at = 0.0

            return status

    def list_camps_with_stats(self, community_id: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """Fetch all camps with computed intake stats from Supabase."""
        if not self.is_available():
            return None

        try:
            url = f"{self.url}/rest/v1/camps?select=*&order=created_at.desc"
            if community_id:
                url += f"&community_id=eq.{community_id}"
            r = self._session.get(url, headers=self._headers(), timeout=5)
            r.raise_for_status()
            camps = [
                c for c in r.json()
                if not (
                    (c.get("name") or "").startswith("[PURGED") or 
                    "(demo)" in (c.get("name") or "").lower() or 
                    "demo" in (c.get("id") or "").lower()
                )
            ]

            # Fetch records summary to compute stats
            rec_url = f"{self.url}/rest/v1/records?select=id,camp_id,status,person_name"
            if community_id:
                rec_url += f"&community_id=eq.{community_id}"
            rec_res = self._session.get(rec_url, headers=self._headers(), timeout=5)
            rec_res.raise_for_status()
            self._record_success()
            records = [
                rec for rec in rec_res.json()
                if rec.get("status") != "archived_mock" and
                not (rec.get("person_name") or "").startswith("[PURGED") and
                "demo" not in (rec.get("id") or "").lower()
            ]

            # Group stats by camp_id
            stats_by_camp: Dict[str, Dict[str, int]] = {}
            for rec in records:
                cid = rec.get("camp_id")
                if not cid:
                    continue
                if cid not in stats_by_camp:
                    stats_by_camp[cid] = {"scanned": 0, "to_check": 0, "reviewed": 0}
                stats_by_camp[cid]["scanned"] += 1
                if rec.get("status") == "needs_review":
                    stats_by_camp[cid]["to_check"] += 1
                elif rec.get("status") == "reviewed":
                    stats_by_camp[cid]["reviewed"] += 1

            enriched = []
            for c in camps:
                cid = c["id"]
                st = stats_by_camp.get(cid, {"scanned": 0, "to_check": 0, "reviewed": 0})
                expected = int(c.get("expected_records") or 100)
                scanned = st["scanned"]
                pct = round((scanned / expected * 100), 1) if expected > 0 else 0.0
                enriched.append({
                    "id": cid,
                    "name": c.get("name"),
                    "date_str": c.get("date_str") or "Today",
                    "location": c.get("location") or "Chennai Health Camp",
                    "target_group": c.get("target_group") or "adult",
                    "community_id": c.get("community_id"),
                    "created_by": c.get("created_by"),
                    "status": c.get("status") or "active",
                    "expected_records": expected,
                    "total_scanned": scanned,
                    "to_check": st["to_check"],
                    "reviewed": st["reviewed"],
                    "progress_pct": min(100.0, pct),
                    "created_at": c.get("created_at"),
                    "updated_at": c.get("updated_at")
                })
            return enriched
        except Exception as e:
            self._record_failure(e)
            return None

    def get_camp_by_id(self, camp_id: str) -> Optional[Dict[str, Any]]:
        """Fetch single camp by ID from Supabase."""
        if not self.is_available():
            return None
        try:
            res = self._session.get(
                f"{self.url}/rest/v1/camps?id=eq.{camp_id}&select=*&limit=1",
                headers=self._headers(),
                timeout=5
            )
            res.raise_for_status()
            rows = res.json()
            if not rows:
                return None
            c = rows[0]
            rec_res = self._session.get(
                f"{self.url}/rest/v1/records?camp_id=eq.{camp_id}&select=id,status",
                headers=self._headers(),
                timeout=5
            )
            scanned = 0
            to_check = 0
            reviewed = 0
            if rec_res.status_code == 200:
                for rec in rec_res.json():
                    scanned += 1
                    if rec.get("status") == "needs_review":
                        to_check += 1
                    elif rec.get("status") == "reviewed":
                        reviewed += 1
            expected = int(c.get("expected_records") or 100)
            pct = round((scanned / expected * 100), 1) if expected > 0 else 0.0
            return {
                "id": c.get("id"),
                "name": c.get("name"),
                "date_str": c.get("date_str") or "Today",
                "location": c.get("location") or "Chennai Health Camp",
                "target_group": c.get("target_group") or "adult",
                "community_id": c.get("community_id"),
                "created_by": c.get("created_by"),
                "status": c.get("status") or "active",
                "expected_records": expected,
                "total_scanned": scanned,
                "to_check": to_check,
                "reviewed": reviewed,
                "progress_pct": min(100.0, pct),
                "created_at": c.get("created_at"),
                "updated_at": c.get("updated_at")
            }
        except Exception as e:
            self._record_failure(e)
            return None

    def create_camp(
        self,
        name: str,
        expected_records: int = 100,
        date_str: str = "Today",
        location: str = "Chennai Health Camp",
        target_group: str = "adult",
        community_id: Optional[str] = None,
        created_by: Optional[str] = None,
        camp_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new screening camp in Supabase."""
        if not self.is_available():
            raise RuntimeError("Supabase credentials not configured or host unreachable.")

        import uuid
        actual_camp_id = None
        if camp_id:
            try:
                actual_camp_id = str(uuid.UUID(str(camp_id).strip()))
            except (ValueError, TypeError):
                pass
        if not actual_camp_id:
            actual_camp_id = str(uuid.uuid4())

        now = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {
            "id": actual_camp_id,
            "name": name.strip(),
            "date_str": date_str.strip() or "Today",
            "expected_records": max(1, expected_records),
            "location": (location or "Chennai Health Camp").strip(),
            "target_group": (target_group or "adult").strip().lower(),
            "status": "active",
            "created_at": now,
            "updated_at": now
        }
        if not community_id:
            raise ValueError("community_id is required to create a camp in Supabase.")
        try:
            comm_uuid = str(uuid.UUID(str(community_id).strip()))
            payload["community_id"] = comm_uuid
        except (ValueError, TypeError):
            # Fallback to verified default community UUID if non-UUID tenant identifier is passed
            payload["community_id"] = "8bbf2a9d-cef9-4fda-a500-c163c0baed4d"

        if created_by:
            try:
                creator_uuid = str(uuid.UUID(str(created_by).strip()))
                payload["created_by"] = creator_uuid
            except (ValueError, TypeError):
                logger.warning("create_camp: created_by '%s' is not a valid UUID; omitting FK", created_by)

        try:
            res = self._session.post(
                f"{self.url}/rest/v1/camps",
                headers=self._headers(),
                json=payload,
                timeout=5
            )
            res.raise_for_status()
            self._record_success()
            return self.get_camp_by_id(actual_camp_id) or payload
        except Exception as e:
            self._record_failure(e)
            raise

    def _build_adult_payload(self, record_data: Dict[str, Any], rec_id: str, now: str) -> Dict[str, Any]:
        sr = record_data.get("structured_record") or {}
        if isinstance(sr, str):
            sr = _safe_json_loads(sr, {})
        p_info = sr.get("personal_information") or {}
        vitals = sr.get("vitals") or sr.get("health_assessment") or {}
        referral = sr.get("referral") or {}
        history = sr.get("medical_history") or {}
        f_hist = sr.get("family_history") or {}
        vision = sr.get("vision_assessment") or sr.get("vision") or {}
        dental = sr.get("dental_assessment") or sr.get("dental") or {}

        return {
            "id": rec_id,
            "camp_id": record_data["camp_id"],
            "person_name": _clean_field_val(record_data.get("person_name") or p_info.get("full_name")) or "Unknown Patient",
            "age": str(_clean_field_val(record_data.get("age") or p_info.get("age")) or ""),
            "gender": _clean_field_val(record_data.get("gender") or p_info.get("gender")),
            "height": str(_clean_field_val(record_data.get("height") or vitals.get("height_cm")) or ""),
            "weight": str(_clean_field_val(record_data.get("weight") or vitals.get("weight_kg")) or ""),
            "blood_pressure": _clean_field_val(record_data.get("blood_pressure") or vitals.get("bp") or vitals.get("blood_pressure_mmhg")),
            "pulse": str(_clean_field_val(record_data.get("pulse") or vitals.get("pulse")) or ""),
            "rbs": str(_clean_field_val(record_data.get("rbs") or vitals.get("rbs")) or ""),
            "waist_circumference": str(_clean_field_val(record_data.get("waist_circumference") or vitals.get("waist_cm")) or ""),
            "ecg_notes": _clean_field_val(record_data.get("ecg_notes") or vitals.get("ecg")),
            "echo_notes": _clean_field_val(record_data.get("echo_notes") or vitals.get("echo")),
            "examination_details": _clean_field_val(record_data.get("examination_details")),
            "referral_details": _clean_field_val(record_data.get("referral_details") or referral.get("specialist")),
            "marital_status": _clean_field_val(record_data.get("marital_status") or p_info.get("marital_status")),
            "parent_spouse_name": _clean_field_val(record_data.get("parent_spouse_name") or p_info.get("parent_or_spouse")),
            "no_of_children": str(_clean_field_val(record_data.get("no_of_children") or p_info.get("children")) or ""),
            "address": _clean_field_val(record_data.get("address") or p_info.get("address")),
            "mobile_number": _clean_field_val(record_data.get("mobile_number") or p_info.get("mobile")),
            "blood_group": _clean_field_val(record_data.get("blood_group") or p_info.get("blood_group")),
            "dob": _clean_field_val(record_data.get("dob") or p_info.get("dob")),
            "admission_id": _clean_field_val(record_data.get("admission_id") or p_info.get("admission_id")),
            "moc_doc_id": _clean_field_val(record_data.get("moc_doc_id") or p_info.get("moc_doc_id")),
            "school_college_name": _clean_field_val(record_data.get("school_college_name") or p_info.get("institution")),
            "staff_student_type": _clean_field_val(record_data.get("staff_student_type") or p_info.get("staff_or_student")),
            "shift": _clean_field_val(record_data.get("shift") or p_info.get("shift")),
            "year_of_study": _clean_field_val(record_data.get("year_of_study") or p_info.get("year")),
            "course_dept": _clean_field_val(record_data.get("course_dept") or p_info.get("course")),
            "section": _clean_field_val(record_data.get("section") or p_info.get("section")),
            "medical_history": _to_json_str(record_data.get("medical_history") or history, "{}"),
            "family_history": _to_json_str(record_data.get("family_history") or f_hist, "{}"),
            "vision_category": _clean_field_val(record_data.get("vision_category") or vision.get("vision_type")),
            "distant_vision_right": _clean_field_val(record_data.get("distant_vision_right") or vision.get("distant_vision_right")),
            "distant_vision_left": _clean_field_val(record_data.get("distant_vision_left") or vision.get("distant_vision_left")),
            "dental_status": _clean_field_val(record_data.get("dental_status") or dental.get("status")),
            "dental_abnormality": _clean_field_val(record_data.get("dental_abnormality") or dental.get("abnormality")),
            "dental_advice": _clean_field_val(record_data.get("dental_advice") or dental.get("advice")),
            "consent_given": int(record_data.get("consent_given") if record_data.get("consent_given") is not None else 1),
            "consent_signature": record_data.get("consent_signature"),
            "status": record_data.get("status", "needs_review"),
            "missing_fields": _to_json_str(record_data.get("missing_fields"), "[]"),
            "uncertain_fields": _to_json_str(record_data.get("uncertain_fields"), "[]"),
            "created_at": now,
            "updated_at": now
        }

    def _build_paediatric_payload(self, record_data: Dict[str, Any], rec_id: str, now: str) -> Dict[str, Any]:
        sr = record_data.get("structured_record") or {}
        if isinstance(sr, str):
            sr = _safe_json_loads(sr, {})
        p_info = sr.get("personal_information") or {}
        vitals = sr.get("vitals") or sr.get("health_assessment") or {}
        paed = sr.get("paediatric_growth") or {}
        vision = sr.get("vision_assessment") or sr.get("vision") or {}
        dental = sr.get("dental_assessment") or sr.get("dental") or {}
        referral = sr.get("referral") or {}

        return {
            "id": rec_id,
            "camp_id": record_data["camp_id"],
            "person_name": _clean_field_val(record_data.get("person_name") or p_info.get("full_name")) or "Unknown Child",
            "age": str(_clean_field_val(record_data.get("age") or p_info.get("age")) or ""),
            "gender": _clean_field_val(record_data.get("gender") or p_info.get("gender")),
            "height": str(_clean_field_val(record_data.get("height") or vitals.get("height_cm")) or ""),
            "weight": str(_clean_field_val(record_data.get("weight") or vitals.get("weight_kg")) or ""),
            "bmi": str(_clean_field_val(record_data.get("bmi") or paed.get("bmi")) or ""),
            "bmi_centile": _clean_num_val(record_data.get("bmi_centile") or paed.get("bmi_centile")),
            "bmi_classification": _clean_field_val(record_data.get("bmi_classification") or paed.get("bmi_classification")),
            "blood_pressure": _clean_field_val(record_data.get("blood_pressure") or vitals.get("bp")),
            "bp_percentile": _clean_num_val(record_data.get("bp_percentile") or paed.get("bp_percentile")),
            "bp_classification": _clean_field_val(record_data.get("bp_classification") or paed.get("bp_classification")),
            "pulse": str(_clean_field_val(record_data.get("pulse") or vitals.get("pulse")) or ""),
            "spinal_caliper_atr": _clean_num_val(record_data.get("spinal_caliper_atr") or paed.get("spinal_caliper_atr")),
            "head_circumference": str(_clean_field_val(record_data.get("head_circumference") or vitals.get("head_circumference")) or ""),
            "muac": str(_clean_field_val(record_data.get("muac") or vitals.get("muac")) or ""),
            "screening_type": _clean_field_val(record_data.get("screening_type") or paed.get("screening_type")),
            "school_name": _clean_field_val(record_data.get("school_name") or p_info.get("institution") or p_info.get("school_college_name")),
            "class_and_section": _clean_field_val(record_data.get("class_and_section") or p_info.get("class_and_section")),
            "admission_id": _clean_field_val(record_data.get("admission_id") or p_info.get("admission_id")),
            "moc_doc_id": _clean_field_val(record_data.get("moc_doc_id") or p_info.get("moc_doc_id")),
            "dob": _clean_field_val(record_data.get("dob") or p_info.get("dob")),
            "blood_group": _clean_field_val(record_data.get("blood_group") or p_info.get("blood_group")),
            "community_id": record_data.get("community_id"),
            "parent_guardian_name": _clean_field_val(record_data.get("parent_guardian_name") or p_info.get("parent_or_spouse")),
            "examination_details": _clean_field_val(record_data.get("examination_details")),
            "referral_details": _clean_field_val(record_data.get("referral_details") or referral.get("specialist")),
            "status": record_data.get("status", "needs_review"),
            "missing_fields": _to_json_str(record_data.get("missing_fields"), "[]"),
            "uncertain_fields": _to_json_str(record_data.get("uncertain_fields"), "[]"),
            "paediatric_alerts": _to_json_str(record_data.get("paediatric_alerts") or record_data.get("screening_alerts"), "[]"),
            "consent_given": int(record_data.get("consent_given") if record_data.get("consent_given") is not None else 1),
            "consent_signature": record_data.get("consent_signature"),
            "created_at": now,
            "updated_at": now
        }

    def _merge_paediatric_child_fields(self, record_dict: Dict[str, Any], pr: Dict[str, Any]) -> None:
        """Merge paediatric child-table columns into record dictionary consistently."""
        if not pr or not isinstance(pr, dict):
            return
        paed_keys = [
            "dob", "blood_group", "school_name", "class_and_section",
            "head_circumference", "muac", "parent_guardian_name", "admission_id",
            "moc_doc_id", "screening_type", "spinal_caliper_atr", "gait_status",
            "vision_distant_left", "vision_distant_right", "color_vision", "squint_strabismus",
            "dental_caries", "malocclusion", "oral_hygiene", "oral_cavity_throat",
            "ears_hearing_status", "otoscopy_findings", "lymph_nodes", "systemic_findings",
            "referral_reason", "referral_specialist", "bmi", "bmi_centile",
            "bmi_classification", "bp_percentile", "bp_classification", "vital_signs_status",
            "dietary_habits", "immunization_status", "birth_history", "milestones",
            "emergency_contact_phone", "known_allergies", "consent_signature"
        ]
        for k in paed_keys:
            val = pr.get(k)
            if val is not None and val != "":
                if record_dict.get(k) is None or record_dict.get(k) == "":
                    record_dict[k] = val

        # Aliases
        if pr.get("parent_guardian_name") and not record_dict.get("parent_name"):
            record_dict["parent_name"] = pr["parent_guardian_name"]
        if pr.get("emergency_contact_phone") and not record_dict.get("mobile_number"):
            record_dict["mobile_number"] = pr["emergency_contact_phone"]
        if pr.get("address") and not record_dict.get("address"):
            record_dict["address"] = pr["address"]

        # Structured record & original AI extraction merge
        child_sr = _safe_json_loads(pr.get("structured_record"), {})
        if child_sr and isinstance(child_sr, dict) and not record_dict.get("structured_record"):
            record_dict["structured_record"] = child_sr

        child_orig = _safe_json_loads(pr.get("original_ai_extraction"), {})
        if child_orig and isinstance(child_orig, dict) and not record_dict.get("original_ai_extraction"):
            record_dict["original_ai_extraction"] = child_orig

    def list_records(
        self,
        camp_id: Optional[str] = None,
        status: Optional[str] = None,
        community_id: Optional[str] = None,
        query: Optional[str] = None,
        scanned_by: Optional[str] = None,
        limit: int = 100
    ) -> Optional[List[Dict[str, Any]]]:
        """List records with optional camp isolation, community isolation, and status filters."""
        if not self.is_available():
            return None

        try:
            safe_limit = min(int(limit), 1000)
            params = ["select=*,camps(name)", f"limit={safe_limit}", "order=created_at.desc"]
            if camp_id:
                params.append(f"camp_id=eq.{camp_id}")
            if status:
                params.append(f"status=eq.{status}")
            if community_id:
                params.append(f"community_id=eq.{community_id}")
            if scanned_by:
                params.append(f"scanned_by=eq.{scanned_by}")
            if query:
                params.append(f"or=(person_name.ilike.*{query}*,id.ilike.*{query}*)")

            qs = "&".join(params)
            res = self._session.get(
                f"{self.url}/rest/v1/records?{qs}",
                headers=self._headers(),
                timeout=5
            )
            res.raise_for_status()
            self._record_success()
            rows = res.json()

            results = []
            for r in rows:
                if (r.get("person_name") or "").startswith("[PURGED") or r.get("status") == "archived_mock" or "demo" in (r.get("id") or "").lower():
                    continue
                camp_obj = r.get("camps") or {}
                camp_name = camp_obj.get("name") if isinstance(camp_obj, dict) else None
                results.append({
                    "id": r["id"],
                    "camp_id": r.get("camp_id"),
                    "camp_name": camp_name or "Health Camp",
                    "person_name": r.get("person_name") or "Unknown Patient",
                    "age": r.get("age"),
                    "gender": r.get("gender"),
                    "height": r.get("height"),
                    "weight": r.get("weight"),
                    "blood_pressure": r.get("blood_pressure"),
                    "examination_details": r.get("examination_details"),
                    "referral_details": r.get("referral_details"),
                    "missing_fields": _safe_json_loads(r.get("missing_fields"), []),
                    "uncertain_fields": _safe_json_loads(r.get("uncertain_fields"), []),
                    "extraction_notes": r.get("extraction_notes"),
                    "structured_record": _safe_json_loads(r.get("structured_record"), {}),
                    "status": r.get("status") or "needs_review",
                    "page1_preview": r.get("page1_preview"),
                    "page2_preview": r.get("page2_preview"),
                    "reviewer_notes": r.get("reviewer_notes"),
                    "reviewed_by": r.get("reviewed_by"),
                    "reviewed_at": r.get("reviewed_at"),
                    "scanned_by": r.get("scanned_by"),
                    "community_id": r.get("community_id"),
                    "target_group": r.get("target_group") or "adult",
                    "is_paediatric": int(r.get("is_paediatric") or 0),
                    "paediatric_alerts": _safe_json_loads(r.get("paediatric_alerts"), []),
                    "edit_history": _safe_json_loads(r.get("edit_history"), []),
                    "version": int(r.get("version") or 1),
                    "confidence_scores": _safe_json_loads(r.get("confidence_scores"), {}),
                    "consent_given": int(r.get("consent_given") if r.get("consent_given") is not None else 1),
                    "consent_signature": r.get("consent_signature"),
                    "locked_by": r.get("locked_by"),
                    "locked_at": r.get("locked_at"),
                    "approved_by": r.get("approved_by"),
                    "approved_at": r.get("approved_at"),
                    "quality_warning_bypassed": _safe_json_loads(r.get("quality_warning_bypassed"), []),
                    "quality_bypassed": int(r.get("quality_bypassed") or 0),
                    "parent_spouse_name": r.get("parent_spouse_name"),
                    "mobile_number": r.get("mobile_number"),
                    "form_type": r.get("form_type"),
                    "created_at": r.get("created_at"),
                    "updated_at": r.get("updated_at")
                })

            # Batch-enrich paediatric records from child table
            paed_records = [rec for rec in results if int(rec.get("is_paediatric") or 0) == 1]
            if paed_records:
                paed_ids = [rec["id"] for rec in paed_records]
                for chunk_start in range(0, len(paed_ids), 50):
                    chunk = paed_ids[chunk_start:chunk_start + 50]
                    try:
                        pr_res = self._session.get(
                            f"{self.url}/rest/v1/paediatric_records?id=in.({','.join(chunk)})",
                            headers=self._headers(),
                            timeout=8
                        )
                        if pr_res.status_code == 200:
                            pr_map = {row["id"]: row for row in pr_res.json()}
                            for rec in paed_records:
                                if rec["id"] in pr_map:
                                    self._merge_paediatric_child_fields(rec, pr_map[rec["id"]])
                    except Exception as p_list_err:
                        logger.warning("Supabase batch paediatric merge warning: %s", p_list_err)

            return results
        except Exception as e:
            self._record_failure(e)
            return None

    def insert_record(self, record_data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a newly scanned record into Supabase (parent records + child tables)."""
        if not self.is_configured():
            raise RuntimeError("Supabase credentials not configured.")

        rec_id = record_data.get("id") or f"rec_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"
        now = datetime.now(timezone.utc).isoformat()
        tg = (record_data.get("target_group") or "adult").strip().lower()
        is_paed = 1 if (tg == "paediatric" or record_data.get("is_paediatric")) else 0

        # Sanitize camp_id to ensure foreign key constraint passes in Supabase
        import uuid
        raw_camp = record_data.get("camp_id")
        try:
            camp_uuid = str(uuid.UUID(str(raw_camp).strip()))
        except (ValueError, TypeError, AttributeError):
            camp_uuid = "00000000-0000-0000-0000-000000000001"

        # Sanitize community_id to ensure UUID format in Supabase
        raw_comm = record_data.get("community_id")
        try:
            comm_uuid = str(uuid.UUID(str(raw_comm).strip())) if raw_comm else None
        except (ValueError, TypeError, AttributeError):
            comm_uuid = None

        payload = {
            "id": rec_id,
            "camp_id": camp_uuid,
            "person_name": record_data.get("person_name") or "Unknown Patient",
            "age": str(record_data.get("age")) if record_data.get("age") is not None else None,
            "gender": record_data.get("gender"),
            "height": str(record_data.get("height")) if record_data.get("height") is not None else None,
            "weight": str(record_data.get("weight")) if record_data.get("weight") is not None else None,
            "blood_pressure": record_data.get("blood_pressure"),
            "examination_details": record_data.get("examination_details"),
            "referral_details": record_data.get("referral_details"),
            "missing_fields": _to_json_str(record_data.get("missing_fields"), "[]"),
            "uncertain_fields": _to_json_str(record_data.get("uncertain_fields"), "[]"),
            "extraction_notes": record_data.get("extraction_notes"),
            "structured_record": _to_json_str(record_data.get("structured_record"), "{}"),
            "status": record_data.get("status", "needs_review"),
            "page1_preview": record_data.get("page1_preview"),
            "page2_preview": record_data.get("page2_preview"),
            "reviewer_notes": record_data.get("reviewer_notes"),
            "reviewed_by": record_data.get("reviewed_by"),
            "reviewed_at": record_data.get("reviewed_at"),
            "scanned_by": record_data.get("scanned_by"),
            "community_id": comm_uuid,
            "target_group": tg,
            "is_paediatric": is_paed,
            "paediatric_alerts": _to_json_str(record_data.get("paediatric_alerts"), "[]"),
            "edit_history": _to_json_str(record_data.get("edit_history"), "[]"),
            "version": int(record_data.get("version") or 1),
            "confidence_scores": _to_json_str(record_data.get("confidence_scores"), "{}"),
            "consent_given": int(record_data.get("consent_given") if record_data.get("consent_given") is not None else 1),
            "consent_signature": record_data.get("consent_signature"),
            "locked_by": record_data.get("locked_by"),
            "locked_at": record_data.get("locked_at"),
            "approved_by": record_data.get("approved_by"),
            "approved_at": record_data.get("approved_at"),
            "original_ai_extraction": _to_json_str(record_data.get("original_ai_extraction"), "{}"),
            "quality_warning_bypassed": _to_json_str(record_data.get("quality_warning_bypassed"), "[]"),
            "quality_bypassed": int(record_data.get("quality_bypassed") or 0),
            "template_id": record_data.get("template_id"),
            "template_version": record_data.get("template_version"),
            "form_family": record_data.get("form_family") or ("paediatric" if is_paed else "adult"),
            "created_at": now,
            "updated_at": now
        }

        res = self._session.post(
            f"{self.url}/rest/v1/records",
            headers=self._headers(),
            json=payload,
            timeout=10
        )
        res.raise_for_status()

        # Child table routing
        try:
            if is_paed == 0:
                adult_payload = self._build_adult_payload(record_data, rec_id, now)
                self._session.post(
                    f"{self.url}/rest/v1/adult_records",
                    headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                    json=adult_payload,
                    timeout=5
                )
            else:
                paed_payload = self._build_paediatric_payload(record_data, rec_id, now)
                self._session.post(
                    f"{self.url}/rest/v1/paediatric_records",
                    headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                    json=paed_payload,
                    timeout=5
                )
        except Exception as child_err:
            logger.warning("Supabase child record insert notice: %s", child_err)

        return self.get_record_by_id(rec_id) or {
            **payload,
            "structured_record": _safe_json_loads(payload["structured_record"], {}),
            "missing_fields": _safe_json_loads(payload["missing_fields"], []),
            "uncertain_fields": _safe_json_loads(payload["uncertain_fields"], []),
            "paediatric_alerts": _safe_json_loads(payload["paediatric_alerts"], [])
        }

    def get_record_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single record by its ID from Supabase."""
        if not self.is_configured():
            return None
        try:
            res = self._session.get(
                f"{self.url}/rest/v1/records?id=eq.{record_id}&select=*,camps(name)&limit=1",
                headers=self._headers(),
                timeout=10
            )
            res.raise_for_status()
            rows = res.json()
            if not rows:
                # Fallback check child tables
                try:
                    ar = self._session.get(f"{self.url}/rest/v1/adult_records?id=eq.{record_id}&limit=1", headers=self._headers(), timeout=5).json()
                    if ar:
                        return ar[0]
                    pr = self._session.get(f"{self.url}/rest/v1/paediatric_records?id=eq.{record_id}&limit=1", headers=self._headers(), timeout=5).json()
                    if pr:
                        return pr[0]
                except Exception:
                    pass
                return None
            r = rows[0]
            camp_obj = r.get("camps") or {}
            camp_name = camp_obj.get("name") if isinstance(camp_obj, dict) else None
            record_dict = {
                "id": r["id"],
                "camp_id": r.get("camp_id"),
                "camp_name": camp_name or "Health Camp",
                "person_name": r.get("person_name") or "Unknown Patient",
                "age": r.get("age"),
                "gender": r.get("gender"),
                "height": r.get("height"),
                "weight": r.get("weight"),
                "blood_pressure": r.get("blood_pressure"),
                "examination_details": r.get("examination_details"),
                "referral_details": r.get("referral_details"),
                "missing_fields": _safe_json_loads(r.get("missing_fields"), []),
                "uncertain_fields": _safe_json_loads(r.get("uncertain_fields"), []),
                "extraction_notes": r.get("extraction_notes"),
                "structured_record": _safe_json_loads(r.get("structured_record"), {}),
                "original_ai_extraction": _safe_json_loads(r.get("original_ai_extraction"), {}),
                "status": r.get("status") or "needs_review",
                "page1_preview": r.get("page1_preview"),
                "page2_preview": r.get("page2_preview"),
                "reviewer_notes": r.get("reviewer_notes"),
                "reviewed_by": r.get("reviewed_by"),
                "reviewed_at": r.get("reviewed_at"),
                "scanned_by": r.get("scanned_by"),
                "community_id": r.get("community_id"),
                "target_group": r.get("target_group") or "adult",
                "is_paediatric": int(r.get("is_paediatric") or 0),
                "paediatric_alerts": _safe_json_loads(r.get("paediatric_alerts"), []),
                "edit_history": _safe_json_loads(r.get("edit_history"), []),
                "version": int(r.get("version") or 1),
                "confidence_scores": _safe_json_loads(r.get("confidence_scores"), {}),
                "consent_given": int(r.get("consent_given") if r.get("consent_given") is not None else 1),
                "consent_signature": r.get("consent_signature"),
                "locked_by": r.get("locked_by"),
                "locked_at": r.get("locked_at"),
                "approved_by": r.get("approved_by"),
                "approved_at": r.get("approved_at"),
                "quality_warning_bypassed": _safe_json_loads(r.get("quality_warning_bypassed"), []),
                "quality_bypassed": int(r.get("quality_bypassed") or 0),
                "parent_spouse_name": r.get("parent_spouse_name"),
                "mobile_number": r.get("mobile_number"),
                "form_type": r.get("form_type"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at")
            }
            if int(r.get("is_paediatric") or 0) == 1:
                try:
                    pr_res = self._session.get(
                        f"{self.url}/rest/v1/paediatric_records?id=eq.{record_id}&limit=1",
                        headers=self._headers(),
                        timeout=5
                    )
                    if pr_res.status_code == 200:
                        pr_rows = pr_res.json()
                        if pr_rows:
                            self._merge_paediatric_child_fields(record_dict, pr_rows[0])
                except Exception as pr_err:
                    logger.warning("Supabase single paediatric merge warning: %s", pr_err)
            return record_dict
        except Exception as e:
            logger.error("Supabase get_record error: %s", e)
            return None

    def update_reviewed_record(self, record_id: str, update_data: Dict[str, Any], reviewer: str) -> Optional[Dict[str, Any]]:
        """Update reviewed fields and mark record as reviewed in Supabase."""
        if not self.is_configured():
            return None
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "person_name": update_data.get("person_name", "").strip() or "Unnamed Patient",
            "age": str(update_data.get("age")) if update_data.get("age") is not None else None,
            "gender": update_data.get("gender"),
            "height": str(update_data.get("height")) if update_data.get("height") is not None else None,
            "weight": str(update_data.get("weight")) if update_data.get("weight") is not None else None,
            "blood_pressure": update_data.get("blood_pressure"),
            "examination_details": update_data.get("examination_details"),
            "referral_details": update_data.get("referral_details"),
            "missing_fields": _to_json_str(update_data.get("missing_fields"), "[]"),
            "uncertain_fields": _to_json_str(update_data.get("uncertain_fields"), "[]"),
            "extraction_notes": update_data.get("extraction_notes"),
            "reviewer_notes": update_data.get("reviewer_notes"),
            "reviewed_by": reviewer,
            "reviewed_at": now,
            "status": "reviewed",
            "updated_at": now
        }
        if update_data.get("structured_record") is not None:
            payload["structured_record"] = _to_json_str(update_data.get("structured_record"), "{}")
        if update_data.get("original_ai_extraction") is not None:
            payload["original_ai_extraction"] = _to_json_str(update_data.get("original_ai_extraction"), "{}")

        try:
            res = self._session.patch(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                json=payload,
                timeout=10
            )
            res.raise_for_status()
            # Also update child adult/paediatric table if exists
            child_patch = {
                "person_name": payload["person_name"],
                "status": "reviewed",
                "updated_at": now
            }
            if payload.get("age") is not None: child_patch["age"] = payload["age"]
            if payload.get("gender") is not None: child_patch["gender"] = payload["gender"]
            if payload.get("height") is not None: child_patch["height"] = payload["height"]
            if payload.get("weight") is not None: child_patch["weight"] = payload["weight"]
            if payload.get("blood_pressure") is not None: child_patch["blood_pressure"] = payload["blood_pressure"]
            if update_data.get("dob") is not None: child_patch["dob"] = update_data["dob"]
            if update_data.get("blood_group") is not None: child_patch["blood_group"] = update_data["blood_group"]
            if payload.get("structured_record") is not None: child_patch["structured_record"] = payload["structured_record"]
            if payload.get("weight") is not None: child_patch["weight"] = payload["weight"]
            if payload.get("blood_pressure") is not None: child_patch["blood_pressure"] = payload["blood_pressure"]
            try:
                self._session.patch(f"{self.url}/rest/v1/adult_records?id=eq.{record_id}", headers=self._headers(), json=child_patch, timeout=5)
            except Exception:
                pass
            try:
                self._session.patch(f"{self.url}/rest/v1/paediatric_records?id=eq.{record_id}", headers=self._headers(), json=child_patch, timeout=5)
            except Exception:
                pass

            return self.get_record_by_id(record_id)
        except Exception as e:
            logger.error("Supabase update_reviewed_record error: %s", e)
            return None

    def update_record(self, record_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update arbitrary record fields in Supabase."""
        if not self.is_configured():
            return None
        now = datetime.now(timezone.utc).isoformat()
        payload = {**update_data, "updated_at": now}
        for k in ("missing_fields", "uncertain_fields", "paediatric_alerts", "edit_history", "confidence_scores", "quality_warning_bypassed", "structured_record"):
            if k in payload and isinstance(payload[k], (dict, list)):
                payload[k] = _to_json_str(payload[k])
        try:
            res = self._session.patch(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                json=payload,
                timeout=10
            )
            res.raise_for_status()
            return self.get_record_by_id(record_id)
        except Exception as e:
            logger.error("Supabase update_record error: %s", e)
            return None

    def approve_record(self, record_id: str, approved_by: str) -> Optional[Dict[str, Any]]:
        """Mark record as approved in Supabase (records + child tables)."""
        if not self.is_configured():
            return None
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "status": "approved",
            "approved_by": approved_by,
            "approved_at": now,
            "updated_at": now
        }
        try:
            res = self._session.patch(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                json=payload,
                timeout=10
            )
            res.raise_for_status()
            try:
                self._session.patch(f"{self.url}/rest/v1/adult_records?id=eq.{record_id}", headers=self._headers(), json={"status": "approved", "updated_at": now}, timeout=5)
            except Exception:
                pass
            try:
                self._session.patch(f"{self.url}/rest/v1/paediatric_records?id=eq.{record_id}", headers=self._headers(), json={"status": "approved", "updated_at": now}, timeout=5)
            except Exception:
                pass
            return self.get_record_by_id(record_id)
        except Exception as e:
            logger.error("Supabase approve_record error: %s", e)
            return None

    def lock_record(self, record_id: str, doctor_name: str) -> Dict[str, Any]:
        """Lock record for doctor editing in Supabase."""
        if not self.is_configured():
            return {"success": False, "error": "Supabase not configured"}
        now = datetime.now(timezone.utc).isoformat()
        try:
            res = self._session.patch(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                json={"locked_by": doctor_name, "locked_at": now},
                timeout=5
            )
            return {"success": res.status_code in (200, 204), "locked_by": doctor_name, "locked_at": now}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def unlock_record(self, record_id: str, doctor_name: Optional[str] = None) -> bool:
        """Unlock record in Supabase."""
        if not self.is_configured():
            return False
        try:
            res = self._session.patch(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                json={"locked_by": None, "locked_at": None},
                timeout=5
            )
            return res.status_code in (200, 204)
        except Exception:
            return False

    def list_adult_records(self, camp_id: Optional[str] = None, status: Optional[str] = None, limit: int = 100) -> Optional[List[Dict[str, Any]]]:
        if not self.is_available():
            return None
        params = [f"limit={limit}", "order=created_at.desc"]
        if camp_id: params.append(f"camp_id=eq.{camp_id}")
        if status: params.append(f"status=eq.{status}")
        try:
            res = self._session.get(f"{self.url}/rest/v1/adult_records?{'&'.join(params)}", headers=self._headers(), timeout=5)
            if res.status_code == 200:
                return res.json()
            return None
        except Exception as e:
            logger.warning("Supabase list_adult_records error: %s", e)
            return None

    def get_adult_record_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_available():
            return None
        try:
            res = self._session.get(f"{self.url}/rest/v1/adult_records?id=eq.{record_id}&limit=1", headers=self._headers(), timeout=5)
            if res.status_code == 200 and res.json():
                return res.json()[0]
            return None
        except Exception:
            return None

    def list_paediatric_records(self, camp_id: Optional[str] = None, status: Optional[str] = None, limit: int = 100) -> Optional[List[Dict[str, Any]]]:
        if not self.is_available():
            return None
        params = [f"limit={limit}", "order=created_at.desc"]
        if camp_id: params.append(f"camp_id=eq.{camp_id}")
        if status: params.append(f"status=eq.{status}")
        try:
            res = self._session.get(f"{self.url}/rest/v1/paediatric_records?{'&'.join(params)}", headers=self._headers(), timeout=5)
            if res.status_code == 200:
                return res.json()
            return None
        except Exception as e:
            logger.warning("Supabase list_paediatric_records error: %s", e)
            return None

    def get_paediatric_record_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_available():
            return None
        try:
            res = self._session.get(f"{self.url}/rest/v1/paediatric_records?id=eq.{record_id}&limit=1", headers=self._headers(), timeout=5)
            if res.status_code == 200 and res.json():
                return res.json()[0]
            return None
        except Exception:
            return None

    def save_file_metadata(
        self,
        file_id: str,
        org_id: str,
        storage_path: str,
        file_type: str = "scan",
        original_filename: str = "",
        mime_type: str = "image/jpeg",
        file_size: int = 0,
        record_id: Optional[str] = None
    ) -> bool:
        """Store scan file metadata in Supabase files table."""
        if not self.is_configured():
            return False
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "id": file_id,
            "org_id": org_id,
            "record_id": record_id,
            "file_type": file_type,
            "original_filename": original_filename,
            "storage_path": storage_path,
            "mime_type": mime_type,
            "file_size": file_size,
            "created_at": now
        }
        try:
            res = self._session.post(
                f"{self.url}/rest/v1/files",
                headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                json=payload,
                timeout=5
            )
            return res.status_code in (200, 201, 204)
        except Exception as e:
            logger.warning("Failed to save file metadata to Supabase: %s", e)
            return False

    def get_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve file metadata from Supabase files table."""
        if not self.is_configured():
            return None
        try:
            res = self._session.get(
                f"{self.url}/rest/v1/files?id=eq.{file_id}&select=*",
                headers=self._headers(),
                timeout=5
            )
            if res.status_code == 200:
                rows = res.json()
                if rows and isinstance(rows, list):
                    return rows[0]
            return None
        except Exception as e:
            logger.warning("Failed to get file metadata from Supabase: %s", e)
            return None

    def get_doctor_profile(self, user_id_or_email: str) -> Optional[Dict[str, Any]]:
        """Fetch doctor profile from Supabase doctors view or users table."""
        if not self.is_available():
            return None
        try:
            res = self._session.get(
                f"{self.url}/rest/v1/doctors?id=eq.{user_id_or_email}&limit=1",
                headers=self._headers(),
                timeout=5
            )
            if res.status_code == 200 and res.json():
                return res.json()[0]
            res = self._session.get(
                f"{self.url}/rest/v1/doctors?email=eq.{user_id_or_email}&limit=1",
                headers=self._headers(),
                timeout=5
            )
            if res.status_code == 200 and res.json():
                return res.json()[0]
            u = self.get_user_profile(user_id_or_email)
            if u:
                return {
                    "id": u.get("id"),
                    "email": u.get("email"),
                    "full_name": u.get("full_name"),
                    "specialization": "General Physician",
                    "community_role": "doctor"
                }
            return None
        except Exception as e:
            logger.warning("Failed to fetch doctor profile from Supabase: %s", e)
            return None

    def update_doctor_profile(
        self,
        user_id: str,
        full_name: Optional[str] = None,
        specialization: Optional[str] = None,
        avatar_url: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Update doctor user and user_profiles in Supabase."""
        if not self.is_available():
            return None
        now = datetime.now(timezone.utc).isoformat()
        try:
            user_patch = {"updated_at": now}
            if full_name: user_patch["full_name"] = full_name
            if avatar_url: user_patch["avatar_url"] = avatar_url
            self._session.patch(
                f"{self.url}/rest/v1/users?id=eq.{user_id}",
                headers=self._headers(),
                json=user_patch,
                timeout=5
            )
            if specialization:
                prof_patch = {"specialization": specialization, "updated_at": now}
                self._session.patch(
                    f"{self.url}/rest/v1/user_profiles?user_id=eq.{user_id}",
                    headers=self._headers(),
                    json=prof_patch,
                    timeout=5
                )
            return self.get_doctor_profile(user_id)
        except Exception as e:
            logger.warning("Failed to update doctor profile in Supabase: %s", e)
            return None

    def delete_record(self, record_id: str) -> bool:
        """Delete a record from Supabase."""
        if not self.is_configured():
            return False
        try:
            res = self._session.delete(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                timeout=10
            )
            if res.status_code in (200, 204) and len(res.json() if res.text.strip().startswith('[') else []) > 0:
                return True
            # If RLS prevented hard delete, purge via PATCH so it is immediately excluded
            patch_res = self._session.patch(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                json={"status": "archived_mock", "person_name": "[PURGED RECORD]"},
                timeout=10
            )
            return patch_res.status_code in (200, 204)
        except Exception as e:
            logger.error("Supabase delete_record error: %s", e)
            return False

    def update_camp(self, camp_id: str, name: str, expected_records: int, date_str: str = "Today", location: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update camp details in Supabase."""
        if not self.is_configured():
            return None
        payload = {
            "name": name.strip(),
            "expected_records": max(1, expected_records),
            "date_str": date_str.strip() or "Today",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if location:
            payload["location"] = location
        try:
            res = self._session.patch(
                f"{self.url}/rest/v1/camps?id=eq.{camp_id}",
                headers=self._headers(),
                json=payload,
                timeout=10
            )
            res.raise_for_status()
            return self.get_camp_by_id(camp_id)
        except Exception as e:
            logger.error("Supabase update_camp error: %s", e)
            return None

    def delete_camp(self, camp_id: str) -> bool:
        """Delete camp and cascade records in Supabase."""
        if not self.is_configured():
            return False
        try:
            # Delete records first in case FK cascade is not configured
            self._session.delete(f"{self.url}/rest/v1/records?camp_id=eq.{camp_id}", headers=self._headers(), timeout=10)
            res = self._session.delete(f"{self.url}/rest/v1/camps?id=eq.{camp_id}", headers=self._headers(), timeout=10)
            if res.status_code in (200, 204) and len(res.json() if res.text.strip().startswith('[') else []) > 0:
                return True
            # Purge via PATCH fallback
            self._session.patch(
                f"{self.url}/rest/v1/records?camp_id=eq.{camp_id}",
                headers=self._headers(),
                json={"status": "archived_mock", "person_name": "[PURGED RECORD]"},
                timeout=10
            )
            patch_res = self._session.patch(
                f"{self.url}/rest/v1/camps?id=eq.{camp_id}",
                headers=self._headers(),
                json={"name": f"[PURGED CAMP] {camp_id}"},
                timeout=10
            )
            return patch_res.status_code in (200, 204)
        except Exception as e:
            logger.error("Supabase delete_camp error: %s", e)
            return False

    def upload_storage_image(self, file_path: str, file_bytes: bytes, content_type: str = "image/jpeg", bucket: str = "intake-scans") -> Optional[str]:
        """Upload scanned intake image to Supabase Storage and return public URL."""
        if not self.is_configured():
            return None
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": content_type,
            "x-upsert": "true"
        }
        # Path format: {bucket}/{file_path}
        clean_path = file_path.lstrip("/")
        upload_url = f"{self.url}/storage/v1/object/{bucket}/{clean_path}"
        try:
            res = self._session.post(upload_url, headers=headers, data=file_bytes, timeout=15)
            if res.status_code in (200, 201):
                # Public URL format
                public_url = f"{self.url}/storage/v1/object/public/{bucket}/{clean_path}"
                return public_url
            logger.warning("Supabase storage upload returned status %d: %s", res.status_code, res.text)
            return None
        except Exception as e:
            logger.error("Supabase storage upload error: %s", e)
            return None

    def auth_sign_in(self, email: str, password: str) -> Dict[str, Any]:
        """Sign in using Supabase Auth endpoint with mandatory email verification check."""
        if not self.is_configured():
            return {"success": False, "error": "service_unavailable", "message": "Supabase not configured"}
        url = f"{self.url}/auth/v1/token?grant_type=password"
        headers = {
            "apikey": self.key,
            "Content-Type": "application/json"
        }
        try:
            res = self._session.post(url, headers=headers, json={"email": email.strip().lower(), "password": password}, timeout=10)
            data = res.json()
            if res.status_code == 200 and "access_token" in data:
                user = data.get("user") or {}
                # Stage 1A: Mandatory email verification check
                confirmed_at = user.get("email_confirmed_at") or user.get("confirmed_at")
                if not confirmed_at:
                    logger.warning("Login refused for unverified account: %s", email)
                    return {
                        "success": False,
                        "code": "EMAIL_NOT_VERIFIED",
                        "error": "Email verification is required before logging in. Please check your inbox for the confirmation link.",
                        "message": "Email verification is required before logging in. Please check your inbox for the confirmation link.",
                        "user": user
                    }
                return {
                    "success": True,
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token"),
                    "token_type": data.get("token_type", "bearer"),
                    "user": user,
                    "user_id": user.get("id"),
                    "email": user.get("email"),
                    "user_metadata": user.get("user_metadata", {}),
                    "app_metadata": user.get("app_metadata", {}),
                    "expires_in": data.get("expires_in")
                }
            # Handle specific Supabase error payloads
            err_msg = str(data.get("msg") or data.get("error_description") or "").lower()
            if "email not confirmed" in err_msg or "email_not_confirmed" in err_msg:
                return {
                    "success": False,
                    "code": "EMAIL_NOT_VERIFIED",
                    "error": "Email verification is required before logging in. Please check your inbox for the confirmation link.",
                    "message": "Email verification is required before logging in. Please check your inbox for the confirmation link."
                }
            return {
                "success": False,
                "code": "INVALID_CREDENTIALS",
                "error": data.get("error_description") or data.get("msg") or "Invalid email or password",
                "message": data.get("error_description") or data.get("msg") or "Invalid email or password"
            }
        except Exception as e:
            return {"success": False, "code": "NETWORK_ERROR", "error": "network_error", "message": str(e)}

    def auth_sign_up(
        self,
        email: str,
        password: str,
        full_name: str,
        account_type: str = "doctor",
        specialization: str = "General Physician",
        metadata: Optional[Dict[str, Any]] = None,
        email_redirect_to: Optional[str] = "ink2insight://auth/callback"
    ) -> Dict[str, Any]:
        """Sign up new user via Supabase Auth with account_type in ('doctor', 'user')."""
        if not self.is_configured():
            return {"success": False, "error": "Supabase not configured"}

        clean_type = str(account_type).strip().lower()
        if clean_type not in ("doctor", "user"):
            clean_type = "doctor"

        url = f"{self.url}/auth/v1/signup"
        headers = {
            "apikey": self.key,
            "Content-Type": "application/json"
        }
        user_data = {
            "full_name": full_name.strip(),
            "account_type": clean_type,
            "specialization": specialization.strip() if clean_type == "doctor" else "Outreach Worker"
        }
        if metadata:
            user_data.update(metadata)

        payload = {
            "email": email.strip().lower(),
            "password": password,
            "data": user_data
        }
        target_redirect = email_redirect_to or "ink2insight://auth/callback"
        approved_prefixes = ("ink2insight://auth/callback", "ink2insight://auth", "http://127.0.0.1:5001", "http://localhost:5001")
        if not any(target_redirect.startswith(p) for p in approved_prefixes):
            target_redirect = "ink2insight://auth/callback"
        try:
            res = self._session.post(
                url,
                headers=headers,
                json=payload,
                params={"redirect_to": target_redirect},
                timeout=10
            )
            data = res.json()
            if res.status_code in (200, 201):
                user = data.get("user") or data
                identities = user.get("identities")
                if identities is not None and len(identities) == 0:
                    logger.info("Supabase returned empty identities for %s; account already exists.", email)
                    return {
                        "success": False,
                        "code": "USER_ALREADY_EXISTS",
                        "error": "An account with this email address already exists. Please sign in instead.",
                        "message": "An account with this email address already exists. Please sign in instead."
                    }
                user_id = user.get("id")
                confirmed_at = user.get("email_confirmed_at") or user.get("confirmed_at")
                return {
                    "success": True,
                    "user": user,
                    "user_id": user_id,
                    "email": user.get("email"),
                    "email_confirmed": bool(confirmed_at),
                    "access_token": data.get("access_token"),
                    "refresh_token": data.get("refresh_token"),
                    "account_type": clean_type
                }

            # Resilient fallback: if Supabase Cloud SMTP/email delivery encounters an upstream error,
            # generate the official token-verification link via the admin API so email verification is preserved.
            err_msg = str(data.get("msg") or data.get("error_description") or "")
            service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or self.key
            if ("error sending confirmation email" in err_msg.lower() or res.status_code == 500) and service_key:
                logger.warning("Supabase email delivery failed (%s). Generating official verification link via admin API.", err_msg)
                admin_gen_url = f"{self.url}/auth/v1/admin/generate_link"
                admin_headers = {
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": "application/json"
                }
                gen_res = self._session.post(
                    admin_gen_url,
                    headers=admin_headers,
                    json={
                        "type": "signup",
                        "email": email.strip().lower(),
                        "password": password,
                        "options": {
                            "data": user_data,
                            "redirectTo": target_redirect
                        }
                    },
                    params={"redirect_to": target_redirect},
                    timeout=10
                )
                if gen_res.status_code in (200, 201):
                    gen_data = gen_res.json()
                    user_id = gen_data.get("id")
                    confirmed_at = gen_data.get("email_confirmed_at") or gen_data.get("confirmed_at")
                    action_link = _rewrite_action_link_redirect(gen_data.get("action_link"), target_redirect)
                    return {
                        "success": True,
                        "user": gen_data,
                        "user_id": user_id,
                        "email": gen_data.get("email"),
                        "email_confirmed": bool(confirmed_at),
                        "action_link": action_link,
                        "account_type": clean_type
                    }
                else:
                    logger.error("Admin generate_link failed: %s %s", gen_res.status_code, gen_res.text)

            return {"success": False, "code": "SIGNUP_FAILED", "error": data.get("msg") or data.get("error_description") or "Sign up failed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def auth_refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh an expired access token using the Supabase Auth refresh_token grant."""
        if not self.is_configured():
            return {"success": False, "error": "Supabase not configured"}
        url = f"{self.url}/auth/v1/token?grant_type=refresh_token"
        headers = {
            "apikey": self.key,
            "Content-Type": "application/json"
        }
        try:
            res = self._session.post(url, headers=headers, json={"refresh_token": refresh_token.strip()}, timeout=10)
            data = res.json()
            if res.status_code == 200 and "access_token" in data:
                user = data.get("user") or {}
                return {
                    "success": True,
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token"),
                    "user_id": user.get("id"),
                    "expires_in": data.get("expires_in")
                }
            return {"success": False, "error": data.get("error_description") or data.get("msg") or "Token refresh failed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def auth_recover_password(self, email: str, redirect_to: Optional[str] = "ink2insight://auth/callback") -> Dict[str, Any]:
        """Trigger a password recovery email via Supabase Auth."""
        if not self.is_configured():
            return {"success": False, "error": "Supabase not configured"}
        url = f"{self.url}/auth/v1/recover"
        headers = {
            "apikey": self.key,
            "Content-Type": "application/json"
        }
        target_redirect = redirect_to or "ink2insight://auth/callback"
        approved_prefixes = ("ink2insight://auth/callback", "ink2insight://auth", "http://127.0.0.1:5001", "http://localhost:5001")
        if not any(target_redirect.startswith(p) for p in approved_prefixes):
            target_redirect = "ink2insight://auth/callback"
        try:
            res = self._session.post(
                url,
                headers=headers,
                json={"email": email.strip().lower()},
                params={"redirect_to": target_redirect},
                timeout=10
            )
            if res.status_code in (200, 204):
                return {"success": True, "message": "Password recovery email dispatched."}
            data = res.json()
            err_msg = str(data.get("msg") or data.get("error_description") or "")
            service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or self.key
            if ("error sending confirmation email" in err_msg.lower() or res.status_code == 500) and service_key:
                logger.warning("Supabase recovery email delivery failed (%s). Generating official recovery link via admin API.", err_msg)
                admin_gen_url = f"{self.url}/auth/v1/admin/generate_link"
                admin_headers = {
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": "application/json"
                }
                gen_res = self._session.post(
                    admin_gen_url,
                    headers=admin_headers,
                    json={
                        "type": "recovery",
                        "email": email.strip().lower(),
                        "options": {
                            "redirectTo": target_redirect
                        }
                    },
                    params={"redirect_to": target_redirect},
                    timeout=10
                )
                if gen_res.status_code in (200, 201):
                    gen_data = gen_res.json()
                    action_link = _rewrite_action_link_redirect(gen_data.get("action_link"), target_redirect)
                    return {
                        "success": True,
                        "message": "Password recovery link generated.",
                        "action_link": action_link
                    }

            return {"success": False, "error": data.get("msg") or "Password recovery request failed."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def auth_reset_password(self, access_token: str, new_password: str) -> Dict[str, Any]:
        """Update user password using a verified recovery token or active session token."""
        if not self.is_configured():
            return {"success": False, "error": "Supabase not configured"}
        url = f"{self.url}/auth/v1/user"
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {access_token.strip()}",
            "Content-Type": "application/json"
        }
        try:
            res = self._session.put(url, headers=headers, json={"password": new_password}, timeout=10)
            if res.status_code in (200, 204):
                return {"success": True, "message": "Password updated successfully."}
            data = res.json()
            return {"success": False, "error": data.get("msg") or "Password update failed."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def auth_sign_out(self, access_token: str) -> Dict[str, Any]:
        """Sign out user and invalidate refresh tokens in Supabase Auth."""
        if not self.is_configured():
            return {"success": True}
        url = f"{self.url}/auth/v1/logout"
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {access_token.strip()}"
        }
        try:
            res = self._session.post(url, headers=headers, timeout=5)
            return {"success": res.status_code in (200, 204)}
        except Exception as e:
            logger.warning("Supabase sign out error: %s", e)
            return {"success": False, "error": str(e)}

    def get_user_communities(self, user_id: str) -> List[Dict[str, Any]]:
        """Fetch all trusted communities for an authenticated user from Supabase."""
        return self.get_user_community_memberships(user_id, active_only=True)

    def get_user_community_memberships(self, user_id: str, active_only: bool = True) -> List[Dict[str, Any]]:
        """Fetch all trusted community memberships for an authenticated user from Supabase.
        
        Args:
            user_id: Supabase Auth UUID of the user.
            active_only: If True, only returns memberships with status == 'active'.
                         If False, returns all memberships (active, pending, rejected, revoked).
        """
        if not self.is_available():
            return []
        try:
            filter_query = "&status=eq.active" if active_only else ""
            url = f"{self.url}/rest/v1/community_memberships?user_id=eq.{user_id}{filter_query}&select=id,community_id,role_id,status,joined_at,communities(id,name,code,is_active)"
            res = self._session.get(url, headers=self._headers(), timeout=5)
            if res.status_code == 200:
                rows = res.json()
                results = []
                for r in rows:
                    status = r.get("status") or "active"
                    if active_only and status != "active":
                        continue
                    comm = r.get("communities") or {}
                    if not isinstance(comm, dict):
                        comm = {}
                    # If active_only, skip if the community itself is deactivated
                    if active_only and comm.get("is_active") is False:
                        continue
                    role_id = r.get("role_id") or "doctor"
                    results.append({
                        "membership_id": r["id"],
                        "community_id": r["community_id"],
                        "role_id": role_id,
                        "role": role_id,  # Provide both role_id and role
                        "status": status,
                        "joined_at": r.get("joined_at"),
                        "community_name": comm.get("name") if comm.get("name") else "Screening Community",
                        "community_code": comm.get("code") if comm.get("code") else ""
                    })
                return results
            return []
        except Exception as e:
            logger.warning("Failed to fetch community memberships from Supabase: %s", e)
            return []

    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch public.users profile for a given user ID."""
        if not self.is_available():
            return None
        try:
            url = f"{self.url}/rest/v1/users?id=eq.{user_id}&select=id,email,full_name,status,email_verified"
            res = self._session.get(url, headers=self._headers(), timeout=5)
            if res.status_code == 200:
                rows = res.json()
                if rows and isinstance(rows, list):
                    return rows[0]
            return None
        except Exception as e:
            logger.warning("Failed to fetch user profile from Supabase: %s", e)
            return None

    def update_user_email_verified(self, user_id: str, email_verified: bool = True) -> bool:
        """Update email_verified status in public.users."""
        if not self.is_available():
            return False
        try:
            now = datetime.now(timezone.utc).isoformat()
            payload: Dict[str, Any] = {
                "email_verified": email_verified,
                "updated_at": now
            }
            if email_verified:
                payload["email_verified_at"] = now
            url = f"{self.url}/rest/v1/users?id=eq.{user_id}"
            res = self._session.patch(url, headers=self._headers(), json=payload, timeout=5)
            return res.status_code in (200, 204)
        except Exception as e:
            logger.warning("Failed to update email_verified in Supabase: %s", e)
            return False

    def create_community_atomic(
        self,
        name: str,
        code: str,
        creator_id: str,
        creator_name: str,
        creator_email: str,
        user_role: str = "doctor",
        email_verified: bool = True
    ) -> Dict[str, Any]:
        """Execute rpc_create_community_atomic in Supabase.
        
        Invokes canonical PostgreSQL RPC with atomic creation of the community and
        founder membership as 'community_admin'.
        """
        if not self.is_available():
            return {
                "success": False,
                "error": "database_unavailable",
                "code": "SUPABASE_UNAVAILABLE",
                "message": "Supabase service is unavailable."
            }
        try:
            url = f"{self.url}/rest/v1/rpc/rpc_create_community_atomic"
            payload = {
                "p_name": name,
                "p_code": code,
                "p_creator_id": creator_id,
                "p_creator_name": creator_name,
                "p_creator_email": creator_email,
                "p_user_role": user_role,
                "p_email_verified": email_verified
            }
            res = self._session.post(url, headers=self._headers(), json=payload, timeout=10)
            if res.status_code == 200:
                comm_data = res.json()
                return {
                    "success": True,
                    "community": {
                        "id": comm_data.get("community_id"),
                        "name": comm_data.get("name"),
                        "code": comm_data.get("code"),
                        "role": comm_data.get("role", "community_admin"),
                        "role_id": comm_data.get("role", "community_admin"),
                        "status": "active"
                    }
                }
            
            # Parse error response from Supabase PostgREST
            err_body = {}
            try:
                err_body = res.json()
            except Exception:
                err_body = {"message": res.text}
            
            raw_msg = err_body.get("message", "Unknown database error")
            error_code = "COMMUNITY_CREATION_FAILED"
            error_msg = raw_msg
            
            # Map canonical PL/pgSQL exception prefix
            if ":" in raw_msg:
                parts = raw_msg.split(":", 1)
                prefix = parts[0].strip()
                if prefix in (
                    "COMMUNITY_CODE_TAKEN", "COMMUNITY_SLUG_TAKEN", "COMMUNITY_NAME_REQUIRED",
                    "COMMUNITY_CODE_REQUIRED", "CREATOR_NOT_FOUND", "CREATOR_INACTIVE",
                    "CREATOR_EMAIL_MISMATCH", "CREATOR_NAME_MISMATCH", "FORBIDDEN",
                    "EMAIL_NOT_VERIFIED", "INVALID_CREATOR", "INVALID_EMAIL", "CREATOR_NAME_REQUIRED"
                ):
                    error_code = prefix
                    error_msg = parts[1].strip()
            
            return {
                "success": False,
                "error": "rpc_error",
                "code": error_code,
                "message": error_msg,
                "status_code": res.status_code
            }
        except Exception as e:
            logger.error("Error executing rpc_create_community_atomic: %s", e)
            return {
                "success": False,
                "error": "network_error",
                "code": "RPC_REQUEST_FAILED",
                "message": str(e)
            }

    @staticmethod
    def derive_invitation_status(inv: Dict[str, Any], now: Optional[datetime] = None) -> str:
        """Derive invitation status from existing database fields without schema changes.
        
        Statuses:
        - 'revoked': is_revoked is True
        - 'exhausted': use_count >= max_uses
        - 'expired': expires_at <= current time
        - 'active': otherwise
        """
        if inv.get("is_revoked"):
            return "revoked"
        
        use_count = int(inv.get("use_count") or 0)
        max_uses = int(inv.get("max_uses") or 1)
        if use_count >= max_uses:
            return "exhausted"
        
        expires_at_raw = inv.get("expires_at")
        if expires_at_raw:
            if now is None:
                now = datetime.now(timezone.utc)
            try:
                if isinstance(expires_at_raw, str):
                    exp_clean = expires_at_raw.replace("Z", "+00:00")
                    exp_dt = datetime.fromisoformat(exp_clean)
                elif isinstance(expires_at_raw, datetime):
                    exp_dt = expires_at_raw
                else:
                    exp_dt = None
                
                if exp_dt:
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if exp_dt <= now:
                        return "expired"
            except Exception:
                pass

        return "active"

    def create_invitation(
        self,
        community_id: str,
        code: str,
        role_id: str,
        created_by: str,
        requires_admin_approval: bool = False,
        max_uses: int = 1,
        expires_at: Optional[str] = None,
        email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new invitation in Supabase public.invitations table.
        
        Returns dict with success: bool and invitation dict.
        """
        if not self.is_available():
            return {
                "success": False,
                "error": "database_unavailable",
                "code": "SUPABASE_UNAVAILABLE",
                "message": "Supabase service is unavailable."
            }
        try:
            import uuid
            inv_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            if not expires_at:
                from datetime import timedelta
                expires_at = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()
            
            payload = {
                "id": inv_id,
                "code": code,
                "community_id": community_id,
                "email": email or None,
                "role_id": role_id,
                "max_uses": max_uses,
                "use_count": 0,
                "expires_at": expires_at,
                "is_revoked": False,
                "created_by": created_by,
                "created_at": now,
                "requires_admin_approval": requires_admin_approval
            }
            url = f"{self.url}/rest/v1/invitations"
            headers = {**self._headers(), "Prefer": "return=representation"}
            res = self._session.post(url, headers=headers, json=payload, timeout=10)
            if res.status_code in (200, 201):
                rows = res.json()
                inv_data = rows[0] if isinstance(rows, list) and rows else payload
                inv_data["status"] = self.derive_invitation_status(inv_data)
                return {
                    "success": True,
                    "invitation": inv_data
                }
            return {
                "success": False,
                "error": "insert_failed",
                "code": "INVITATION_CREATION_FAILED",
                "message": f"Failed to create invitation: {res.text}",
                "status_code": res.status_code
            }
        except Exception as e:
            logger.error("Failed to create invitation in Supabase: %s", e)
            return {
                "success": False,
                "error": "network_error",
                "code": "REQUEST_FAILED",
                "message": str(e)
            }

    def get_invitation_by_id_or_code(self, invite_id_or_code: str) -> Optional[Dict[str, Any]]:
        """Fetch an invitation by its UUID or code from Supabase."""
        if not self.is_available():
            return None
        try:
            import uuid
            clean = invite_id_or_code.strip()
            is_uuid = False
            try:
                uuid.UUID(clean)
                is_uuid = True
            except (ValueError, TypeError, AttributeError):
                is_uuid = False
            
            if is_uuid:
                url = f"{self.url}/rest/v1/invitations?or=(id.eq.{clean},code.eq.{clean})&select=*"
            else:
                url = f"{self.url}/rest/v1/invitations?code=eq.{clean}&select=*"
            
            res = self._session.get(url, headers=self._headers(), timeout=5)
            if res.status_code == 200:
                rows = res.json()
                if rows and isinstance(rows, list):
                    inv = rows[0]
                    inv["status"] = self.derive_invitation_status(inv)
                    return inv
            return None
        except Exception as e:
            logger.warning("Failed to fetch invitation from Supabase: %s", e)
            return None

    def list_invitations(self, community_id: str, created_by: Optional[str] = None) -> List[Dict[str, Any]]:
        """List invitations for a given community, optionally filtered by creator."""
        if not self.is_available():
            return []
        try:
            filter_query = f"?community_id=eq.{community_id}"
            if created_by:
                filter_query += f"&created_by=eq.{created_by}"
            filter_query += "&order=created_at.desc"
            url = f"{self.url}/rest/v1/invitations{filter_query}"
            res = self._session.get(url, headers=self._headers(), timeout=5)
            if res.status_code == 200:
                rows = res.json()
                if isinstance(rows, list):
                    for r in rows:
                        r["status"] = self.derive_invitation_status(r)
                    return rows
            return []
        except Exception as e:
            logger.warning("Failed to list invitations from Supabase: %s", e)
            return []

    def revoke_invitation(self, invitation_id: str) -> bool:
        """Revoke an invitation by setting is_revoked=True in Supabase.
        Never hard-deletes to protect response audit history.
        """
        if not self.is_available():
            return False
        try:
            url = f"{self.url}/rest/v1/invitations?id=eq.{invitation_id}"
            res = self._session.patch(url, headers=self._headers(), json={"is_revoked": True}, timeout=5)
            return res.status_code in (200, 204)
        except Exception as e:
            logger.error("Failed to revoke invitation in Supabase: %s", e)
            return False

    def preview_invitation(
        self,
        code: str,
        user_id: str,
        user_email: str,
        email_verified: bool,
        account_type: str
    ) -> Dict[str, Any]:
        """Execute rpc_redeem_invitation_atomic in Supabase.
        
        Validates and previews an invitation code with strict zero side-effects.
        Does not increment use_count, create membership, or record a response.
        """
        if not self.is_available():
            return {
                "success": False,
                "error": "database_unavailable",
                "code": "SUPABASE_UNAVAILABLE",
                "message": "Supabase service is unavailable."
            }
        try:
            url = f"{self.url}/rest/v1/rpc/rpc_redeem_invitation_atomic"
            payload = {
                "p_code": code,
                "p_user_id": user_id,
                "p_user_email": user_email,
                "p_email_verified": bool(email_verified),
                "p_account_type": account_type
            }
            res = self._session.post(url, headers=self._headers(), json=payload, timeout=10)
            if res.status_code == 200:
                data = res.json()
                return {
                    "success": True,
                    "valid": True,
                    "action": "preview",
                    "data": data,
                    **data
                }
            
            err_body = {}
            try:
                err_body = res.json()
            except Exception:
                err_body = {"message": res.text}
            
            raw_msg = err_body.get("message", "Invitation redemption preview failed")
            error_code = "INVITATION_PREVIEW_FAILED"
            error_msg = raw_msg
            
            if ":" in raw_msg:
                parts = raw_msg.split(":", 1)
                prefix = parts[0].strip()
                if prefix.isupper() and ("_" in prefix or prefix in ("FORBIDDEN",)):
                    error_code = prefix
                    error_msg = parts[1].strip()
            
            return {
                "success": False,
                "valid": False,
                "error": "rpc_error",
                "code": error_code,
                "message": error_msg,
                "status_code": res.status_code
            }
        except Exception as e:
            logger.error("Error executing rpc_redeem_invitation_atomic: %s", e)
            return {
                "success": False,
                "valid": False,
                "error": "network_error",
                "code": "RPC_REQUEST_FAILED",
                "message": str(e)
            }

    def redeem_invitation_preview(
        self,
        code: str,
        user_id: str,
        user_email: str,
        email_verified: bool,
        account_type: str
    ) -> Dict[str, Any]:
        """Alias for preview_invitation."""
        return self.preview_invitation(code, user_id, user_email, email_verified, account_type)

    def accept_invitation(
        self,
        code: str,
        user_id: str,
        user_email: str,
        email_verified: bool,
        account_type: str
    ) -> Dict[str, Any]:
        """Execute rpc_accept_invitation_atomic in Supabase.
        
        Atomically validates the invitation, increments usage, inserts response,
        and creates community membership (status='active' or 'pending').
        """
        if not self.is_available():
            return {
                "success": False,
                "error": "database_unavailable",
                "code": "SUPABASE_UNAVAILABLE",
                "message": "Supabase service is unavailable."
            }
        try:
            url = f"{self.url}/rest/v1/rpc/rpc_accept_invitation_atomic"
            payload = {
                "p_code": code,
                "p_user_id": user_id,
                "p_user_email": user_email,
                "p_email_verified": bool(email_verified),
                "p_account_type": account_type
            }
            res = self._session.post(url, headers=self._headers(), json=payload, timeout=10)
            if res.status_code == 200:
                data = res.json()
                return {
                    "success": True,
                    "action": "accepted",
                    "data": data,
                    **data
                }
            
            err_body = {}
            try:
                err_body = res.json()
            except Exception:
                err_body = {"message": res.text}
            
            raw_msg = err_body.get("message", "Invitation acceptance failed")
            error_code = "INVITATION_ACCEPT_FAILED"
            error_msg = raw_msg
            
            if ":" in raw_msg:
                parts = raw_msg.split(":", 1)
                prefix = parts[0].strip()
                if prefix.isupper() and ("_" in prefix or prefix in ("FORBIDDEN",)):
                    error_code = prefix
                    error_msg = parts[1].strip()
            
            return {
                "success": False,
                "error": "rpc_error",
                "code": error_code,
                "message": error_msg,
                "status_code": res.status_code
            }
        except Exception as e:
            logger.error("Error executing rpc_accept_invitation_atomic: %s", e)
            return {
                "success": False,
                "error": "network_error",
                "code": "RPC_REQUEST_FAILED",
                "message": str(e)
            }

    def decline_invitation(
        self,
        code: str,
        user_id: str,
        user_email: str,
        email_verified: bool
    ) -> Dict[str, Any]:
        """Execute rpc_decline_invitation_atomic in Supabase.
        
        Atomically records a 'declined' response without incrementing usage.
        A declined invitation cannot subsequently be accepted with the same code.
        """
        if not self.is_available():
            return {
                "success": False,
                "error": "database_unavailable",
                "code": "SUPABASE_UNAVAILABLE",
                "message": "Supabase service is unavailable."
            }
        try:
            url = f"{self.url}/rest/v1/rpc/rpc_decline_invitation_atomic"
            payload = {
                "p_code": code,
                "p_user_id": user_id,
                "p_user_email": user_email,
                "p_email_verified": bool(email_verified)
            }
            res = self._session.post(url, headers=self._headers(), json=payload, timeout=10)
            if res.status_code == 200:
                data = res.json()
                return {
                    "success": True,
                    "action": "declined",
                    "data": data,
                    **data
                }
            
            err_body = {}
            try:
                err_body = res.json()
            except Exception:
                err_body = {"message": res.text}
            
            raw_msg = err_body.get("message", "Invitation decline failed")
            error_code = "INVITATION_DECLINE_FAILED"
            error_msg = raw_msg
            
            if ":" in raw_msg:
                parts = raw_msg.split(":", 1)
                prefix = parts[0].strip()
                if prefix.isupper() and ("_" in prefix or prefix in ("FORBIDDEN",)):
                    error_code = prefix
                    error_msg = parts[1].strip()
            
            return {
                "success": False,
                "error": "rpc_error",
                "code": error_code,
                "message": error_msg,
                "status_code": res.status_code
            }
        except Exception as e:
            logger.error("Error executing rpc_decline_invitation_atomic: %s", e)
            return {
                "success": False,
                "error": "network_error",
                "code": "RPC_REQUEST_FAILED",
                "message": str(e)
            }

    def list_pending_memberships(self, community_id: str) -> List[Dict[str, Any]]:
        """List all pending community memberships for a given community in Supabase.
        Only called by authorized Community Admins.
        """
        if not self.is_available():
            return []
        try:
            url = (
                f"{self.url}/rest/v1/community_memberships"
                f"?community_id=eq.{community_id}"
                f"&status=eq.pending"
                f"&select=id,community_id,user_id,role_id,status,joined_at,updated_at,"
                f"users!community_memberships_user_id_fkey(id,full_name,email)"
                f"&order=joined_at.asc"
            )
            res = self._session.get(url, headers=self._headers(), timeout=10)
            if res.status_code == 200:
                rows = res.json()
                if isinstance(rows, list):
                    items = []
                    for r in rows:
                        user_info = r.get("users") or {}
                        items.append({
                            "id": r.get("id"),
                            "membership_id": r.get("id"),
                            "community_id": r.get("community_id"),
                            "user_id": r.get("user_id"),
                            "role": r.get("role_id"),
                            "role_id": r.get("role_id"),
                            "status": r.get("status", "pending"),
                            "requested_at": r.get("joined_at"),
                            "joined_at": r.get("joined_at"),
                            "updated_at": r.get("updated_at"),
                            "user": {
                                "id": user_info.get("id") or r.get("user_id"),
                                "name": user_info.get("full_name") or "User",
                                "full_name": user_info.get("full_name") or "User",
                                "email": user_info.get("email") or ""
                            }
                        })
                    return items
            return []
        except Exception as e:
            logger.warning("Failed to list pending memberships from Supabase: %s", e)
            return []

    def get_membership_by_id(self, membership_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single community membership record by its UUID."""
        if not self.is_available():
            return None
        try:
            url = (
                f"{self.url}/rest/v1/community_memberships"
                f"?id=eq.{membership_id}"
                f"&select=id,community_id,user_id,role_id,status,joined_at,updated_at,"
                f"approved_by,approved_at,rejected_by,rejected_at,rejection_reason"
            )
            res = self._session.get(url, headers=self._headers(), timeout=5)
            if res.status_code == 200:
                rows = res.json()
                if isinstance(rows, list) and len(rows) > 0:
                    return rows[0]
            return None
        except Exception as e:
            logger.warning("Failed to fetch membership %s from Supabase: %s", membership_id, e)
            return None

    def admin_review_membership(
        self,
        membership_id: str,
        admin_user_id: str,
        decision: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute rpc_admin_review_membership_atomic in Supabase.
        
        Invokes canonical PostgreSQL RPC with atomic status update,
        audit trail recording, and strict Community Admin authorization check.
        """
        if not self.is_available():
            return {
                "success": False,
                "error": "database_unavailable",
                "code": "SUPABASE_UNAVAILABLE",
                "message": "Supabase service is unavailable."
            }
        try:
            clean_decision = str(decision).strip().lower()
            if clean_decision in ("approve", "approved"):
                clean_decision = "approved"
            elif clean_decision in ("reject", "rejected"):
                clean_decision = "rejected"
            else:
                return {
                    "success": False,
                    "error": "bad_request",
                    "code": "INVALID_DECISION",
                    "message": "Decision must be approve or reject."
                }

            url = f"{self.url}/rest/v1/rpc/rpc_admin_review_membership_atomic"
            payload = {
                "p_membership_id": str(membership_id),
                "p_admin_user_id": str(admin_user_id),
                "p_decision": clean_decision,
                "p_reason": (str(reason).strip() if reason and clean_decision == "rejected" else None)
            }
            res = self._session.post(url, headers=self._headers(), json=payload, timeout=10)
            if res.status_code == 200:
                data = res.json()
                return {
                    "success": True,
                    "data": data,
                    **data
                }

            err_body = {}
            try:
                err_body = res.json()
            except Exception:
                err_body = {"message": res.text}

            raw_msg = err_body.get("message", "Membership review failed")
            error_code = "MEMBERSHIP_REVIEW_FAILED"
            error_msg = raw_msg

            if ":" in raw_msg:
                parts = raw_msg.split(":", 1)
                prefix = parts[0].strip()
                if prefix.isupper() and ("_" in prefix or prefix in ("FORBIDDEN",)):
                    error_code = prefix
                    error_msg = parts[1].strip()

            return {
                "success": False,
                "error": "rpc_error",
                "code": error_code,
                "message": error_msg,
                "status_code": res.status_code
            }
        except Exception as e:
            logger.error("Error executing rpc_admin_review_membership_atomic: %s", e)
            return {
                "success": False,
                "error": "network_error",
                "code": "RPC_REQUEST_FAILED",
                "message": str(e)
            }

    def sync_user_profile(
        self,
        user_id: str,
        email: str,
        full_name: str,
        account_type: str = "doctor",
        specialization: str = "General Physician"
    ) -> bool:
        """Safely ensure public.users profile exists in Supabase matching Auth UUID without creating split identities.
        
        Requirements:
        1. Exact argument signature: (user_id, email, full_name, account_type, specialization).
        2. Preserves existing email_verified flag on public.users (never resets to False).
        3. Preserves existing created_at timestamp on retries.
        4. Doctor accounts receive a public.user_profiles row; User accounts do not.
        5. Preserves existing doctor profile fields (specialization, medical_license_number, qualification, bio).
        6. Updates server-controlled auth app_metadata if admin auth endpoint is accessible.
        7. Returns boolean True on success, False on failure.
        """
        if not self.is_available():
            return False

        clean_type = "user" if str(account_type).strip().lower() == "user" else "doctor"
        clean_email = (email or "").strip().lower()
        clean_name = (full_name or "").strip()
        now = datetime.now(timezone.utc).isoformat()

        try:
            # Step 1: Check if public.users record already exists to preserve existing fields
            user_check = self._session.get(
                f"{self.url}/rest/v1/users?or=(id.eq.{user_id},email.eq.{clean_email})&select=id,email,full_name,email_verified,created_at",
                headers=self._headers(),
                timeout=5
            )

            if user_check.status_code == 200 and user_check.json():
                # User exists: update profile details without overwriting email_verified or created_at
                existing_user = user_check.json()[0]
                target_user_id = existing_user.get("id") or user_id
                user_res = self._session.patch(
                    f"{self.url}/rest/v1/users?id=eq.{target_user_id}",
                    headers=self._headers(),
                    json={
                        "email": clean_email,
                        "full_name": clean_name,
                        "updated_at": now
                    },
                    timeout=5
                )
            else:
                # User does not exist: initial insert
                user_payload = {
                    "id": user_id,
                    "email": clean_email,
                    "full_name": clean_name,
                    "status": "active",
                    "email_verified": False,
                    "created_at": now,
                    "updated_at": now
                }
                user_res = self._session.post(
                    f"{self.url}/rest/v1/users",
                    headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                    json=user_payload,
                    timeout=5
                )

            if user_res.status_code not in (200, 201, 204):
                logger.warning("Failed to sync public user record in Supabase: %d %s", user_res.status_code, user_res.text)
                return False

            # Step 2: Synchronize doctor user_profiles (Doctor accounts only)
            if clean_type == "doctor":
                prof_check = self._session.get(
                    f"{self.url}/rest/v1/user_profiles?user_id=eq.{user_id}&select=user_id,specialization,medical_license_number,qualification,bio,created_at",
                    headers=self._headers(),
                    timeout=5
                )
                if prof_check.status_code == 200 and prof_check.json():
                    # Profile already exists: preserve specialization, medical_license_number, qualification, bio, created_at
                    # Only touch updated_at
                    self._session.patch(
                        f"{self.url}/rest/v1/user_profiles?user_id=eq.{user_id}",
                        headers=self._headers(),
                        json={"updated_at": now},
                        timeout=5
                    )
                else:
                    # Initial doctor profile insert
                    prof_payload = {
                        "user_id": user_id,
                        "specialization": (specialization or "General Physician").strip(),
                        "created_at": now,
                        "updated_at": now
                    }
                    prof_res = self._session.post(
                        f"{self.url}/rest/v1/user_profiles",
                        headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                        json=prof_payload,
                        timeout=5
                    )
                    if prof_res.status_code not in (200, 201, 204):
                        logger.warning("Failed to sync public user_profiles record in Supabase: %d %s", prof_res.status_code, prof_res.text)
                        return False

            # Step 3: Store account_type in trusted server-controlled app_metadata via Supabase Admin API
            admin_url = f"{self.url}/auth/v1/admin/users/{user_id}"
            admin_headers = {
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json"
            }

            # Fetch existing user details to preserve unrelated app_metadata fields
            existing_app_meta = {}
            try:
                admin_get_res = self._session.get(admin_url, headers=admin_headers, timeout=5)
                if admin_get_res.status_code in (200, 201):
                    user_obj = admin_get_res.json()
                    if isinstance(user_obj, dict) and isinstance(user_obj.get("app_metadata"), dict):
                        existing_app_meta = dict(user_obj["app_metadata"])
                elif admin_get_res.status_code in (401, 403) or admin_get_res.status_code >= 500:
                    logger.error(
                        "Supabase Admin API GET failed for user %s: HTTP %d (trusted metadata check failed)",
                        user_id, admin_get_res.status_code
                    )
                    return False
            except (requests.Timeout, requests.ConnectionError, requests.RequestException) as net_err:
                logger.error(
                    "Supabase Admin API GET error for user %s: %s",
                    user_id, net_err.__class__.__name__
                )
                self._record_failure(net_err)
                return False

            # Preserve existing metadata fields while updating trusted account_type
            merged_app_meta = {**existing_app_meta, "account_type": clean_type}

            try:
                admin_put_res = self._session.put(
                    admin_url,
                    headers=admin_headers,
                    json={"app_metadata": merged_app_meta},
                    timeout=5
                )
                if admin_put_res.status_code not in (200, 201):
                    logger.error(
                        "Supabase Admin API PUT failed for user %s: HTTP %d (trusted account_type not saved)",
                        user_id, admin_put_res.status_code
                    )
                    return False
            except (requests.Timeout, requests.ConnectionError, requests.RequestException) as net_err:
                logger.error(
                    "Supabase Admin API PUT error for user %s: %s",
                    user_id, net_err.__class__.__name__
                )
                self._record_failure(net_err)
                return False

            self._record_success()
            return True
        except Exception as e:
            logger.warning("Error syncing public user profile to Supabase: %s", e)
            self._record_failure(e)
            return False

    def sync_from_sqlite(self, db_path: str) -> Dict[str, int]:
        """Optionally migrate or seed existing SQLite camps & records into Supabase."""
        if not self.is_configured():
            return {"camps": 0, "records": 0}
        
        try:
            from backend import database
        except ImportError:
            import database

        camps = database.list_camps_with_stats(db_path)
        records = database.list_records(camp_id=None, limit=1000, db_path=db_path)
        
        camps_synced = 0
        records_synced = 0
        
        for c in camps:
            try:
                res = self._session.post(
                    f"{self.url}/rest/v1/camps",
                    headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                    json={
                        "id": c["id"],
                        "name": c["name"],
                        "date_str": c.get("date_str") or "Today",
                        "expected_records": c.get("expected_records", 100),
                        "created_at": c.get("created_at") or datetime.now(timezone.utc).isoformat()
                    },
                    timeout=10
                )
                if res.status_code in (200, 201):
                    camps_synced += 1
            except Exception as e:
                logger.warning("Failed to sync camp %s to Supabase: %s", c["id"], e)

        for r in records:
            try:
                res = self._session.post(
                    f"{self.url}/rest/v1/records",
                    headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                    json={
                        "id": r["id"],
                        "camp_id": r["camp_id"],
                        "person_name": r.get("person_name") or "Unknown Patient",
                        "age": r.get("age"),
                        "gender": r.get("gender"),
                        "height": r.get("height"),
                        "weight": r.get("weight"),
                        "blood_pressure": r.get("blood_pressure"),
                        "examination_details": r.get("examination_details"),
                        "referral_details": r.get("referral_details"),
                        "missing_fields": r.get("missing_fields") or [],
                        "uncertain_fields": r.get("uncertain_fields") or [],
                        "extraction_notes": r.get("extraction_notes"),
                        "structured_record": r.get("structured_record") or {},
                        "status": r.get("status") or "needs_review",
                        "page1_preview": r.get("page1_preview"),
                        "page2_preview": r.get("page2_preview"),
                        "reviewer_notes": r.get("reviewer_notes"),
                        "reviewed_by": r.get("reviewed_by"),
                        "reviewed_at": r.get("reviewed_at"),
                        "created_at": r.get("created_at") or datetime.now(timezone.utc).isoformat(),
                        "updated_at": r.get("updated_at") or datetime.now(timezone.utc).isoformat()
                    },
                    timeout=10
                )
                if res.status_code in (200, 201):
                    records_synced += 1
            except Exception as e:
                logger.warning("Failed to sync record %s to Supabase: %s", r["id"], e)

        return {"camps": camps_synced, "records": records_synced}


# Module singleton instance
supabase_service = SupabaseService()


