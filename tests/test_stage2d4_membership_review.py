"""I2I Phase 2D-4 - Community Admin Membership Review Test Suite

Tests all Phase 2D-4 requirements using Phase 2C canonical RPC functions:
1. Community Admin lists pending membership requests (HTTP 200, user details & timestamps returned).
2. Doctor member cannot list pending membership requests (HTTP 403 INSUFFICIENT_ROLE).
3. User / Field Worker cannot list pending membership requests (HTTP 403 INSUFFICIENT_ROLE).
4. Community Admin approves pending membership (HTTP 200, status='active', decision='approved').
5. Community Admin rejects pending membership (HTTP 200, status='rejected', decision='rejected').
6. Non-admin cannot approve or reject memberships (HTTP 403 INSUFFICIENT_ROLE).
7. Admin cannot review their own membership (HTTP 403 SELF_REVIEW_PROHIBITED).
8. Cross-community review denied (HTTP 403 COMMUNITY_ACCESS_DENIED).
9. Already reviewed membership cannot be reviewed again (HTTP 409 MEMBERSHIP_NOT_PENDING).
10. Rejection reason is preserved and stored (p_reason passed to atomic RPC).
11. Rejected and pending memberships do not grant protected clinical access (HTTP 403 FORBIDDEN).
12. Invalid and missing authentication rejected (HTTP 401 MISSING_TOKEN / INVALID_TOKEN).
"""

import sys
import os
import types
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Setup sys.path and Lightweight Shims
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
for p in [REPO_ROOT, BACKEND_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

for mod_name in ["bcrypt", "pydantic", "PIL", "xlsxwriter", "pypdf", "reportlab", "razorpay", "pillow_heif", "numpy"]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)
sys.modules["numpy"].ndarray = object

# 1. dotenv shim
def _load_dotenv(dotenv_path=None, **kwargs):
    p = dotenv_path or os.path.join(REPO_ROOT, "backend", ".env")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k not in os.environ:
                        os.environ[k] = v

if "dotenv" not in sys.modules:
    sys.modules["dotenv"] = types.ModuleType("dotenv")
sys.modules["dotenv"].load_dotenv = _load_dotenv
import dotenv
dotenv.load_dotenv()

# 2. crypto & validation shims
sys.modules["bcrypt"].hashpw = lambda p, s: b"$2b$12$fakehash"
sys.modules["bcrypt"].gensalt = lambda: b"salt"
sys.modules["bcrypt"].checkpw = lambda p, h: True

class BaseModel:
    def __init__(self, **kw):
        for k, v in kw.items(): setattr(self, k, v)
    def model_dump(self): return self.__dict__
    def dict(self): return self.__dict__
sys.modules["pydantic"].BaseModel = BaseModel
sys.modules["pydantic"].Field = lambda *a, **k: None
class ValidationError(Exception): pass
sys.modules["pydantic"].ValidationError = ValidationError

pil_img = types.ModuleType("PIL.Image")
pil_img.Image = object
pil_ops = types.ModuleType("PIL.ImageOps")
sys.modules["PIL"].Image = pil_img
sys.modules["PIL"].ImageOps = pil_ops
sys.modules["PIL"].Image = pil_img
sys.modules["PIL"].ImageOps = pil_ops

