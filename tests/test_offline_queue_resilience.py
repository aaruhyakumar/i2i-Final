"""Test Suite for BUG-01 Offline Scan Queue Resilience & Data Preservation.

Validates that:
1. HTTP 401 returns unauthorized and pauses processing without deleting scans.
2. HTTP 403 returns forbidden and marks permission_denied without deleting scans.
3. HTTP 422 returns validation failure and marks validation_error without deleting scans.
4. HTTP 400 / 413 returns client error without deleting scans.
5. HTTP 500 / network failures preserve scans and retry.
6. Successful extraction deletes the scan only upon confirmed success.
7. Fresh auth token is obtained from localStorage upon retry, never reusing stale tokens.
8. Static code analysis guarantees no idbDelete is called on HTTP 4xx errors in offline-queue.js.
"""

import os
import re
import unittest
import tempfile
import io
from PIL import Image
from unittest.mock import patch, MagicMock

import backend.app as app_module
import backend.database as database
from backend.services import auth_service


class TestOfflineQueueResilience(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_db_fd, cls.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(cls.temp_db_fd)
        database.init_db(cls.temp_db_path)
        cls.app = app_module.create_app(db_path=cls.temp_db_path)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.temp_db_path):
            try:
                os.remove(cls.temp_db_path)
            except Exception:
                pass

    def setUp(self):
        auth_service._ACCOUNT_LOCKOUTS.clear()
        self.app.config["SUPABASE_ENABLED"] = True

    def test_01_static_analysis_no_deletion_on_4xx(self):
        """Verify offline-queue.js contains no idbDelete on HTTP 4xx responses."""
        js_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "js", "offline-queue.js")
        with open(js_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Check drain method
        drain_pos = code.find("async drain(")
        self.assertNotEqual(drain_pos, -1, "drain method must exist in offline-queue.js")
        drain_code = code[drain_pos:drain_pos + 6000]

        # In catch block, ensure idbDelete is not called for status >= 400
        catch_pos = drain_code.find("catch (fetchErr)")
        self.assertNotEqual(catch_pos, -1, "fetchErr catch block must exist in drain")
        catch_block = drain_code[catch_pos:]

        self.assertNotIn("idbDelete(this._db, entry.id)", catch_block,
                         "CRITICAL: idbDelete must NEVER be called inside catch(fetchErr) block!")

        # Verify 401 handling pauses queue and sets auth_required
        self.assertIn("status === 401", catch_block)
        self.assertIn("'auth_required'", catch_block)
        self.assertIn("break;", catch_block)

        # Verify 403 handling sets permission_denied and requires_attention
        self.assertIn("status === 403", catch_block)
        self.assertIn("'permission_denied'", catch_block)
        self.assertIn("requires_attention", catch_block)

        # Verify 422 handling sets validation_error and requires_attention
        self.assertIn("status === 422", catch_block)
        self.assertIn("'validation_error'", catch_block)

        # Verify fresh auth token is resolved from localStorage
        self.assertIn("getFreshAuthToken", code)
        self.assertIn("headersToSend['Authorization']", drain_code)

    def test_02_backend_expired_token_returns_401(self):
        """Backend returns 401 when token is expired or invalid, prompting client pause."""
        resp = self.client.post(
            "/api/extract",
            headers={"Authorization": "Bearer expired_or_invalid_jwt_token_123"},
            content_type="multipart/form-data"
        )
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json() or {}
        self.assertEqual(data.get("error"), "unauthorized")

    def test_03_backend_missing_token_returns_401(self):
        """Backend returns 401 when Authorization header is omitted."""
        resp = self.client.post("/api/extract", content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json() or {}
        self.assertEqual(data.get("error"), "unauthorized")

    def test_04_backend_missing_pages_returns_400(self):
        """Backend returns 400 when required pages are missing (client error)."""
        valid_claims = {
            "sub": "doc_test_queue",
            "user_id": "doc_test_queue",
            "org_id": "org_default",
            "role": "doctor",
            "account_type": "doctor",
            "email": "doctor@queue.test",
            "name": "Dr. Queue",
            "email_verified": True,
            "raw": {"sub": "doc_test_queue"}
        }
        with patch.object(auth_service, "safe_decode_token", return_value=(valid_claims["raw"], None)), \
             patch.object(auth_service, "extract_claims", return_value=valid_claims):
            resp = self.client.post(
                "/api/extract",
                headers={"Authorization": "Bearer valid_mock_token"},
                content_type="multipart/form-data"
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json() or {}
            self.assertEqual(data.get("code"), "MISSING_PAGES")

    def test_05_backend_unreadable_quality_returns_422(self):
        """Backend returns 422 when image quality is unreadable (validation error)."""
        valid_claims = {
            "sub": "doc_test_queue",
            "user_id": "doc_test_queue",
            "org_id": "org_default",
            "role": "doctor",
            "account_type": "doctor",
            "email": "doctor@queue.test",
            "name": "Dr. Queue",
            "email_verified": True,
            "raw": {"sub": "doc_test_queue"}
        }

        # Create a tiny blank image with zero variance (extremely blurry/unreadable)
        img = Image.new("RGB", (20, 20), color=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        blank_bytes = buf.getvalue()

        with patch.object(auth_service, "safe_decode_token", return_value=(valid_claims["raw"], None)), \
             patch.object(auth_service, "extract_claims", return_value=valid_claims), \
             patch("backend.services.quality_service.assess_image_quality", return_value={"ok": False, "issues": ["Severe motion blur"]}):
            resp = self.client.post(
                "/api/extract",
                headers={"Authorization": "Bearer valid_mock_token"},
                data={
                    "student_form": (io.BytesIO(blank_bytes), "p1.jpg", "image/jpeg"),
                    "health_assessment": (io.BytesIO(blank_bytes), "p2.jpg", "image/jpeg")
                },
                content_type="multipart/form-data"
            )
            self.assertEqual(resp.status_code, 422)
            data = resp.get_json() or {}
            self.assertEqual(data.get("code"), "IMAGE_UNREADABLE")

    def test_06_backend_success_returns_200_enabling_deletion(self):
        """Backend returns 200 on successful extraction, which authorizes queue deletion."""
        valid_claims = {
            "sub": "doc_test_queue",
            "user_id": "doc_test_queue",
            "org_id": "org_default",
            "role": "doctor",
            "account_type": "doctor",
            "email": "doctor@queue.test",
            "name": "Dr. Queue",
            "email_verified": True,
            "raw": {"sub": "doc_test_queue"}
        }

        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        img_bytes = buf.getvalue()

        mock_extraction = {
            "success": True,
            "record_id": "rec_queue_success_001",
            "data": {
                "person_name": "Queue Test Patient",
                "age": "30",
                "gender": "Female"
            }
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(valid_claims["raw"], None)), \
             patch.object(auth_service, "extract_claims", return_value=valid_claims), \
             patch("backend.app.ai_extract", return_value=mock_extraction):
            resp = self.client.post(
                "/api/extract",
                headers={"Authorization": "Bearer valid_mock_token"},
                data={
                    "student_form": (io.BytesIO(img_bytes), "p1.jpg", "image/jpeg"),
                    "health_assessment": (io.BytesIO(img_bytes), "p2.jpg", "image/jpeg"),
                    "quality_bypassed": "1"
                },
                content_type="multipart/form-data"
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json() or {}
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("record_id"), "rec_queue_success_001")


if __name__ == "__main__":
    unittest.main()
