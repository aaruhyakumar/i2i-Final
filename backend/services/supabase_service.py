"""I2 insights — Supabase Cloud Database Integration Service.

Connects to Supabase PostgreSQL using PostgREST endpoints.
Provides multi-device cloud synchronization for health camps and intake records.
Falls back seamlessly to local SQLite if Supabase credentials are not configured.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import requests

logger = logging.getLogger("doc_scanner.supabase")

class SupabaseService:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        self.url = (url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
        self._circuit_open_until = 0.0
        self._failure_count = 0
        self._last_error = ""

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

    def check_tables_exist(self) -> Dict[str, bool]:
        """Check if camps and records tables exist in Supabase schema."""
        if not self.is_available():
            return {"camps": False, "records": False}
        status = {"camps": False, "records": False}
        try:
            r = requests.get(f"{self.url}/rest/v1/camps?limit=1", headers=self._headers(), timeout=3)
            status["camps"] = (r.status_code == 200)
            if status["camps"]:
                self._record_success()
        except Exception as e:
            self._record_failure(e)
            return status
        try:
            r = requests.get(f"{self.url}/rest/v1/records?limit=1", headers=self._headers(), timeout=3)
            status["records"] = (r.status_code == 200)
            if status["records"]:
                self._record_success()
        except Exception as e:
            self._record_failure(e)
        return status

    def list_camps_with_stats(self) -> Optional[List[Dict[str, Any]]]:
        """Fetch all camps with computed intake stats from Supabase."""
        if not self.is_available():
            return None

        try:
            # Fetch camps
            r = requests.get(
                f"{self.url}/rest/v1/camps?select=*&order=created_at.desc",
                headers=self._headers(),
                timeout=5
            )
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
            rec_res = requests.get(
                f"{self.url}/rest/v1/records?select=id,camp_id,status,person_name",
                headers=self._headers(),
                timeout=5
            )
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
                    "expected_records": expected,
                    "total_scanned": scanned,
                    "to_check": st["to_check"],
                    "reviewed": st["reviewed"],
                    "progress_pct": min(100.0, pct),
                    "created_at": c.get("created_at")
                })
            return enriched
        except Exception as e:
            self._record_failure(e)
            return None

    def get_camp_by_id(self, camp_id: str) -> Optional[Dict[str, Any]]:
        """Fetch single camp by ID."""
        if not self.is_available():
            return None
        camps = self.list_camps_with_stats()
        if not camps:
            return None
        for c in camps:
            if c["id"] == camp_id:
                return c
        return None

    def create_camp(self, name: str, expected_records: int = 100, date_str: str = "Today") -> Dict[str, Any]:
        """Create a new screening camp in Supabase."""
        if not self.is_available():
            raise RuntimeError("Supabase credentials not configured or host unreachable.")

        camp_id = f"camp_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "id": camp_id,
            "name": name.strip(),
            "date_str": date_str.strip() or "Today",
            "expected_records": max(1, expected_records),
            "created_at": now
        }
        try:
            res = requests.post(
                f"{self.url}/rest/v1/camps",
                headers=self._headers(),
                json=payload,
                timeout=5
            )
            res.raise_for_status()
            self._record_success()
            return self.get_camp_by_id(camp_id) or payload
        except Exception as e:
            self._record_failure(e)
            raise

    def list_records(self, camp_id: Optional[str] = None, status: Optional[str] = None, limit: int = 100) -> Optional[List[Dict[str, Any]]]:
        """List records with optional camp isolation and status filters."""
        if not self.is_available():
            return None

        try:
            params = ["select=*,camps(name)", f"limit={limit}", "order=created_at.desc"]
            if camp_id:
                params.append(f"camp_id=eq.{camp_id}")
            if status:
                params.append(f"status=eq.{status}")

            qs = "&".join(params)
            res = requests.get(
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
                    "camp_id": r["camp_id"],
                    "camp_name": camp_name or "Health Camp",
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
                    "created_at": r.get("created_at"),
                    "updated_at": r.get("updated_at")
                })
            return results
        except Exception as e:
            self._record_failure(e)
            return None

    def insert_record(self, record_data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a newly scanned record into Supabase."""
        if not self.is_configured():
            raise RuntimeError("Supabase credentials not configured.")

        rec_id = record_data.get("id") or f"rec_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"
        now = datetime.now(timezone.utc).isoformat()

        payload = {
            "id": rec_id,
            "camp_id": record_data["camp_id"],
            "person_name": record_data.get("person_name") or "Unknown Patient",
            "age": record_data.get("age"),
            "gender": record_data.get("gender"),
            "height": record_data.get("height"),
            "weight": record_data.get("weight"),
            "blood_pressure": record_data.get("blood_pressure"),
            "examination_details": record_data.get("examination_details"),
            "referral_details": record_data.get("referral_details"),
            "missing_fields": record_data.get("missing_fields") or [],
            "uncertain_fields": record_data.get("uncertain_fields") or [],
            "extraction_notes": record_data.get("extraction_notes"),
            "structured_record": record_data.get("structured_record") or {},
            "status": record_data.get("status", "needs_review"),
            "page1_preview": record_data.get("page1_preview"),
            "page2_preview": record_data.get("page2_preview"),
            "reviewer_notes": record_data.get("reviewer_notes"),
            "reviewed_by": record_data.get("reviewed_by"),
            "reviewed_at": record_data.get("reviewed_at"),
            "created_at": now,
            "updated_at": now
        }

        res = requests.post(
            f"{self.url}/rest/v1/records",
            headers=self._headers(),
            json=payload,
            timeout=10
        )
        res.raise_for_status()
        return payload

    def get_record_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single record by its ID from Supabase."""
        if not self.is_configured():
            return None
        try:
            res = requests.get(
                f"{self.url}/rest/v1/records?id=eq.{record_id}&select=*,camps(name)&limit=1",
                headers=self._headers(),
                timeout=10
            )
            res.raise_for_status()
            rows = res.json()
            if not rows:
                return None
            r = rows[0]
            camp_obj = r.get("camps") or {}
            camp_name = camp_obj.get("name") if isinstance(camp_obj, dict) else None
            return {
                "id": r["id"],
                "camp_id": r["camp_id"],
                "camp_name": camp_name or "Health Camp",
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
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at")
            }
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
            "age": update_data.get("age"),
            "gender": update_data.get("gender"),
            "height": update_data.get("height"),
            "weight": update_data.get("weight"),
            "blood_pressure": update_data.get("blood_pressure"),
            "examination_details": update_data.get("examination_details"),
            "referral_details": update_data.get("referral_details"),
            "missing_fields": update_data.get("missing_fields") or [],
            "uncertain_fields": update_data.get("uncertain_fields") or [],
            "extraction_notes": update_data.get("extraction_notes"),
            "reviewer_notes": update_data.get("reviewer_notes"),
            "reviewed_by": reviewer,
            "reviewed_at": now,
            "status": "reviewed",
            "updated_at": now
        }
        if update_data.get("structured_record") is not None:
            payload["structured_record"] = update_data.get("structured_record")

        try:
            res = requests.patch(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                json=payload,
                timeout=10
            )
            res.raise_for_status()
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
        try:
            res = requests.patch(
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

    def delete_record(self, record_id: str) -> bool:
        """Delete a record from Supabase."""
        if not self.is_configured():
            return False
        try:
            res = requests.delete(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                timeout=10
            )
            if res.status_code in (200, 204) and len(res.json() if res.text.strip().startswith('[') else []) > 0:
                return True
            # If RLS prevented hard delete, purge via PATCH so it is immediately excluded
            patch_res = requests.patch(
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
            res = requests.patch(
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
            requests.delete(f"{self.url}/rest/v1/records?camp_id=eq.{camp_id}", headers=self._headers(), timeout=10)
            res = requests.delete(f"{self.url}/rest/v1/camps?id=eq.{camp_id}", headers=self._headers(), timeout=10)
            if res.status_code in (200, 204) and len(res.json() if res.text.strip().startswith('[') else []) > 0:
                return True
            # Purge via PATCH fallback
            requests.patch(
                f"{self.url}/rest/v1/records?camp_id=eq.{camp_id}",
                headers=self._headers(),
                json={"status": "archived_mock", "person_name": "[PURGED RECORD]"},
                timeout=10
            )
            patch_res = requests.patch(
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
            res = requests.post(upload_url, headers=headers, data=file_bytes, timeout=15)
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
            res = requests.post(url, headers=headers, json={"email": email.strip().lower(), "password": password}, timeout=10)
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
        metadata: Optional[Dict[str, Any]] = None
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
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=10)
            data = res.json()
            if res.status_code in (200, 201):
                user = data.get("user") or data
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
            res = requests.post(url, headers=headers, json={"refresh_token": refresh_token.strip()}, timeout=10)
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

    def auth_recover_password(self, email: str) -> Dict[str, Any]:
        """Trigger a password recovery email via Supabase Auth."""
        if not self.is_configured():
            return {"success": False, "error": "Supabase not configured"}
        url = f"{self.url}/auth/v1/recover"
        headers = {
            "apikey": self.key,
            "Content-Type": "application/json"
        }
        try:
            res = requests.post(url, headers=headers, json={"email": email.strip().lower()}, timeout=10)
            if res.status_code in (200, 204):
                return {"success": True, "message": "Password recovery email dispatched."}
            data = res.json()
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
            res = requests.put(url, headers=headers, json={"password": new_password}, timeout=10)
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
            res = requests.post(url, headers=headers, timeout=5)
            return {"success": res.status_code in (200, 204)}
        except Exception as e:
            logger.warning("Supabase sign out error: %s", e)
            return {"success": False, "error": str(e)}

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
            res = requests.get(url, headers=self._headers(), timeout=5)
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
            res = requests.get(url, headers=self._headers(), timeout=5)
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
            res = requests.patch(url, headers=self._headers(), json=payload, timeout=5)
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
            res = requests.post(url, headers=self._headers(), json=payload, timeout=10)
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
            res = requests.post(url, headers=headers, json=payload, timeout=10)
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
            
            res = requests.get(url, headers=self._headers(), timeout=5)
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
            res = requests.get(url, headers=self._headers(), timeout=5)
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
            res = requests.patch(url, headers=self._headers(), json={"is_revoked": True}, timeout=5)
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
            res = requests.post(url, headers=self._headers(), json=payload, timeout=10)
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
            res = requests.post(url, headers=self._headers(), json=payload, timeout=10)
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
            res = requests.post(url, headers=self._headers(), json=payload, timeout=10)
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
            res = requests.get(url, headers=self._headers(), timeout=10)
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
            res = requests.get(url, headers=self._headers(), timeout=5)
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
            res = requests.post(url, headers=self._headers(), json=payload, timeout=10)
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
            user_check = requests.get(
                f"{self.url}/rest/v1/users?id=eq.{user_id}&select=id,email,full_name,email_verified,created_at",
                headers=self._headers(),
                timeout=5
            )

            if user_check.status_code == 200 and user_check.json():
                # User exists: update profile details without overwriting email_verified or created_at
                user_res = requests.patch(
                    f"{self.url}/rest/v1/users?id=eq.{user_id}",
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
                user_res = requests.post(
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
                prof_check = requests.get(
                    f"{self.url}/rest/v1/user_profiles?user_id=eq.{user_id}&select=user_id,specialization,medical_license_number,qualification,bio,created_at",
                    headers=self._headers(),
                    timeout=5
                )
                if prof_check.status_code == 200 and prof_check.json():
                    # Profile already exists: preserve specialization, medical_license_number, qualification, bio, created_at
                    # Only touch updated_at
                    requests.patch(
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
                    prof_res = requests.post(
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
                admin_get_res = requests.get(admin_url, headers=admin_headers, timeout=5)
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
                admin_put_res = requests.put(
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
                res = requests.post(
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
                res = requests.post(
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