# 3. requests shim backed by urllib
if "requests" not in sys.modules:
    requests_mod = types.ModuleType("requests")
    import urllib.request
    import urllib.error
    import ssl

    class Response:
        def __init__(self, status_code, content, headers=None):
            self.status_code = status_code
            self.content = content
            self.headers = headers or {}
            self.text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
        def json(self):
            return json.loads(self.text)
        @property
        def ok(self):
            return 200 <= self.status_code < 300

    def _req(method, url, headers=None, json=None, data=None, params=None, timeout=10):
        headers = dict(headers or {})
        if params:
            from urllib.parse import urlencode
            url += ("&" if "?" in url else "?") + urlencode(params)
        body = None
        if json is not None:
            import json as json_lib
            body = json_lib.dumps(json).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif data is not None:
            if isinstance(data, str): body = data.encode("utf-8")
            else: body = data
        if "User-Agent" not in headers:
            headers["User-Agent"] = "supabase-py/2.0.0"
        
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return Response(r.status, r.read(), dict(r.headers))
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read(), dict(e.headers))
        except Exception as e:
            raise Exception(f"Network error: {e}")

    requests_mod.get = lambda url, **kw: _req("GET", url, **kw)
    requests_mod.post = lambda url, **kw: _req("POST", url, **kw)
    requests_mod.put = lambda url, **kw: _req("PUT", url, **kw)
    requests_mod.delete = lambda url, **kw: _req("DELETE", url, **kw)
    requests_mod.patch = lambda url, **kw: _req("PATCH", url, **kw)
    class RequestException(Exception): pass
    requests_exceptions = types.ModuleType("requests.exceptions")
    requests_exceptions.RequestException = RequestException
    requests_mod.exceptions = requests_exceptions
    sys.modules["requests"] = requests_mod
    sys.modules["requests.exceptions"] = requests_exceptions

# 4. jwt shim
if "jwt" not in sys.modules:
    import base64
    import hmac
    import hashlib
    import time
    jwt_mod = types.ModuleType("jwt")
    class InvalidTokenError(Exception): pass
    class ExpiredSignatureError(InvalidTokenError): pass
    class InvalidIssuerError(InvalidTokenError): pass
    class InvalidAudienceError(InvalidTokenError): pass
    jwt_mod.InvalidTokenError = InvalidTokenError
    jwt_mod.ExpiredSignatureError = ExpiredSignatureError
    jwt_mod.InvalidIssuerError = InvalidIssuerError
    jwt_mod.InvalidAudienceError = InvalidAudienceError

    def _b64url_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

    def _b64url_decode(s: str) -> bytes:
        rem = len(s) % 4
        if rem > 0: s += '=' * (4 - rem)
        return base64.urlsafe_b64decode(s.encode('ascii'))

    def jwt_encode(payload, key, algorithm="HS256", headers=None):
        hdr = {"typ": "JWT", "alg": algorithm}
        if headers: hdr.update(headers)
        p1 = _b64url_encode(json.dumps(hdr, separators=(',', ':')).encode('utf-8'))
        p2 = _b64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
        msg = f"{p1}.{p2}".encode('ascii')
        sig = hmac.new(key.encode('utf-8') if isinstance(key, str) else key, msg, hashlib.sha256).digest()
        p3 = _b64url_encode(sig)
        return f"{p1}.{p2}.{p3}"

    def jwt_decode(token, key, algorithms=None, issuer=None, options=None, **kwargs):
        parts = token.split('.')
        if len(parts) != 3: raise InvalidTokenError("Not enough segments")
        payload = json.loads(_b64url_decode(parts[1]).decode('utf-8'))
        if "exp" in payload and time.time() > payload["exp"]:
            raise ExpiredSignatureError("Signature has expired")
        return payload

    def get_unverified_header(token):
        parts = token.split('.')
        if len(parts) < 2:
            raise InvalidTokenError("Invalid token format")
        hdr_b = _b64url_decode(parts[0])
        return json.loads(hdr_b.decode('utf-8'))

    jwt_mod.encode = jwt_encode
    jwt_mod.decode = jwt_decode
    jwt_mod.get_unverified_header = get_unverified_header
    sys.modules["jwt"] = jwt_mod

if "flask_cors" not in sys.modules:
    fc = types.ModuleType("flask_cors")
    fc.CORS = lambda app, **kw: None
    sys.modules["flask_cors"] = fc

if "flask_limiter" not in sys.modules:
    fl = types.ModuleType("flask_limiter")
    class Limiter:
        def __init__(self, *a, **k): pass
        def limit(self, *a, **k):
            def decorator(f): return f
            return decorator
    fl.Limiter = Limiter
    flu = types.ModuleType("flask_limiter.util")
    flu.get_remote_address = lambda: "127.0.0.1"
    sys.modules["flask_limiter"] = fl
    sys.modules["flask_limiter.util"] = flu

