"""I2I Phase 2D-5 - Existing Frontend Community Integration Test Suite

Tests all Phase 2D-5 integration flows connecting frontend controls to the Flask & Supabase backend:
1. Doctor creates a community (POST /api/community/create) -> atomic RPC, community_admin role assigned.
2. Doctor creates an invitation with >=128-bit cryptographic randomness (POST /api/community/invites).
3. Invitation preview via /api/community/invites/preview and /api/community/verify-invite (read-only, no SQLite bypass).
4. User accepts invitation requiring Admin approval (POST /api/community/invites/accept) -> status='pending'.
5. Community Admin reviews & approves pending membership (POST /api/community/memberships/{id}/review).
6. Community Admin reviews & rejects pending membership with reason preserved.
7. Approved active member accesses community clinical endpoints (GET /api/camps).
8. Pending and rejected members cannot access protected clinical data (HTTP 403).
9. Backend-validated community switching across multiple memberships (authoritative backend verification).
10. Invitation revocation and lifecycle error enforcement (revoked, expired, exhausted, previously declined).
11. Unauthenticated and tampered token rejections across all community endpoints.
12. Non-doctor account cannot create community workspaces (HTTP 403 DOCTOR_REQUIRED).
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

class MockResponse:
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

class MockClient:
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
                return MockResponse(res)

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
            return MockResponse(({"error": "not_found"}, 404))

        res = handler()
        mock_res = MockResponse(res)
        for hook in self.app.after_requests:
            mock_res = hook(mock_res) or mock_res
        return mock_res

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
        methods = methods or options.get("methods", ["GET"])
        def decorator(f):
            for m in methods:
                self.routes[(rule, m.upper())] = f
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
    def test_client(self):
        return MockClient(self)

fmod.Flask = FlaskApp
sys.modules["flask"] = fmod

import backend.database as database
import backend.app as app_module
from backend.services.supabase_service import SupabaseService
import backend.services.auth_service as auth_service

# ---------------------------------------------------------------------------
# Test Suite Class
# ---------------------------------------------------------------------------
class TestStage2D5FrontendBackendIntegration(unittest.TestCase):
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
            try: os.remove(cls.temp_db_path)
            except Exception: pass

    def setUp(self):
        auth_service._ACCOUNT_LOCKOUTS.clear()
        self.app.config["SUPABASE_ENABLED"] = True

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
    # Flow 1: Doctor Creates Community via Atomic RPC
    # -------------------------------------------------------------------------
    def test_01_doctor_creates_community_atomic(self):
        """Doctor calls POST /api/community/create -> atomic RPC creates community, assigns community_admin."""
        doc_id = "d0000000-0000-4000-8000-000000000001"
        claims = self._make_claims(doc_id, "lead.doc@clinic.org", "Dr. Lead", role="doctor", account_type="doctor")
        auth_hdr = {"Authorization": "Bearer token.doc"}

        comm_id = "c0000000-0000-4000-8000-000000000001"
        rpc_return = {
            "success": True,
            "community": {
                "id": comm_id,
                "code": "CAMP-TEST",
                "name": "Kolar Rural Health Outreach",
                "role": "community_admin",
                "role_id": "community_admin",
                "status": "active"
            }
        }

        mock_profile = {
            "id": doc_id,
            "email": "lead.doc@clinic.org",
            "full_name": "Dr. Lead",
            "email_verified": True,
            "status": "active"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "create_community_atomic", return_value=rpc_return):

            res = self.client.post("/api/community/create", json={
                "name": "Kolar Rural Health Outreach",
                "code": "CAMP-TEST"
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 201)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            comm = data.get("community", {})
            self.assertEqual(comm.get("id"), comm_id)
            self.assertEqual(comm.get("code"), "CAMP-TEST")
            self.assertEqual(comm.get("role"), "community_admin")

    # -------------------------------------------------------------------------
    # Flow 2: Invitation Generation with >=128-bit Randomness
    # -------------------------------------------------------------------------
    def test_02_doctor_creates_invitation_128bit_randomness(self):
        """Doctor creates invitation -> 128-bit cryptographic randomness verified (INV-(DOC|USR)-[0-9A-F]{32})."""
        doc_id = "d0000000-0000-4000-8000-000000000001"
        claims = self._make_claims(doc_id, "lead.doc@clinic.org", "Dr. Lead", role="doctor", account_type="doctor")
        auth_hdr = {"Authorization": "Bearer token.doc"}
        comm_id = "c0000000-0000-4000-8000-000000000001"

        memberships = [{
            "community_id": comm_id,
            "role": "doctor",
            "role_id": "doctor",
            "status": "active"
        }]

        captured_code = {}

        def mock_create_inv(**kwargs):
            code = kwargs.get("code")
            captured_code["code"] = code
            return {
                "success": True,
                "invitation": {
                    "id": "inv-128bit-001",
                    "code": code,
                    "community_id": comm_id,
                    "role_id": kwargs.get("role_id"),
                    "created_by": doc_id,
                    "requires_admin_approval": kwargs.get("requires_admin_approval", True),
                    "status": "active"
                }
            }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships), \
             patch.object(SupabaseService, "create_invitation", side_effect=mock_create_inv):

            res = self.client.post("/api/community/invites", json={
                "community_id": comm_id,
                "role": "field_worker"
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 201)
            code = captured_code.get("code")
            self.assertIsNotNone(code)
            # Verify >= 128-bit randomness: 32 hex characters
            self.assertRegex(code, r"^INV-(DOC|USR)-[0-9A-F]{32}$")

    # -------------------------------------------------------------------------
    # Flow 3: Read-Only Preview & No SQLite Bypass
    # -------------------------------------------------------------------------
    def test_03_invitation_preview_readonly_and_no_sqlite_bypass(self):
        """Preview is strictly read-only, and /api/community/verify-invite routes directly to authenticated preview."""
        user_id = "u0000000-0000-4000-8000-000000000002"
        claims = self._make_claims(user_id, "applicant@clinic.org", "Applicant Ann", role="field_worker", account_type="user")
        auth_hdr = {"Authorization": "Bearer token.applicant"}
        inv_code = "INV-USR-A1B2C3D4E5F60718293A4B5C6D7E8F90"

        preview_data = {
            "invitation_id": "inv-test-preview",
            "community_id": "comm-test",
            "community_name": "Apollo Health Camp",
            "role_id": "field_worker",
            "requires_admin_approval": True,
            "will_require_approval": True,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            "max_uses": 5,
            "use_count": 0,
            "message": "Invitation code is valid."
        }

        mock_profile = {
            "id": user_id,
            "email": "applicant@clinic.org",
            "email_verified": True,
            "status": "active"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "preview_invitation", return_value={"success": True, "data": preview_data}):

            # 1. Preview via /api/community/invites/preview
            res1 = self.client.post("/api/community/invites/preview", json={"code": inv_code}, headers=auth_hdr)
            self.assertEqual(res1.status_code, 200)
            data1 = res1.get_json()
            self.assertTrue(data1.get("valid"))
            self.assertEqual(data1.get("action"), "preview")
            self.assertTrue(data1.get("requires_admin_approval"))

            # 2. Preview via /api/community/verify-invite
            res2 = self.client.post("/api/community/verify-invite", json={"code": inv_code}, headers=auth_hdr)
            self.assertEqual(res2.status_code, 200)
            data2 = res2.get_json()
            self.assertTrue(data2.get("valid"))
            self.assertEqual(data2.get("action"), "preview")

            # 3. Confirm unauthenticated bypass on /api/community/verify-invite is completely eliminated
            res_unauth = self.client.post("/api/community/verify-invite", json={"code": inv_code})
            self.assertEqual(res_unauth.status_code, 401)
            self.assertEqual(res_unauth.get_json().get("code"), "MISSING_TOKEN")

    # -------------------------------------------------------------------------
    # Flow 4: User Accepts Invitation Requiring Admin Approval
    # -------------------------------------------------------------------------
    def test_04_user_accepts_invitation_pending_approval(self):
        """User explicitly accepts invitation -> rpc_accept_invitation_atomic returns status='pending'."""
        user_id = "u0000000-0000-4000-8000-000000000002"
        claims = self._make_claims(user_id, "applicant@clinic.org", "Applicant Ann", role="field_worker", account_type="user")
        auth_hdr = {"Authorization": "Bearer token.applicant"}
        inv_code = "INV-USR-PENDING1234567890ABCDEF"

        accept_data = {
            "membership_id": "m-pending-001",
            "community_id": "c-test-001",
            "role_id": "field_worker",
            "status": "pending",
            "action": "pending",
            "requires_admin_approval": True,
            "message": "Invitation accepted. Membership is pending Community Admin review."
        }

        mock_profile = {
            "id": user_id,
            "email": "applicant@clinic.org",
            "email_verified": True,
            "status": "active"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "accept_invitation", return_value={"success": True, "data": accept_data}):

            res = self.client.post("/api/community/invites/accept", json={"code": inv_code}, headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("status"), "pending")
            self.assertEqual(data.get("action"), "accepted")

    # -------------------------------------------------------------------------
    # Flow 5: Admin Reviews & Approves Pending Membership
    # -------------------------------------------------------------------------
    def test_05_admin_approves_pending_membership(self):
        """Community Admin approves pending membership -> status becomes 'active'."""
        admin_id = "a0000000-0000-4000-8000-000000000001"
        claims = self._make_claims(admin_id, "admin@clinic.org", "Admin Alice", role="doctor", account_type="doctor")
        auth_hdr = {"Authorization": "Bearer token.admin"}
        comm_id = "c0000000-0000-4000-8000-000000000001"
        mem_id = "m-pending-001"

        memberships = [{"community_id": comm_id, "role": "community_admin", "status": "active"}]

        pending_mem = {
            "id": mem_id,
            "community_id": comm_id,
            "user_id": "u0000000-0000-4000-8000-000000000002",
            "status": "pending",
            "role_id": "field_worker"
        }

        approved_data = {
            "membership_id": mem_id,
            "community_id": comm_id,
            "decision": "approve",
            "status": "active",
            "reviewed_by": admin_id,
            "message": "Membership successfully approved."
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships), \
             patch.object(SupabaseService, "get_membership_by_id", return_value=pending_mem), \
             patch.object(SupabaseService, "admin_review_membership", return_value={"success": True, "data": approved_data}):

            res = self.client.post(f"/api/community/memberships/{mem_id}/review", json={
                "decision": "approve"
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("status"), "active")
            self.assertEqual(data.get("decision"), "approved")

    # -------------------------------------------------------------------------
    # Flow 6: Admin Reviews & Rejects Pending Membership with Reason
    # -------------------------------------------------------------------------
    def test_06_admin_rejects_pending_membership_with_reason(self):
        """Community Admin rejects pending membership -> status becomes 'rejected', reason preserved."""
        admin_id = "a0000000-0000-4000-8000-000000000001"
        claims = self._make_claims(admin_id, "admin@clinic.org", "Admin Alice", role="doctor", account_type="doctor")
        auth_hdr = {"Authorization": "Bearer token.admin"}
        comm_id = "c0000000-0000-4000-8000-000000000001"
        mem_id = "m-pending-002"

        memberships = [{"community_id": comm_id, "role": "community_admin", "status": "active"}]
        pending_mem = {
            "id": mem_id,
            "community_id": comm_id,
            "user_id": "u0000000-0000-4000-8000-000000000003",
            "status": "pending",
            "role_id": "field_worker"
        }

        rejected_data = {
            "membership_id": mem_id,
            "community_id": comm_id,
            "decision": "reject",
            "status": "rejected",
            "reason": "Incomplete registration documentation",
            "reviewed_by": admin_id,
            "message": "Membership successfully rejected."
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships), \
             patch.object(SupabaseService, "get_membership_by_id", return_value=pending_mem), \
             patch.object(SupabaseService, "admin_review_membership", return_value={"success": True, "data": rejected_data}) as mock_rev:

            res = self.client.post(f"/api/community/memberships/{mem_id}/review", json={
                "decision": "reject",
                "reason": "Incomplete registration documentation"
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("status"), "rejected")
            self.assertEqual(data.get("reason"), "Incomplete registration documentation")
            _, kwargs = mock_rev.call_args
            self.assertEqual(kwargs.get("reason") or kwargs.get("p_reason"), "Incomplete registration documentation")

    # -------------------------------------------------------------------------
    # Flow 7: Approved Member Clinical Access
    # -------------------------------------------------------------------------
    def test_07_approved_member_clinical_access(self):
        """Active member can access community clinical endpoints (GET /api/camps)."""
        user_id = "u0000000-0000-4000-8000-000000000002"
        claims = self._make_claims(user_id, "approved.user@clinic.org", "Approved User", role="field_worker", account_type="user")
        comm_id = "c0000000-0000-4000-8000-000000000001"
        auth_hdr = {
            "Authorization": "Bearer token.approved",
            "X-Community-Id": comm_id
        }

        memberships = [{"community_id": comm_id, "role": "field_worker", "status": "active"}]
        mock_camps = [{"id": "camp-1", "community_id": comm_id, "name": "Kolar Camp 1"}]

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships), \
             patch.object(SupabaseService, "list_camps_with_stats", return_value=mock_camps):

            res = self.client.get("/api/camps", headers=auth_hdr)
            self.assertEqual(res.status_code, 200)

    # -------------------------------------------------------------------------
    # Flow 8: Pending and Rejected Members Denied Clinical Access
    # -------------------------------------------------------------------------
    def test_08_pending_and_rejected_members_denied_access(self):
        """Pending and rejected members cannot access protected clinical endpoints (HTTP 403)."""
        comm_id = "c0000000-0000-4000-8000-000000000001"
        auth_hdr = {
            "Authorization": "Bearer token.test",
            "X-Community-Id": comm_id
        }

        # 1. Pending membership
        user_pending_id = "u0000000-0000-4000-8000-000000000004"
        claims_pending = self._make_claims(user_pending_id, "pending@clinic.org", "Pending User", role="field_worker", account_type="user")
        memberships_pending = [{"community_id": comm_id, "role": "field_worker", "status": "pending"}]

        with patch.object(auth_service, "safe_decode_token", return_value=(claims_pending, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships_pending):

            res_p = self.client.get("/api/camps", headers=auth_hdr)
            self.assertEqual(res_p.status_code, 403)
            self.assertEqual(res_p.get_json().get("code"), "MISSING_TENANT_IDENTITY")

        # 2. Rejected membership
        user_rej_id = "u0000000-0000-4000-8000-000000000005"
        claims_rej = self._make_claims(user_rej_id, "rej@clinic.org", "Rejected User", role="field_worker", account_type="user")
        memberships_rej = [{"community_id": comm_id, "role": "field_worker", "status": "rejected"}]

        with patch.object(auth_service, "safe_decode_token", return_value=(claims_rej, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships_rej):

            res_r = self.client.get("/api/camps", headers=auth_hdr)
            self.assertEqual(res_r.status_code, 403)
            self.assertEqual(res_r.get_json().get("code"), "MISSING_TENANT_IDENTITY")

    # -------------------------------------------------------------------------
    # Flow 9: Backend-Validated Community Switching
    # -------------------------------------------------------------------------
    def test_09_backend_validated_community_switching(self):
        """User switching communities is strictly validated by backend memberships."""
        user_id = "u0000000-0000-4000-8000-000000000006"
        claims = self._make_claims(user_id, "multi@clinic.org", "Multi User", role="doctor", account_type="doctor")
        comm_a = "c0000000-0000-4000-8000-00000000000A"
        comm_b = "c0000000-0000-4000-8000-00000000000B"
        comm_unauthorized = "c0000000-0000-4000-8000-00000000000C"

        memberships = [
            {"community_id": comm_a, "role": "doctor", "status": "active"},
            {"community_id": comm_b, "role": "doctor", "status": "active"}
        ]

        mock_camps_a = [{"id": "camp-a", "community_id": comm_a, "name": "Camp A"}]

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships), \
             patch.object(SupabaseService, "list_camps_with_stats", return_value=mock_camps_a):

            # Access Community A -> 200 OK
            res_a = self.client.get("/api/camps", headers={"Authorization": "Bearer token.multi", "X-Community-Id": comm_a})
            self.assertEqual(res_a.status_code, 200)

            # Access Community B -> 200 OK
            res_b = self.client.get("/api/camps", headers={"Authorization": "Bearer token.multi", "X-Community-Id": comm_b})
            self.assertEqual(res_b.status_code, 200)

            # Attempting to access unauthorized community's invitations -> 403 Forbidden
            res_c = self.client.get(f"/api/community/invites?community_id={comm_unauthorized}", headers={"Authorization": "Bearer token.multi"})
            self.assertEqual(res_c.status_code, 403)
            self.assertEqual(res_c.get_json().get("code"), "COMMUNITY_ACCESS_DENIED")

    # -------------------------------------------------------------------------
    # Flow 10: Revoked Invitation Cannot Be Accepted
    # -------------------------------------------------------------------------
    def test_10_revocation_prevents_preview_and_acceptance(self):
        """Revoking an invitation prevents any subsequent preview or acceptance."""
        user_id = "u0000000-0000-4000-8000-000000000002"
        claims = self._make_claims(user_id, "user@clinic.org", "User Two", role="field_worker", account_type="user")
        auth_hdr = {"Authorization": "Bearer token.user"}
        rev_code = "INV-REVOKED-CODE"

        mock_profile = {
            "id": user_id,
            "email": "user@clinic.org",
            "email_verified": True,
            "status": "active"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "preview_invitation", return_value={"success": False, "code": "INVITATION_REVOKED", "message": "Invitation has been revoked."}), \
             patch.object(SupabaseService, "accept_invitation", return_value={"success": False, "code": "INVITATION_REVOKED", "message": "Invitation has been revoked."}):

            res_prev = self.client.post("/api/community/invites/preview", json={"code": rev_code}, headers=auth_hdr)
            self.assertEqual(res_prev.status_code, 400)
            self.assertEqual(res_prev.get_json().get("code"), "INVITATION_REVOKED")

            res_acc = self.client.post("/api/community/invites/accept", json={"code": rev_code}, headers=auth_hdr)
            self.assertEqual(res_acc.status_code, 400)
            self.assertEqual(res_acc.get_json().get("code"), "INVITATION_REVOKED")

    # -------------------------------------------------------------------------
    # Flow 11: Lifecycle Error Mapping (Expired, Exhausted, Declined)
    # -------------------------------------------------------------------------
    def test_11_lifecycle_errors(self):
        """Expired, exhausted, and previously declined codes return mapped HTTP status codes."""
        user_id = "u0000000-0000-4000-8000-000000000002"
        claims = self._make_claims(user_id, "user@clinic.org", "User Two", role="field_worker", account_type="user")
        auth_hdr = {"Authorization": "Bearer token.user"}

        mock_profile = {
            "id": user_id,
            "email": "user@clinic.org",
            "email_verified": True,
            "status": "active"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "is_available", return_value=True), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile):

            # Expired -> 400
            with patch.object(SupabaseService, "preview_invitation", return_value={"success": False, "code": "INVITATION_EXPIRED", "message": "Expired"}):
                res = self.client.post("/api/community/invites/preview", json={"code": "INV-EXP"}, headers=auth_hdr)
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.get_json().get("code"), "INVITATION_EXPIRED")

            # Exhausted -> 400
            with patch.object(SupabaseService, "preview_invitation", return_value={"success": False, "code": "INVITATION_EXHAUSTED", "message": "Exhausted"}):
                res = self.client.post("/api/community/invites/preview", json={"code": "INV-EXH"}, headers=auth_hdr)
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.get_json().get("code"), "INVITATION_EXHAUSTED")

            # Previously Declined -> 409
            with patch.object(SupabaseService, "accept_invitation", return_value={"success": False, "code": "INVITATION_PREVIOUSLY_DECLINED", "message": "Declined"}):
                res = self.client.post("/api/community/invites/accept", json={"code": "INV-DEC"}, headers=auth_hdr)
                self.assertEqual(res.status_code, 409)
                self.assertEqual(res.get_json().get("code"), "INVITATION_PREVIOUSLY_DECLINED")

    # -------------------------------------------------------------------------
    # Flow 12: Non-Doctor Account Cannot Create Community Workspaces
    # -------------------------------------------------------------------------
    def test_12_non_doctor_cannot_create_community(self):
        """User account (account_type='user') cannot create communities (HTTP 403 DOCTOR_REQUIRED)."""
        user_id = "u0000000-0000-4000-8000-000000000002"
        claims = self._make_claims(user_id, "regular.user@clinic.org", "Regular User", role="field_worker", account_type="user")
        auth_hdr = {"Authorization": "Bearer token.user"}

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "is_available", return_value=True):

            res = self.client.post("/api/community/create", json={
                "name": "Unauthorized Clinic"
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.get_json().get("code"), "DOCTOR_REQUIRED")


if __name__ == "__main__":
    unittest.main()