# 5. Flask mock framework
fmod = types.ModuleType("flask")
class RequestProxy:
    def __init__(self):
        self.path = "/"
        self.method = "GET"
        self.headers = {}
        self.args = {}
        self._json = {}
    def get_json(self, silent=True): return self._json

req_singleton = RequestProxy()
g_singleton = types.SimpleNamespace()
fmod.request = req_singleton
fmod.g = g_singleton

class JsonResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
    def get_json(self): return self._data
    @property
    def json(self): return self._data

def jsonify(*args, **kwargs):
    data = args[0] if args else kwargs
    return JsonResponse(data)

fmod.jsonify = jsonify
fmod.Response = JsonResponse
fmod.send_from_directory = lambda *a, **k: JsonResponse({"status": "file"})
fmod.send_file = lambda *a, **k: JsonResponse({"status": "file"})

class TestResponse:
    def __init__(self, res):
        if isinstance(res, tuple):
            self.data_raw = res[0]
            self.status_code = res[1]
            self.headers = res[2] if len(res) > 2 else {}
        elif hasattr(res, "status_code"):
            self.data_raw = res
            self.status_code = res.status_code
            self.headers = getattr(res, "headers", {})
        else:
            self.data_raw = res
            self.status_code = 200
            self.headers = {}
    def get_json(self, silent=True):
        if hasattr(self.data_raw, "get_json"):
            return self.data_raw.get_json()
        if isinstance(self.data_raw, dict):
            return self.data_raw
        return self.data_raw
    @property
    def json(self): return self.get_json()

class CaseInsensitiveDict(dict):
    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default
    def __getitem__(self, key):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        raise KeyError(key)
    def __contains__(self, key):
        return any(k.lower() == key.lower() for k in self.keys())

class TestClient:
    def __init__(self, app):
        self.app = app
        self.environ_base = {}
    def open(self, path, method="GET", headers=None, json=None, query_string=None, **kw):
        parts = path.split("?", 1)
        req_singleton.path = parts[0]
        req_singleton.method = method.upper()
        req_headers = CaseInsensitiveDict(self.environ_base)
        if headers:
            for k, v in headers.items():
                req_headers[k] = v
        req_singleton.headers = req_headers

        args = dict(query_string or {})
        if len(parts) > 1:
            from urllib.parse import parse_qs
            for k, v in parse_qs(parts[1]).items():
                args[k] = v[0] if v else ""
        req_singleton.args = args
        req_singleton._json = json or {}
        for k in list(g_singleton.__dict__.keys()):
            delattr(g_singleton, k)

        for hook in self.app.before_requests:
            res = hook()
            if res is not None:
                return TestResponse(res)

        handler = self.app.routes.get((req_singleton.path, req_singleton.method))
        if not handler:
            sorted_routes = sorted(
                self.app.routes.items(),
                key=lambda item: (1 if "<path:" in item[0][0] else 0, -len(item[0][0].split("/")), -len(item[0][0]))
            )
            for (rule, m), fn in sorted_routes:
                if m == req_singleton.method and "<" in rule:
                    def _conv_sub(m_param):
                        param = m_param.group(1)
                        if ":" in param:
                            conv, param = param.split(":", 1)
                            if conv == "path":
                                return f"(?P<{param}>.+)"
                        return f"(?P<{param}>[^/]+)"
                    regex = re.sub(r"<([^>]+)>", _conv_sub, rule)
                    match = re.match(f"^{regex}$", req_singleton.path)
                    if match:
                        handler = lambda fn=fn, kw=match.groupdict(): fn(**kw)
                        break

        if not handler:
            return TestResponse(({"error": "not_found"}, 404))

        res = handler()
        test_res = TestResponse(res)
        for hook in self.app.after_requests:
            test_res = hook(test_res) or test_res
        return test_res

    def get(self, path, **kw): return self.open(path, method="GET", **kw)
    def post(self, path, **kw): return self.open(path, method="POST", **kw)
    def put(self, path, **kw): return self.open(path, method="PUT", **kw)
    def delete(self, path, **kw): return self.open(path, method="DELETE", **kw)

class FlaskApp:
    def __init__(self, name, **kwargs):
        self.name = name
        self.config = {}
        self.routes = {}
        self.before_requests = []
        self.after_requests = []
    def route(self, rule, methods=None, **options):
        methods = methods or ["GET"]
        def decorator(f):
            for m in methods: self.routes[(rule, m)] = f
            return f
        return decorator
    def before_request(self, f):
        self.before_requests.append(f)
        return f
    def after_request(self, f):
        self.after_requests.append(f)
        return f
    def errorhandler(self, code):
        def decorator(f): return f
        return decorator
    def test_client(self): return TestClient(self)

fmod.Flask = FlaskApp
sys.modules["flask"] = fmod

from backend.app import create_app
from backend import database
from backend.services.supabase_service import SupabaseService, supabase_service
from backend.services import auth_service


class TestStage2D4MembershipReview(unittest.TestCase):
    """Focused backend tests for Phase 2D-4 Community Admin Membership Review."""

    @classmethod
    def setUpClass(cls):
        cls.temp_db_fd, cls.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(cls.temp_db_fd)
        cls._orig_db_path = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = cls.temp_db_path
        database.init_db(cls.temp_db_path)
        cls.app = create_app(db_path=cls.temp_db_path)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls._orig_db_path:
            os.environ["DATABASE_PATH"] = cls._orig_db_path
        else:
            os.environ.pop("DATABASE_PATH", None)
        if os.path.exists(cls.temp_db_path):
            try:
                os.remove(cls.temp_db_path)
            except Exception:
                pass

    def setUp(self):
        auth_service._ACCOUNT_LOCKOUTS.clear()
        self.app.config["SUPABASE_ENABLED"] = True

    # -------------------------------------------------------------------------
    # Helper to construct valid mock tokens / claims
    # -------------------------------------------------------------------------
    def _make_claims(self, user_id, email, name, role="doctor", account_type="doctor", email_verified=True):
        raw = {
            "sub": user_id,
            "email": email,
            "user_metadata": {"full_name": name},
            "app_metadata": {"account_type": account_type, "role": role, "email_verified": email_verified},
            "email_confirmed_at": "2026-11-15T00:00:00Z" if email_verified else None
        }
        return {
            "sub": user_id,
            "user_id": user_id,
            "role": role,
            "account_type": account_type,
            "email": email,
            "name": name,
            "email_verified": email_verified,
            "app_metadata": {"account_type": account_type, "role": role, "email_verified": email_verified},
            "raw": raw
        }

    # -------------------------------------------------------------------------
    # Test 1: Admin lists pending requests
    # -------------------------------------------------------------------------
    def test_01_admin_lists_pending_requests(self):
        """Active Community Admin lists pending membership requests -> HTTP 200, user details & timestamps returned."""
        admin_id = "a0000000-0000-4000-8000-000000000001"
        admin_claims = self._make_claims(admin_id, "admin@clinic.org", "Admin Alice")
        auth_hdr = {"Authorization": "Bearer token.admin"}
        comm_id = "c0000000-0000-4000-8000-000000000001"

        admin_memberships = [{
            "community_id": comm_id,
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        mock_pending = [
            {
                "id": "mem-pending-01",
                "membership_id": "mem-pending-01",
                "community_id": comm_id,
                "user_id": "u0000000-0000-4000-8000-000000000010",
                "role": "field_worker",
                "role_id": "field_worker",
                "status": "pending",
                "requested_at": "2026-11-16T00:00:00Z",
                "joined_at": "2026-11-16T00:00:00Z",
                "user": {
                    "id": "u0000000-0000-4000-8000-000000000010",
                    "name": "Jane Worker",
                    "full_name": "Jane Worker",
                    "email": "jane@clinic.org"
                }
            }
        ]

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=admin_memberships), \
             patch.object(SupabaseService, "list_pending_memberships", return_value=mock_pending) as mock_list:

            res = self.client.get(f"/api/community/memberships/pending?community_id={comm_id}", headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("community_id"), comm_id)
            self.assertEqual(data.get("total"), 1)
            requests_list = data.get("pending_memberships") or data.get("requests")
            self.assertEqual(len(requests_list), 1)
            self.assertEqual(requests_list[0]["status"], "pending")
            self.assertEqual(requests_list[0]["user"]["email"], "jane@clinic.org")
            mock_list.assert_called_once_with(comm_id)

    # -------------------------------------------------------------------------
    # Test 2: Doctor member cannot list pending requests
    # -------------------------------------------------------------------------
    def test_02_doctor_member_cannot_list_pending_requests(self):
        """Doctor member who is not community_admin cannot list pending requests -> HTTP 403 INSUFFICIENT_ROLE."""
        doc_id = "d0000000-0000-4000-8000-000000000002"
        doc_claims = self._make_claims(doc_id, "doc@clinic.org", "Dr. Staff")
        auth_hdr = {"Authorization": "Bearer token.doc"}
        comm_id = "c0000000-0000-4000-8000-000000000001"

        doc_memberships = [{
            "community_id": comm_id,
            "role": "doctor",
            "role_id": "doctor",
            "status": "active"
        }]

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=doc_memberships):

            res = self.client.get(f"/api/community/memberships/pending?community_id={comm_id}", headers=auth_hdr)
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.get_json().get("code"), "INSUFFICIENT_ROLE")

    # -------------------------------------------------------------------------
    # Test 3: User cannot list pending requests
    # -------------------------------------------------------------------------
    def test_03_user_cannot_list_pending_requests(self):
        """Standard user / field worker cannot list pending requests -> HTTP 403 INSUFFICIENT_ROLE."""
        user_id = "u0000000-0000-4000-8000-000000000003"
        user_claims = self._make_claims(user_id, "worker@clinic.org", "Field Worker", role="user", account_type="user")
        auth_hdr = {"Authorization": "Bearer token.user"}
        comm_id = "c0000000-0000-4000-8000-000000000001"

        user_memberships = [{
            "community_id": comm_id,
            "role": "field_worker",
            "role_id": "field_worker",
            "status": "active"
        }]

        with patch.object(auth_service, "safe_decode_token", return_value=(user_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=user_memberships):

            res = self.client.get(f"/api/community/memberships/pending?community_id={comm_id}", headers=auth_hdr)
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.get_json().get("code"), "INSUFFICIENT_ROLE")

    # -------------------------------------------------------------------------
    # Test 4: Admin approves pending membership
    # -------------------------------------------------------------------------
    def test_04_admin_approves_pending_membership(self):
        """Active Community Admin approves pending membership -> HTTP 200, status='active', decision='approved'."""
        admin_id = "a0000000-0000-4000-8000-000000000001"
        admin_claims = self._make_claims(admin_id, "admin@clinic.org", "Admin Alice")
        auth_hdr = {"Authorization": "Bearer token.admin"}
        comm_id = "c0000000-0000-4000-8000-000000000001"
        target_mem_id = "mem-pending-04"

        admin_memberships = [{
            "community_id": comm_id,
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        target_mem = {
            "id": target_mem_id,
            "community_id": comm_id,
            "user_id": "u0000000-0000-4000-8000-000000000020",
            "role_id": "doctor",
            "status": "pending"
        }

        mock_rpc_res = {
            "success": True,
            "membership_id": target_mem_id,
            "status": "active",
            "community_id": comm_id,
            "decision": "approved"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=admin_memberships), \
             patch.object(SupabaseService, "get_membership_by_id", return_value=target_mem), \
             patch.object(SupabaseService, "admin_review_membership", return_value={"success": True, "data": mock_rpc_res}) as mock_rev:

            res = self.client.post(
                f"/api/community/memberships/{target_mem_id}/review",
                json={"decision": "approve"},
                headers=auth_hdr
            )
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("status"), "active")
            self.assertEqual(data.get("decision"), "approved")
            mock_rev.assert_called_once_with(
                membership_id=target_mem_id,
                admin_user_id=admin_id,
                decision="approved",
                reason=None
            )

    # -------------------------------------------------------------------------
    # Test 5: Admin rejects pending membership
    # -------------------------------------------------------------------------
    def test_05_admin_rejects_pending_membership(self):
        """Active Community Admin rejects pending membership -> HTTP 200, status='rejected', decision='rejected'."""
        admin_id = "a0000000-0000-4000-8000-000000000001"
        admin_claims = self._make_claims(admin_id, "admin@clinic.org", "Admin Alice")
        auth_hdr = {"Authorization": "Bearer token.admin"}
        comm_id = "c0000000-0000-4000-8000-000000000001"
        target_mem_id = "mem-pending-05"
        reject_reason = "Incomplete medical registration documentation."

        admin_memberships = [{
            "community_id": comm_id,
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        target_mem = {
            "id": target_mem_id,
            "community_id": comm_id,
            "user_id": "u0000000-0000-4000-8000-000000000021",
            "role_id": "doctor",
            "status": "pending"
        }

        mock_rpc_res = {
            "success": True,
            "membership_id": target_mem_id,
            "status": "rejected",
            "community_id": comm_id,
            "decision": "rejected"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=admin_memberships), \
             patch.object(SupabaseService, "get_membership_by_id", return_value=target_mem), \
             patch.object(SupabaseService, "admin_review_membership", return_value={"success": True, "data": mock_rpc_res}) as mock_rev:

            res = self.client.post(
                f"/api/community/memberships/{target_mem_id}/reject",
                json={"reason": reject_reason},
                headers=auth_hdr
            )
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("status"), "rejected")
            self.assertEqual(data.get("decision"), "rejected")
            mock_rev.assert_called_once_with(
                membership_id=target_mem_id,
                admin_user_id=admin_id,
                decision="rejected",
                reason=reject_reason
            )

    # -------------------------------------------------------------------------
    # Test 6: Non-admin cannot approve or reject
    # -------------------------------------------------------------------------
    def test_06_non_admin_cannot_approve_or_reject(self):
        """Non-admin members attempting to review memberships are rejected -> HTTP 403 INSUFFICIENT_ROLE."""
        doc_id = "d0000000-0000-4000-8000-000000000002"
        doc_claims = self._make_claims(doc_id, "doc@clinic.org", "Dr. Staff")
        auth_hdr = {"Authorization": "Bearer token.doc"}
        comm_id = "c0000000-0000-4000-8000-000000000001"
        target_mem_id = "mem-pending-06"

        doc_memberships = [{
            "community_id": comm_id,
            "role": "doctor",
            "role_id": "doctor",
            "status": "active"
        }]

        target_mem = {
            "id": target_mem_id,
            "community_id": comm_id,
            "user_id": "u0000000-0000-4000-8000-000000000022",
            "role_id": "doctor",
            "status": "pending"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=doc_memberships), \
             patch.object(SupabaseService, "get_membership_by_id", return_value=target_mem), \
             patch.object(SupabaseService, "admin_review_membership") as mock_rev:

            res = self.client.post(
                f"/api/community/memberships/{target_mem_id}/approve",
                headers=auth_hdr
            )
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.get_json().get("code"), "INSUFFICIENT_ROLE")
            mock_rev.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 7: Admin cannot review their own membership
    # -------------------------------------------------------------------------
    def test_07_admin_cannot_review_own_membership(self):
        """Admins attempting to review their own membership are rejected -> HTTP 403 SELF_REVIEW_PROHIBITED."""
        admin_id = "a0000000-0000-4000-8000-000000000001"
        admin_claims = self._make_claims(admin_id, "admin@clinic.org", "Admin Alice")
        auth_hdr = {"Authorization": "Bearer token.admin"}
        comm_id = "c0000000-0000-4000-8000-000000000001"
        target_mem_id = "mem-pending-self"

        admin_memberships = [{
            "community_id": comm_id,
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        # Target membership belongs to the admin herself
        target_mem = {
            "id": target_mem_id,
            "community_id": comm_id,
            "user_id": admin_id,
            "role_id": "doctor",
            "status": "pending"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=admin_memberships), \
             patch.object(SupabaseService, "get_membership_by_id", return_value=target_mem), \
             patch.object(SupabaseService, "admin_review_membership") as mock_rev:

            res = self.client.post(
                f"/api/community/memberships/{target_mem_id}/approve",
                headers=auth_hdr
            )
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.get_json().get("code"), "SELF_REVIEW_PROHIBITED")
            mock_rev.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 8: Cross-community review denied
    # -------------------------------------------------------------------------
    def test_08_cross_community_review_denied(self):
        """Admin of Community A attempting to review membership in Community B is denied -> HTTP 403."""
        admin_id = "a0000000-0000-4000-8000-000000000001"
        admin_claims = self._make_claims(admin_id, "admin@clinic.org", "Admin Alice")
        auth_hdr = {"Authorization": "Bearer token.admin"}
        comm_a = "c0000000-0000-4000-8000-000000000001"
        comm_b = "c0000000-0000-4000-8000-000000000002"
        target_mem_id = "mem-pending-comm-b"

        # Admin belongs ONLY to Community A
        admin_memberships = [{
            "community_id": comm_a,
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        # Target membership belongs to Community B
        target_mem = {
            "id": target_mem_id,
            "community_id": comm_b,
            "user_id": "u0000000-0000-4000-8000-000000000023",
            "role_id": "doctor",
            "status": "pending"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=admin_memberships), \
             patch.object(SupabaseService, "get_membership_by_id", return_value=target_mem), \
             patch.object(SupabaseService, "admin_review_membership") as mock_rev:

            res = self.client.post(
                f"/api/community/memberships/{target_mem_id}/approve",
                headers=auth_hdr
            )
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.get_json().get("code"), "COMMUNITY_ACCESS_DENIED")
            mock_rev.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 9: Already reviewed membership cannot be reviewed again
    # -------------------------------------------------------------------------
    def test_09_already_reviewed_membership_cannot_be_reviewed_again(self):
        """Membership that is already 'active' or 'rejected' cannot be re-reviewed -> HTTP 409 MEMBERSHIP_NOT_PENDING."""
        admin_id = "a0000000-0000-4000-8000-000000000001"
        admin_claims = self._make_claims(admin_id, "admin@clinic.org", "Admin Alice")
        auth_hdr = {"Authorization": "Bearer token.admin"}
        comm_id = "c0000000-0000-4000-8000-000000000001"
        target_mem_id = "mem-already-active"

        admin_memberships = [{
            "community_id": comm_id,
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        # Membership is already active
        target_mem = {
            "id": target_mem_id,
            "community_id": comm_id,
            "user_id": "u0000000-0000-4000-8000-000000000024",
            "role_id": "doctor",
            "status": "active"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=admin_memberships), \
             patch.object(SupabaseService, "get_membership_by_id", return_value=target_mem), \
             patch.object(SupabaseService, "admin_review_membership") as mock_rev:

            res = self.client.post(
                f"/api/community/memberships/{target_mem_id}/approve",
                headers=auth_hdr
            )
            self.assertEqual(res.status_code, 409)
            self.assertEqual(res.get_json().get("code"), "MEMBERSHIP_NOT_PENDING")
            mock_rev.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 10: Rejection reason is preserved
    # -------------------------------------------------------------------------
    def test_10_rejection_reason_is_preserved(self):
        """Rejection reason provided by Admin is passed to RPC and returned in response payload."""
        admin_id = "a0000000-0000-4000-8000-000000000001"
        admin_claims = self._make_claims(admin_id, "admin@clinic.org", "Admin Alice")
        auth_hdr = {"Authorization": "Bearer token.admin"}
        comm_id = "c0000000-0000-4000-8000-000000000001"
        target_mem_id = "mem-pending-reason"
        detailed_reason = "Medical registration credentials not verifiable with state council."

        admin_memberships = [{
            "community_id": comm_id,
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        target_mem = {
            "id": target_mem_id,
            "community_id": comm_id,
            "user_id": "u0000000-0000-4000-8000-000000000025",
            "role_id": "doctor",
            "status": "pending"
        }

        mock_rpc_res = {
            "success": True,
            "membership_id": target_mem_id,
            "status": "rejected",
            "community_id": comm_id,
            "decision": "rejected"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=admin_memberships), \
             patch.object(SupabaseService, "get_membership_by_id", return_value=target_mem), \
             patch.object(SupabaseService, "admin_review_membership", return_value={"success": True, "data": mock_rpc_res}) as mock_rev:

            res = self.client.post(
                f"/api/community/memberships/{target_mem_id}/reject",
                json={"reason": detailed_reason},
                headers=auth_hdr
            )
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data.get("reason"), detailed_reason)
            mock_rev.assert_called_once_with(
                membership_id=target_mem_id,
                admin_user_id=admin_id,
                decision="rejected",
                reason=detailed_reason
            )

    # -------------------------------------------------------------------------
    # Test 11: Rejected and pending memberships do not grant protected access
    # -------------------------------------------------------------------------
    def test_11_rejected_and_pending_memberships_do_not_grant_protected_access(self):
        """Users with pending or rejected membership status are denied access to clinical records -> HTTP 403."""
        user_id = "u0000000-0000-4000-8000-000000000026"
        claims = self._make_claims(user_id, "pending.doc@clinic.org", "Dr. Pending")
        auth_hdr = {"Authorization": "Bearer token.pending"}
        comm_id = "c0000000-0000-4000-8000-000000000001"

        # User's membership is pending (not active!)
        pending_memberships = [{
            "community_id": comm_id,
            "role": "doctor",
            "role_id": "doctor",
            "status": "pending"
        }]

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=pending_memberships):

            # Attempt to access clinical records in comm_1
            res_rec = self.client.get(f"/api/records?camp_id=camp-01&community_id={comm_id}", headers=auth_hdr)
            self.assertEqual(res_rec.status_code, 403)

        # User's membership is rejected
        rejected_memberships = [{
            "community_id": comm_id,
            "role": "doctor",
            "role_id": "doctor",
            "status": "rejected"
        }]

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=rejected_memberships):

            res_rec2 = self.client.get(f"/api/records?camp_id=camp-01&community_id={comm_id}", headers=auth_hdr)
            self.assertEqual(res_rec2.status_code, 403)

    # -------------------------------------------------------------------------
    # Test 12: Invalid and missing authentication rejected
    # -------------------------------------------------------------------------
    def test_12_invalid_and_missing_authentication_rejected(self):
        """Missing or malformed authorization headers return HTTP 401."""
        # A. Missing token on pending listing
        res_no_tok = self.client.get("/api/community/memberships/pending?community_id=c1")
        self.assertEqual(res_no_tok.status_code, 401)
        self.assertEqual(res_no_tok.get_json().get("code"), "MISSING_TOKEN")

        # B. Missing token on review
        res_rev_no_tok = self.client.post("/api/community/memberships/mem-123/approve")
        self.assertEqual(res_rev_no_tok.status_code, 401)
        self.assertEqual(res_rev_no_tok.get_json().get("code"), "MISSING_TOKEN")

        # C. Invalid token on pending listing
        with patch.object(auth_service, "safe_decode_token", return_value=(None, "invalid_token")):
            res_bad_tok = self.client.get(
                "/api/community/memberships/pending?community_id=c1",
                headers={"Authorization": "Bearer bad.token"}
            )
            self.assertEqual(res_bad_tok.status_code, 401)
            self.assertEqual(res_bad_tok.get_json().get("code"), "INVALID_TOKEN")


if __name__ == "__main__":
    unittest.main()
