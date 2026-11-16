"""I2I Stage 1A - Route-Level Authentication & Security Test Suite

Tests all route-level authentication requirements using Flask test client:
1. Valid login (HTTP 200 with tokens, doctor profile, community and user object).
2. Invalid credentials login (HTTP 401 INVALID_CREDENTIALS).
3. Unverified email login rejection (HTTP 403 EMAIL_NOT_VERIFIED).
4. Retired PIN / OTP / phone inputs rejection (HTTP 400 PIN_AND_OTP_RETIRED / PHONE_LOGIN_RETIRED).
5. Signup input validation:
   - Valid doctor signup (HTTP 201)
   - Valid user signup (HTTP 201)
   - Invalid account_type e.g. "admin" (HTTP 400 INVALID_ACCOUNT_TYPE)
   - Missing email/password (HTTP 400 CREDENTIALS_REQUIRED)
   - Missing full name (HTTP 400 NAME_REQUIRED)
   - Weak password (HTTP 400 WEAK_PASSWORD)
6. Refresh token endpoint:
   - Missing refresh token (HTTP 400 REFRESH_TOKEN_REQUIRED)
   - Invalid refresh token (HTTP 401 INVALID_REFRESH_TOKEN)
   - Successful refresh with rotated tokens (HTTP 200)
   - Supabase unconfigured behavior (HTTP 503 AUTH_UNCONFIGURED)
7. Password reset and recovery endpoints:
   - Forgot password missing email (HTTP 400 EMAIL_REQUIRED)
   - Forgot password unconfigured (HTTP 503 AUTH_UNCONFIGURED)
   - Reset password missing token (HTTP 400 TOKEN_REQUIRED)
   - Reset password weak password (HTTP 400 WEAK_PASSWORD)
   - Reset password unconfigured (HTTP 503 AUTH_UNCONFIGURED)
   - Reset password successful (HTTP 200)
8. Protected endpoints & authorization guard:
   - Request without token rejected with HTTP 401 MISSING_TOKEN.
   - Request with invalid token rejected with HTTP 401 INVALID_TOKEN.
   - Request with valid Bearer token accepted (HTTP 200).
"""

import sys
import os
import types
import json
import re
import tempfile
import unittest
import requests
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
sys.modules["PIL.Image"] = pil_img
sys.modules["PIL.ImageOps"] = pil_ops

# requests shim backed by urllib
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
        if len(parts) != 3:
            raise InvalidTokenError("Invalid token format")
        hdr_b = _b64url_decode(parts[0])
        pay_b = _b64url_decode(parts[1])
        sig_b = _b64url_decode(parts[2])
        msg = f"{parts[0]}.{parts[1]}".encode('ascii')
        expected_sig = hmac.new(key.encode('utf-8') if isinstance(key, str) else key, msg, hashlib.sha256).digest()
        if not hmac.compare_digest(sig_b, expected_sig):
            raise InvalidTokenError("Signature verification failed")
        payload = json.loads(pay_b.decode('utf-8'))
        now = int(time.time())
        if "exp" in payload and payload["exp"] < now:
            raise ExpiredSignatureError("Token has expired")
        if issuer and payload.get("iss") != issuer:
            raise InvalidIssuerError(f"Issuer mismatch: {payload.get('iss')} != {issuer}")
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
            # Sort candidate rules so specific routes take precedence over catch-all (<path:...>) routes
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


class TestAuthRoutes(unittest.TestCase):
    """Route-level tests for Supabase Auth migration in Flask backend."""

    @classmethod
    def setUpClass(cls):
        cls.temp_db_fd, cls.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(cls.temp_db_fd)
        cls._orig_db_path = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = cls.temp_db_path
        database.init_db(cls.temp_db_path)
        cls.app = create_app(db_path=cls.temp_db_path)
        cls.app.config["TESTING"] = True
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

    # =========================================================================
    # 1. Login Endpoint Tests
    # =========================================================================

    def test_01_login_valid_credentials_doctor(self):
        """Valid login returns HTTP 200 with tokens and doctor account type."""
        mock_user = {
            "id": "e4b3c2a1-0000-4000-8000-000000000001",
            "email": "dr.valid@clinic.org",
            "user_metadata": {"full_name": "Dr. Valid Doctor", "account_type": "doctor"}
        }
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_in", return_value={
                 "success": True,
                 "access_token": "supa.access.token.jwt",
                 "refresh_token": "supa.refresh.token.val",
                 "token_type": "bearer",
                 "expires_in": 3600,
                 "user": mock_user
             }), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=[
                 {"community_id": "comm_default", "role_id": "doctor", "status": "active"}
             ]):

            resp = self.client.post("/api/auth/login", json={
                "email": "dr.valid@clinic.org",
                "password": "CorrectPassword123!"
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("access_token"), "supa.access.token.jwt")
            self.assertEqual(data.get("refresh_token"), "supa.refresh.token.val")
            self.assertEqual(data.get("user", {}).get("account_type"), "doctor")
            self.assertEqual(data.get("user", {}).get("id"), mock_user["id"])

    def test_02_login_valid_credentials_worker_mapped_to_user(self):
        """Worker user with field_worker role maps to account_type 'user'."""
        mock_user = {
            "id": "e4b3c2a1-0000-4000-8000-000000000002",
            "email": "worker.valid@clinic.org",
            "user_metadata": {"full_name": "Field Worker Jane", "account_type": "user"}
        }
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_in", return_value={
                 "success": True,
                 "access_token": "supa.access.worker.jwt",
                 "refresh_token": "supa.refresh.worker.val",
                 "token_type": "bearer",
                 "expires_in": 3600,
                 "user": mock_user
             }), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=[
                 {"community_id": "comm_default", "role_id": "field_worker", "status": "active"}
             ]):

            resp = self.client.post("/api/auth/login", json={
                "email": "worker.valid@clinic.org",
                "password": "CorrectPassword123!"
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json
            self.assertEqual(data.get("user", {}).get("account_type"), "user")
            self.assertEqual(data.get("user", {}).get("role"), "field_worker")

    def test_03_login_invalid_credentials_returns_401(self):
        """Invalid credentials return HTTP 401 INVALID_CREDENTIALS."""
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_in", return_value={
                 "success": False,
                 "error": "Invalid login credentials",
                 "code": "INVALID_CREDENTIALS"
             }):

            resp = self.client.post("/api/auth/login", json={
                "email": "dr.valid@clinic.org",
                "password": "WrongPassword999!"
            })
            self.assertEqual(resp.status_code, 401)
            data = resp.json
            self.assertEqual(data.get("code"), "INVALID_CREDENTIALS")

    def test_04_login_unverified_email_returns_403(self):
        """Unverified email returns HTTP 403 EMAIL_NOT_VERIFIED."""
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_in", return_value={
                 "success": False,
                 "error": "Email not confirmed. Please verify your email before logging in.",
                 "code": "EMAIL_NOT_VERIFIED"
             }):

            resp = self.client.post("/api/auth/login", json={
                "email": "unverified.user@clinic.org",
                "password": "Password123!"
            })
            self.assertEqual(resp.status_code, 403)
            data = resp.json
            self.assertEqual(data.get("code"), "EMAIL_NOT_VERIFIED")

    def test_05_login_pin_and_otp_retired_codes(self):
        """PIN, OTP, and phone-only inputs are rejected with HTTP 400."""
        # PIN provided
        r_pin = self.client.post("/api/auth/login", json={"email": "dr@test.org", "pin": "1234"})
        self.assertEqual(r_pin.status_code, 400)
        self.assertEqual(r_pin.json.get("code"), "PIN_AND_OTP_RETIRED")

        # OTP provided
        r_otp = self.client.post("/api/auth/login", json={"email": "dr@test.org", "otp": "999999"})
        self.assertEqual(r_otp.status_code, 400)
        self.assertEqual(r_otp.json.get("code"), "PIN_AND_OTP_RETIRED")

        # Phone only provided
        r_phone = self.client.post("/api/auth/login", json={"phone": "+919876543210"})
        self.assertEqual(r_phone.status_code, 400)
        self.assertEqual(r_phone.json.get("code"), "PHONE_LOGIN_RETIRED")

    # =========================================================================
    # 2. Signup Endpoint Tests
    # =========================================================================

    def test_06_signup_valid_doctor(self):
        """Valid doctor signup returns HTTP 201 with email_verification_required: True."""
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000003"
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_up", return_value={
                 "success": True,
                 "user": {"id": mock_uid, "email": "new.doctor@clinic.org"}
             }), \
             patch.object(SupabaseService, "sync_user_profile", return_value={"success": True}):

            resp = self.client.post("/api/auth/signup", json={
                "email": "new.doctor@clinic.org",
                "password": "SecurePassword123!",
                "full_name": "Dr. New Doctor",
                "account_type": "doctor"
            })
            self.assertEqual(resp.status_code, 201)
            data = resp.json
            self.assertTrue(data.get("success"))
            self.assertTrue(data.get("email_verification_required"))
            self.assertEqual(data.get("user", {}).get("account_type"), "doctor")

    def test_07_signup_valid_user(self):
        """Valid worker user signup returns HTTP 201 with account_type 'user'."""
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000004"
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_up", return_value={
                 "success": True,
                 "user": {"id": mock_uid, "email": "new.worker@clinic.org"}
             }), \
             patch.object(SupabaseService, "sync_user_profile", return_value={"success": True}):

            resp = self.client.post("/api/auth/signup", json={
                "email": "new.worker@clinic.org",
                "password": "SecurePassword123!",
                "full_name": "Worker Jane",
                "account_type": "user"
            })
            self.assertEqual(resp.status_code, 201)
            data = resp.json
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("user", {}).get("account_type"), "user")

    def test_08_signup_invalid_account_type(self):
        """Invalid account_type (e.g. 'admin') rejected with HTTP 400 INVALID_ACCOUNT_TYPE."""
        resp = self.client.post("/api/auth/signup", json={
            "email": "invalid.type@clinic.org",
            "password": "SecurePassword123!",
            "full_name": "Attacker Trying Admin",
            "account_type": "admin"
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json.get("code"), "INVALID_ACCOUNT_TYPE")

    def test_09_signup_validation_failures(self):
        """Missing credentials, missing name, or weak password rejected with HTTP 400."""
        # Missing email/password
        r1 = self.client.post("/api/auth/signup", json={"full_name": "Missing Pass", "account_type": "doctor"})
        self.assertEqual(r1.status_code, 400)
        self.assertEqual(r1.json.get("code"), "CREDENTIALS_REQUIRED")

        # Missing full_name
        r2 = self.client.post("/api/auth/signup", json={
            "email": "dr@test.org", "password": "Password123!", "full_name": ""
        })
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(r2.json.get("code"), "NAME_REQUIRED")

        # Weak password (< 6 chars)
        r3 = self.client.post("/api/auth/signup", json={
            "email": "dr@test.org", "password": "123", "full_name": "Dr. Short Pass"
        })
        self.assertEqual(r3.status_code, 400)
        self.assertEqual(r3.json.get("code"), "WEAK_PASSWORD")

    # =========================================================================
    # 3. Refresh Token Endpoint Tests
    # =========================================================================

    def test_10_refresh_token_missing_returns_400(self):
        """Missing refresh token returns HTTP 400 REFRESH_TOKEN_REQUIRED."""
        with patch.object(SupabaseService, "is_configured", return_value=True):
            resp = self.client.post("/api/auth/refresh", json={})
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json.get("code"), "REFRESH_TOKEN_REQUIRED")

    def test_11_refresh_token_invalid_returns_401(self):
        """Invalid refresh token returns HTTP 401 INVALID_REFRESH_TOKEN."""
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_refresh_token", return_value={
                 "success": False, "error": "Invalid refresh token."
             }):

            resp = self.client.post("/api/auth/refresh", json={"refresh_token": "expired_or_bogus_token"})
            self.assertEqual(resp.status_code, 401)
            self.assertEqual(resp.json.get("code"), "INVALID_REFRESH_TOKEN")

    def test_12_refresh_token_successful_rotation(self):
        """Valid refresh token successfully rotates and returns HTTP 200 with new tokens."""
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_refresh_token", return_value={
                 "success": True,
                 "access_token": "brand_new_access_token",
                 "refresh_token": "rotated_refresh_token",
                 "token_type": "bearer",
                 "expires_in": 3600
             }):

            resp = self.client.post("/api/auth/refresh", json={"refresh_token": "valid_active_refresh_token"})
            self.assertEqual(resp.status_code, 200)
            data = resp.json
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("access_token"), "brand_new_access_token")
            self.assertEqual(data.get("refresh_token"), "rotated_refresh_token")

    def test_13_refresh_token_unconfigured_returns_503(self):
        """When Supabase is unconfigured, refresh returns HTTP 503 AUTH_UNCONFIGURED."""
        with patch.object(SupabaseService, "is_configured", return_value=False):
            resp = self.client.post("/api/auth/refresh", json={"refresh_token": "any_token"})
            self.assertEqual(resp.status_code, 503)
            self.assertEqual(resp.json.get("code"), "AUTH_UNCONFIGURED")

    # =========================================================================
    # 4. Password Recovery & Reset Tests
    # =========================================================================

    def test_14_forgot_password_validation_and_unconfigured(self):
        """Forgot password enforces email presence and returns 503 when unconfigured."""
        # Unconfigured service -> 503
        with patch.object(SupabaseService, "is_configured", return_value=False):
            resp_unconf = self.client.post("/api/auth/forgot-password", json={"email": "dr@test.org"})
            self.assertEqual(resp_unconf.status_code, 503)
            self.assertEqual(resp_unconf.json.get("code"), "AUTH_UNCONFIGURED")

        # Configured but missing email -> 400
        with patch.object(SupabaseService, "is_configured", return_value=True):
            resp_no_email = self.client.post("/api/auth/forgot-password", json={"email": ""})
            self.assertEqual(resp_no_email.status_code, 400)
            self.assertEqual(resp_no_email.json.get("code"), "EMAIL_REQUIRED")

    def test_15_reset_password_validation_and_unconfigured(self):
        """Reset password validates tokens/password and returns 503 when unconfigured."""
        # Unconfigured service -> 503
        with patch.object(SupabaseService, "is_configured", return_value=False):
            resp_unconf = self.client.post("/api/auth/reset-password", json={"new_password": "NewPassword123!"})
            self.assertEqual(resp_unconf.status_code, 503)
            self.assertEqual(resp_unconf.json.get("code"), "AUTH_UNCONFIGURED")

        # Configured but weak password -> 400
        with patch.object(SupabaseService, "is_configured", return_value=True):
            resp_weak = self.client.post("/api/auth/reset-password", json={"new_password": "123"})
            self.assertEqual(resp_weak.status_code, 400)
            self.assertEqual(resp_weak.json.get("code"), "WEAK_PASSWORD")

            # Configured but missing token -> 400
            resp_no_tok = self.client.post("/api/auth/reset-password", json={"new_password": "ValidNewPassword123!"})
            self.assertEqual(resp_no_tok.status_code, 400)
            self.assertEqual(resp_no_tok.json.get("code"), "TOKEN_REQUIRED")

    # =========================================================================
    # 5. Protected Endpoints & Authorization Guard
    # =========================================================================

    def test_16_protected_route_missing_token_returns_401(self):
        """Protected endpoint GET /api/doctor/profile without token returns HTTP 401 MISSING_TOKEN."""
        resp = self.client.get("/api/doctor/profile")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json.get("code"), "MISSING_TOKEN")

    def test_17_protected_route_invalid_token_returns_401(self):
        """Protected endpoint with invalid Bearer token returns HTTP 401 INVALID_TOKEN."""
        resp = self.client.get("/api/doctor/profile", headers={"Authorization": "Bearer totally.invalid.token"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json.get("code"), "INVALID_TOKEN")

    def test_18_protected_route_valid_token_succeeds(self):
        """Protected endpoint with valid token succeeds (HTTP 200)."""
        valid_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000005",
            "email": "dr.priya@clinic.org",
            "role": "doctor",
            "org_id": "comm_health_demo_01"
        }
        with patch.object(auth_service, "safe_decode_token", return_value=(valid_claims, None)):
            resp = self.client.get("/api/doctor/profile", headers={"Authorization": "Bearer mock.valid.jwt"})
            self.assertEqual(resp.status_code, 200)
            data = resp.json
            self.assertEqual(data.get("id"), "doc_priya")

    # =========================================================================
    # 6. Task 1 Remediation: Regression Tests for Unauthenticated Access Blocks
    # =========================================================================

    def test_19_unauthenticated_records_read_and_export_blocked(self):
        """Unauthenticated requests cannot read or export patient clinical records."""
        endpoints = [
            ("GET", "/api/records"),
            ("GET", "/api/records/rec_sample_01"),
            ("GET", "/api/records/rec_sample_01/referral-slip"),
            ("GET", "/api/camps/camp_sample_01/adult-records"),
            ("GET", "/api/camps/camp_sample_01/paediatric-records"),
            ("GET", "/api/camps/camp_sample_01/export"),
            ("GET", "/api/camps/camp_sample_01/export/excel"),
            ("GET", "/api/adult-records/rec_sample_01"),
            ("GET", "/api/paediatric-records/rec_sample_01"),
        ]
        for method, path in endpoints:
            resp = self.client.open(path, method=method)
            self.assertEqual(resp.status_code, 401, f"{method} {path} must return 401 for unauthenticated request")
            self.assertEqual(resp.json.get("code"), "MISSING_TOKEN", f"{method} {path} missing code in error")

    def test_20_unauthenticated_records_create_modify_delete_blocked(self):
        """Unauthenticated requests cannot create, modify, delete or transition records."""
        endpoints = [
            ("POST", "/api/records", {"person_name": "Test Subject"}),
            ("PUT", "/api/records/rec_sample_01", {"person_name": "Updated Name"}),
            ("DELETE", "/api/records/rec_sample_01", None),
            ("POST", "/api/records/rec_sample_01/review", {"reviewer_notes": "Reviewed"}),
            ("PUT", "/api/records/rec_sample_01/review", {"reviewer_notes": "Reviewed"}),
            ("POST", "/api/records/rec_sample_01/approve", {}),
            ("POST", "/api/records/rec_sample_01/lock", {}),
            ("POST", "/api/records/rec_sample_01/unlock", {}),
            ("POST", "/api/records/rec_sample_01/release", {}),
            ("POST", "/api/records/rec_sample_01/erase", {}),
            ("DELETE", "/api/records/rec_sample_01/erase", None),
            ("POST", "/api/records/validate", {"record": {}}),
            ("POST", "/api/records/check-duplicate", {"person_name": "John"}),
        ]
        for method, path, body in endpoints:
            resp = self.client.open(path, method=method, json=body)
            self.assertEqual(resp.status_code, 401, f"{method} {path} must return 401 for unauthenticated caller")
            self.assertEqual(resp.json.get("code"), "MISSING_TOKEN", f"{method} {path} expected MISSING_TOKEN")

    def test_21_unauthenticated_camps_endpoints_blocked(self):
        """Unauthenticated requests cannot read, create, update, delete or view camp analytics."""
        endpoints = [
            ("GET", "/api/camps", None),
            ("POST", "/api/camps", {"name": "New Medical Camp"}),
            ("GET", "/api/camps/camp_sample_01", None),
            ("PUT", "/api/camps/camp_sample_01", {"name": "Updated Camp"}),
            ("DELETE", "/api/camps/camp_sample_01", None),
            ("GET", "/api/camps/camp_sample_01/analytics", None),
        ]
        for method, path, body in endpoints:
            resp = self.client.open(path, method=method, json=body)
            self.assertEqual(resp.status_code, 401, f"{method} {path} must return 401 for unauthenticated request")
            self.assertEqual(resp.json.get("code"), "MISSING_TOKEN")

    def test_22_unauthenticated_ai_extraction_blocked(self):
        """Unauthenticated requests cannot trigger AI OCR / extraction endpoints."""
        for path in ["/api/extract", "/api/extract/v2"]:
            resp = self.client.post(path, json={"images": ["data:image/png;base64,sample"]})
            self.assertEqual(resp.status_code, 401, f"{path} must return 401 for unauthenticated caller")
            self.assertEqual(resp.json.get("code"), "MISSING_TOKEN")

    def test_23_unauthenticated_stats_blocked(self):
        """Unauthenticated requests cannot access aggregate statistics."""
        resp = self.client.get("/api/stats")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json.get("code"), "MISSING_TOKEN")

    def test_24_invalid_and_expired_tokens_rejected_on_clinical_endpoints(self):
        """Invalid, expired, or malformed tokens are strictly rejected with HTTP 401 INVALID_TOKEN."""
        bad_headers = {"Authorization": "Bearer expired.or.tampered.token"}
        with patch.object(auth_service, "safe_decode_token", return_value=(None, "Token expired or signature invalid")):
            for path in ["/api/records", "/api/camps", "/api/extract", "/api/stats"]:
                resp = self.client.open(path, method="GET" if "extract" not in path else "POST", headers=bad_headers)
                self.assertEqual(resp.status_code, 401, f"{path} must reject invalid token with 401")
                self.assertEqual(resp.json.get("code"), "INVALID_TOKEN")

    def test_25_authenticated_clinical_access_succeeds(self):
        """Authenticated requests with valid token can access records, camps, and stats."""
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, community_id, community_role, is_active, created_at, updated_at)
                VALUES ('doc_priya_5', 'e4b3c2a1-0000-4000-8000-000000000005', 'dr.priya@clinic.org', 'Dr. Priya', 'comm_health_demo_01', 'doctor', 1, '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        valid_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000005",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000005",
            "email": "dr.priya@clinic.org",
            "role": "doctor",
            "org_id": "comm_health_demo_01",
            "community_id": "comm_health_demo_01",
            "account_type": "doctor"
        }
        auth_header = {"Authorization": "Bearer valid.supabase.token"}
        with patch.object(auth_service, "safe_decode_token", return_value=(valid_claims, None)):
            # GET /api/records
            resp_records = self.client.get("/api/records", headers=auth_header)
            self.assertEqual(resp_records.status_code, 200)

            # GET /api/camps
            resp_camps = self.client.get("/api/camps", headers=auth_header)
            self.assertEqual(resp_camps.status_code, 200)

            # GET /api/stats
            resp_stats = self.client.get("/api/stats", headers=auth_header)
            self.assertEqual(resp_stats.status_code, 200)

    def test_26_public_flows_accessible_without_token(self):
        """Genuinely public endpoints remain accessible without Bearer token."""
        # Health & status
        resp_h = self.client.get("/api/health")
        self.assertEqual(resp_h.status_code, 200)

        resp_s = self.client.get("/api/status")
        self.assertEqual(resp_s.status_code, 200)

        resp_q = self.client.get("/api/config/quality")
        self.assertEqual(resp_q.status_code, 200)

        # Community verification endpoints (unauthenticated caller, missing code returns 400 not 401)
        resp_comm = self.client.post("/api/community/verify", json={})
        self.assertEqual(resp_comm.status_code, 400)
        self.assertNotEqual(resp_comm.status_code, 401)

        resp_inv = self.client.post("/api/community/verify-invite", json={})
        self.assertEqual(resp_inv.status_code, 400)
        self.assertNotEqual(resp_inv.status_code, 401)

    # =========================================================================
    # 7. Task 2 Remediation: Doctor Profile IDOR & Destructive Endpoints
    # =========================================================================

    def test_27_doctor_profile_access_two_different_identities(self):
        """Two different doctors each access and update only their own profile."""
        doc1_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000001",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000001",
            "email": "dr.priya@clinic.org",
            "name": "Dr. Priya",
            "role": "doctor",
            "account_type": "doctor",
            "org_id": "comm_health_demo_01"
        }
        doc2_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000002",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000002",
            "email": "dr.anand@clinic.org",
            "name": "Dr. Anand",
            "role": "doctor",
            "account_type": "doctor",
            "org_id": "comm_health_demo_01"
        }

        # 1. Doctor 1 accesses profile
        with patch.object(auth_service, "safe_decode_token", return_value=(doc1_claims, None)):
            resp1 = self.client.get("/api/doctor/profile", headers={"Authorization": "Bearer token.doc1"})
            self.assertEqual(resp1.status_code, 200)
            data1 = resp1.json
            self.assertEqual(data1.get("email"), "dr.priya@clinic.org")

        # 2. Doctor 2 creates/updates profile
        with patch.object(auth_service, "safe_decode_token", return_value=(doc2_claims, None)):
            put_resp2 = self.client.put("/api/doctor/profile",
                                        headers={"Authorization": "Bearer token.doc2"},
                                        json={"full_name": "Dr. Anand Kumar", "specialization": "Pediatrician"})
            self.assertEqual(put_resp2.status_code, 200)

            get_resp2 = self.client.get("/api/doctor/profile", headers={"Authorization": "Bearer token.doc2"})
            self.assertEqual(get_resp2.status_code, 200)
            data2 = get_resp2.json
            self.assertEqual(data2.get("email"), "dr.anand@clinic.org")
            self.assertEqual(data2.get("specialization"), "Pediatrician")

        # 3. Doctor 1 accesses profile again, untouched by Doctor 2's update
        with patch.object(auth_service, "safe_decode_token", return_value=(doc1_claims, None)):
            resp1_after = self.client.get("/api/doctor/profile", headers={"Authorization": "Bearer token.doc1"})
            self.assertEqual(resp1_after.status_code, 200)
            data1_after = resp1_after.json
            self.assertEqual(data1_after.get("email"), "dr.priya@clinic.org")
            self.assertNotEqual(data1_after.get("email"), data2.get("email"))

    def test_28_doctor_profile_idor_override_rejected(self):
        """Attempts to access or overwrite another doctor's profile via query param or header return HTTP 403."""
        doc2_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000002",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000002",
            "email": "dr.anand@clinic.org",
            "role": "doctor",
            "org_id": "comm_health_demo_01"
        }
        with patch.object(auth_service, "safe_decode_token", return_value=(doc2_claims, None)):
            # 1. Attempt GET with doctor_id query param pointing to Doctor 1
            resp_get_param = self.client.get("/api/doctor/profile?doctor_id=doc_priya",
                                             headers={"Authorization": "Bearer token.doc2"})
            self.assertEqual(resp_get_param.status_code, 403)
            self.assertEqual(resp_get_param.json.get("code"), "FORBIDDEN")

            # 2. Attempt GET with X-Doctor-ID header pointing to Doctor 1
            resp_get_hdr = self.client.get("/api/doctor/profile",
                                           headers={"Authorization": "Bearer token.doc2", "X-Doctor-ID": "dr.priya@clinic.org"})
            self.assertEqual(resp_get_hdr.status_code, 403)
            self.assertEqual(resp_get_hdr.json.get("code"), "FORBIDDEN")

            # 3. Attempt PUT with doctor_id query param pointing to Doctor 1
            resp_put_param = self.client.put("/api/doctor/profile?doctor_id=doc_priya",
                                             headers={"Authorization": "Bearer token.doc2"},
                                             json={"full_name": "Tampered Name"})
            self.assertEqual(resp_put_param.status_code, 403)
            self.assertEqual(resp_put_param.json.get("code"), "FORBIDDEN")

            # 4. Attempt PUT with X-Doctor-ID header pointing to Doctor 1
            resp_put_hdr = self.client.put("/api/doctor/profile",
                                           headers={"Authorization": "Bearer token.doc2", "X-Doctor-ID": "doc_priya"},
                                           json={"full_name": "Tampered Name"})
            self.assertEqual(resp_put_hdr.status_code, 403)
            self.assertEqual(resp_put_hdr.json.get("code"), "FORBIDDEN")

    def test_29_destructive_endpoints_blocked_for_unauthenticated_and_users_and_doctors(self):
        """Destructive endpoints /api/clear-data and /api/reset-demo are blocked for unauthenticated, user, and doctor accounts."""
        # 1. Unauthenticated requests blocked by middleware (401 MISSING_TOKEN)
        for ep in ["/api/clear-data", "/api/reset-demo"]:
            resp_unauth = self.client.post(ep)
            self.assertEqual(resp_unauth.status_code, 401, f"{ep} must require token")
            self.assertEqual(resp_unauth.json.get("code"), "MISSING_TOKEN")

        # 2. Authenticated user account blocked (403 RESET_DISABLED)
        user_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000099",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000099",
            "email": "worker.jane@clinic.org",
            "role": "field_worker",
            "account_type": "user",
            "org_id": "comm_health_demo_01"
        }
        with patch.object(auth_service, "safe_decode_token", return_value=(user_claims, None)):
            for ep in ["/api/clear-data", "/api/reset-demo"]:
                resp_user = self.client.post(ep, headers={"Authorization": "Bearer token.user"})
                self.assertEqual(resp_user.status_code, 403, f"{ep} must be disabled for ordinary user")
                self.assertEqual(resp_user.json.get("code"), "RESET_DISABLED")

        # 3. Authenticated doctor account blocked (403 RESET_DISABLED)
        doctor_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000001",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000001",
            "email": "dr.priya@clinic.org",
            "role": "doctor",
            "account_type": "doctor",
            "org_id": "comm_health_demo_01"
        }
        with patch.object(auth_service, "safe_decode_token", return_value=(doctor_claims, None)):
            for ep in ["/api/clear-data", "/api/reset-demo"]:
                resp_doc = self.client.post(ep, headers={"Authorization": "Bearer token.doctor"})
                self.assertEqual(resp_doc.status_code, 403, f"{ep} must be disabled for doctor")
                self.assertEqual(resp_doc.json.get("code"), "RESET_DISABLED")

    def test_30_destructive_endpoints_controlled_development_behavior(self):
        """Destructive endpoints succeed ONLY when ALLOW_DATA_RESET=1 in development or testing."""
        doctor_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000001",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000001",
            "email": "dr.priya@clinic.org",
            "role": "doctor",
            "account_type": "doctor",
            "org_id": "comm_health_demo_01"
        }
        auth_hdr = {"Authorization": "Bearer token.doctor"}

        # 1. ALLOW_DATA_RESET=1 and APP_ENV=development -> Succeeds (HTTP 200)
        with patch.object(auth_service, "safe_decode_token", return_value=(doctor_claims, None)), \
             patch.dict(os.environ, {"ALLOW_DATA_RESET": "1", "APP_ENV": "development"}):
            resp_dev_ok = self.client.post("/api/clear-data", headers=auth_hdr)
            self.assertEqual(resp_dev_ok.status_code, 200)
            self.assertIn("cleared successfully", resp_dev_ok.json.get("message", ""))

        # 2. ALLOW_DATA_RESET=1 but APP_ENV=production -> Blocked (HTTP 403 RESET_DISABLED)
        with patch.object(auth_service, "safe_decode_token", return_value=(doctor_claims, None)), \
             patch.dict(os.environ, {"ALLOW_DATA_RESET": "1", "APP_ENV": "production", "TESTING": "0"}):
            resp_prod_block = self.client.post("/api/clear-data", headers=auth_hdr)
            self.assertEqual(resp_prod_block.status_code, 403)
            self.assertEqual(resp_prod_block.json.get("code"), "RESET_DISABLED")

        # 3. ALLOW_DATA_RESET=0 and APP_ENV=development -> Blocked (HTTP 403 RESET_DISABLED)
        with patch.object(auth_service, "safe_decode_token", return_value=(doctor_claims, None)), \
             patch.dict(os.environ, {"ALLOW_DATA_RESET": "0", "APP_ENV": "development", "TESTING": "0"}):
            resp_dev_off = self.client.post("/api/clear-data", headers=auth_hdr)
            self.assertEqual(resp_dev_off.status_code, 403)
            self.assertEqual(resp_dev_off.json.get("code"), "RESET_DISABLED")

    # =========================================================================
    # 8. Task 3 Remediation: Tenant Isolation & Multi-Community Access Control
    # =========================================================================

    def test_31_missing_tenant_identity_denies_access(self):
        """Authenticated caller with no tenant/community identity is denied access with 403."""
        no_tenant_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000088",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000088",
            "email": "dr.notenant@clinic.org",
            "role": "doctor",
            "account_type": "doctor",
            "org_id": None,
            "community_id": None,
            "community_ids": []
        }
        auth_hdr = {"Authorization": "Bearer token.no_tenant"}

        with patch.object(auth_service, "safe_decode_token", return_value=(no_tenant_claims, None)):
            # 1. GET /api/records
            r_recs = self.client.get("/api/records", headers=auth_hdr)
            self.assertEqual(r_recs.status_code, 403)
            self.assertEqual(r_recs.json.get("code"), "MISSING_TENANT_IDENTITY")

            # 2. GET /api/camps
            r_camps = self.client.get("/api/camps", headers=auth_hdr)
            self.assertEqual(r_camps.status_code, 403)
            self.assertEqual(r_camps.json.get("code"), "MISSING_TENANT_IDENTITY")

            # 3. POST /api/records
            r_post_rec = self.client.post("/api/records", json={"person_name": "Test Subject"}, headers=auth_hdr)
            self.assertEqual(r_post_rec.status_code, 403)
            self.assertEqual(r_post_rec.json.get("code"), "MISSING_TENANT_IDENTITY")

            # 4. POST /api/camps
            r_post_camp = self.client.post("/api/camps", json={"name": "Orphan Camp"}, headers=auth_hdr)
            self.assertEqual(r_post_camp.status_code, 403)
            self.assertEqual(r_post_camp.json.get("code"), "MISSING_TENANT_IDENTITY")

            # 5. GET /api/export
            r_export = self.client.get("/api/export", headers=auth_hdr)
            self.assertEqual(r_export.status_code, 403)
            self.assertEqual(r_export.json.get("code"), "MISSING_TENANT_IDENTITY")

            # 6. GET /api/analytics
            r_analytics = self.client.get("/api/analytics", headers=auth_hdr)
            self.assertEqual(r_analytics.status_code, 403)
            self.assertEqual(r_analytics.json.get("code"), "MISSING_TENANT_IDENTITY")

    def test_32_cross_community_resource_access_rejected(self):
        """Caller from Community A cannot read, modify, delete, or export Community B's resources."""
        # Seed test data in Community Beta
        database.create_camp(
            name="Beta Camp",
            expected_records=50,
            camp_id="camp_beta_isolation",
            community_id="comm_beta_secure",
            db_path=self.temp_db_path
        )
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO records (id, camp_id, community_id, person_name, status, scanned_by, created_at, updated_at)
                VALUES ('rec_beta_isolation', 'camp_beta_isolation', 'comm_beta_secure', 'Beta Patient', 'needs_review', 'doc_beta', '2026-10-01', '2026-10-01');
            """)
            cur.execute("""
                INSERT OR REPLACE INTO adult_records (id, camp_id, person_name, created_at, updated_at)
                VALUES ('adult_beta_isolation', 'camp_beta_isolation', 'Beta Adult Patient', '2026-10-01', '2026-10-01');
            """)
            cur.execute("""
                INSERT OR REPLACE INTO paediatric_records (id, camp_id, person_name, created_at, updated_at)
                VALUES ('paed_beta_isolation', 'camp_beta_isolation', 'Beta Paediatric Patient', '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        # Doctor from Community Alpha
        alpha_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000010",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000010",
            "email": "dr.alpha@clinic.org",
            "role": "doctor",
            "account_type": "doctor",
            "org_id": "comm_alpha_secure",
            "community_id": "comm_alpha_secure"
        }
        alpha_hdr = {"Authorization": "Bearer token.alpha"}

        with patch.object(auth_service, "safe_decode_token", return_value=(alpha_claims, None)):
            # 1. Read record across community -> 403
            res = self.client.get("/api/records/rec_beta_isolation", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.json.get("error"), "forbidden")

            # 2. Update record across community -> 403
            res = self.client.put("/api/records/rec_beta_isolation", json={"person_name": "Tampered"}, headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 3. Delete record across community -> 403
            res = self.client.delete("/api/records/rec_beta_isolation", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 4. Review record across community -> 403
            res = self.client.post("/api/records/rec_beta_isolation/review", json={"status": "reviewed"}, headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 5. Approve record across community -> 403
            res = self.client.post("/api/records/rec_beta_isolation/approve", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 6. Audit log across community -> 403
            res = self.client.get("/api/records/rec_beta_isolation/audit", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 7. Referral slip across community -> 403
            res = self.client.get("/api/records/rec_beta_isolation/referral-slip", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 8. Camp details across community -> 403
            res = self.client.get("/api/camps/camp_beta_isolation", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 9. Camp update across community -> 403
            res = self.client.put("/api/camps/camp_beta_isolation", json={"name": "Hacked Camp"}, headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 10. Camp delete across community -> 403
            res = self.client.delete("/api/camps/camp_beta_isolation", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 11. Camp export across community -> 403
            res = self.client.get("/api/camps/camp_beta_isolation/export", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 12. Camp analytics across community -> 403
            res = self.client.get("/api/camps/camp_beta_isolation/analytics", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 13. Adult record across community -> 403
            res = self.client.get("/api/adult-records/adult_beta_isolation", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 14. Paediatric record across community -> 403
            res = self.client.get("/api/paediatric-records/paed_beta_isolation", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 15. Camp adult records list across community -> 403
            res = self.client.get("/api/camps/camp_beta_isolation/adult-records", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

            # 16. Camp paediatric records list across community -> 403
            res = self.client.get("/api/camps/camp_beta_isolation/paediatric-records", headers=alpha_hdr)
            self.assertEqual(res.status_code, 403)

        # Admin from Community Alpha attempting DPDP erase on Beta record -> 403
        admin_alpha_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000011",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000011",
            "email": "admin.alpha@clinic.org",
            "role": "admin",
            "account_type": "doctor",
            "org_id": "comm_alpha_secure",
            "community_id": "comm_alpha_secure"
        }
        with patch.object(auth_service, "safe_decode_token", return_value=(admin_alpha_claims, None)):
            res_erase = self.client.post("/api/records/rec_beta_isolation/erase", headers={"Authorization": "Bearer admin.alpha"})
            self.assertEqual(res_erase.status_code, 403)

    def test_33_multi_community_membership_access(self):
        """User belonging to multiple communities retains legitimate access to each approved community."""
        # Seed camps and records in two communities
        database.create_camp(name="Alpha Camp", expected_records=50, camp_id="camp_multi_alpha", community_id="comm_multi_1", db_path=self.temp_db_path)
        database.create_camp(name="Beta Camp", expected_records=50, camp_id="camp_multi_beta", community_id="comm_multi_2", db_path=self.temp_db_path)
        database.create_camp(name="Gamma Camp", expected_records=50, camp_id="camp_multi_gamma", community_id="comm_multi_3", db_path=self.temp_db_path)

        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO records (id, camp_id, community_id, person_name, status, created_at, updated_at)
                VALUES ('rec_multi_alpha', 'camp_multi_alpha', 'comm_multi_1', 'Patient 1', 'needs_review', '2026-10-01', '2026-10-01');
            """)
            cur.execute("""
                INSERT OR REPLACE INTO records (id, camp_id, community_id, person_name, status, created_at, updated_at)
                VALUES ('rec_multi_beta', 'camp_multi_beta', 'comm_multi_2', 'Patient 2', 'needs_review', '2026-10-01', '2026-10-01');
            """)
            cur.execute("""
                INSERT OR REPLACE INTO records (id, camp_id, community_id, person_name, status, created_at, updated_at)
                VALUES ('rec_multi_gamma', 'camp_multi_gamma', 'comm_multi_3', 'Patient 3', 'needs_review', '2026-10-01', '2026-10-01');
            """)
            # Multi-community doctor: belongs to comm_multi_1 (in doctors table) and comm_multi_2 (creator in communities table)
            cur.execute("""
                INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, community_id, community_role, is_active, created_at, updated_at)
                VALUES ('doc_multi_1', 'e4b3c2a1-0000-4000-8000-000000000020', 'dr.multi@clinic.org', 'Dr. Multi', 'comm_multi_1', 'doctor', 1, '2026-10-01', '2026-10-01');
            """)
            cur.execute("""
                INSERT OR REPLACE INTO communities (id, name, code, creator_name, creator_email, created_at)
                VALUES ('comm_multi_2', 'Multi Community 2', 'COMM-M2', 'Dr. Multi', 'dr.multi@clinic.org', '2026-10-01');
            """)
            conn.commit()

        # Multi-community doctor: belongs to comm_multi_1 and comm_multi_2, but NOT comm_multi_3
        multi_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000020",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000020",
            "email": "dr.multi@clinic.org",
            "role": "doctor",
            "account_type": "doctor"
        }
        multi_hdr = {"Authorization": "Bearer token.multi"}

        with patch.object(auth_service, "safe_decode_token", return_value=(multi_claims, None)):
            # Accessing comm_multi_1 record -> 200 OK
            res_a = self.client.get("/api/records/rec_multi_alpha", headers=multi_hdr)
            self.assertEqual(res_a.status_code, 200)

            # Accessing comm_multi_2 record -> 200 OK
            res_b = self.client.get("/api/records/rec_multi_beta", headers=multi_hdr)
            self.assertEqual(res_b.status_code, 200)

            # Accessing comm_multi_3 record -> 403 Forbidden
            res_c = self.client.get("/api/records/rec_multi_gamma", headers=multi_hdr)
            self.assertEqual(res_c.status_code, 403)

            # Accessing camps
            res_camp_a = self.client.get("/api/camps/camp_multi_alpha", headers=multi_hdr)
            self.assertEqual(res_camp_a.status_code, 200)

            res_camp_b = self.client.get("/api/camps/camp_multi_beta", headers=multi_hdr)
            self.assertEqual(res_camp_b.status_code, 200)

            res_camp_c = self.client.get("/api/camps/camp_multi_gamma", headers=multi_hdr)
            self.assertEqual(res_camp_c.status_code, 403)

    def test_34_orphaned_resource_missing_tenant_ownership_denies_safely(self):
        """Resources without valid tenant ownership deny access safely (fail-closed, no guessing)."""
        database.create_camp(
            name="Orphan Camp",
            expected_records=10,
            camp_id="camp_orphan_valid",
            community_id=None,
            db_path=self.temp_db_path
        )
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO records (id, camp_id, community_id, person_name, status, created_at, updated_at)
                VALUES ('rec_orphan_999', 'camp_orphan_valid', NULL, 'Orphaned Subject', 'needs_review', '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        doctor_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000030",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000030",
            "email": "dr.valid@clinic.org",
            "role": "doctor",
            "account_type": "doctor",
            "org_id": "comm_health_demo_01"
        }
        with patch.object(auth_service, "safe_decode_token", return_value=(doctor_claims, None)):
            res = self.client.get("/api/records/rec_orphan_999", headers={"Authorization": "Bearer token.doctor"})
            # Must safely deny access with 403
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.json.get("error"), "forbidden")

    def test_35_unauthorized_community_creation_rejected(self):
        """User cannot create camps or records targeting an unauthorized community."""
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, community_id, community_role, is_active, created_at, updated_at)
                VALUES ('doc_legit_40', 'e4b3c2a1-0000-4000-8000-000000000040', 'dr.legit@clinic.org', 'Dr. Legit', 'comm_alpha_only', 'admin', 1, '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        doctor_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000040",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000040",
            "email": "dr.legit@clinic.org",
            "role": "doctor",
            "account_type": "doctor"
        }
        auth_hdr = {"Authorization": "Bearer token.doctor"}

        with patch.object(auth_service, "safe_decode_token", return_value=(doctor_claims, None)):
            # 1. Attempt to create camp targeting comm_beta_unauthorized -> 403
            res_camp = self.client.post("/api/camps", json={
                "name": "Malicious Camp",
                "community_id": "comm_beta_unauthorized"
            }, headers=auth_hdr)
            self.assertEqual(res_camp.status_code, 403)

            # 2. Legitimate camp creation in own community -> 201
            res_camp_ok = self.client.post("/api/camps", json={
                "name": "Legit Alpha Camp",
                "community_id": "comm_alpha_only"
            }, headers=auth_hdr)
            self.assertEqual(res_camp_ok.status_code, 201)

            # 3. Attempt to create record in unauthorized community directly -> 403
            res_rec_unauth = self.client.post("/api/records", json={
                "person_name": "Injected Patient",
                "community_id": "comm_beta_unauthorized"
            }, headers=auth_hdr)
            self.assertEqual(res_rec_unauth.status_code, 403)

            # 4. Attempt to create record in unauthorized camp -> 403
            res_rec_bad_camp = self.client.post("/api/records", json={
                "person_name": "Injected Patient",
                "camp_id": "camp_beta_isolation"
            }, headers=auth_hdr)
            self.assertEqual(res_rec_bad_camp.status_code, 403)

    # =========================================================================
    # Task 3 Security Corrections Tests (Requirements A through J)
    # =========================================================================

    def test_36_user_metadata_cannot_grant_community_access(self):
        """Requirement A: User cannot gain access by adding a victim community to user_metadata."""
        database.create_camp(name="Victim Camp", expected_records=10, camp_id="camp_victim_36", community_id="comm_victim_hospital", db_path=self.temp_db_path)
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO records (id, camp_id, community_id, person_name, status, created_at, updated_at)
                VALUES ('rec_victim_36', 'camp_victim_36', 'comm_victim_hospital', 'Patient Victim', 'needs_review', '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        # Attacker crafts user_metadata with victim community
        attacker_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000036",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000036",
            "email": "attacker@darkweb.org",
            "role": "doctor",
            "account_type": "doctor",
            "user_metadata": {
                "community_id": "comm_victim_hospital",
                "community_ids": ["comm_victim_hospital"],
                "org_id": "comm_victim_hospital"
            }
        }
        with patch.object(auth_service, "safe_decode_token", return_value=(attacker_claims, None)):
            res = self.client.get("/api/records/rec_victim_36", headers={"Authorization": "Bearer token.attacker"})
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.json.get("error"), "forbidden")

    def test_37_stale_jwt_claims_cannot_restore_revoked_membership(self):
        """Requirement B: app_metadata and stale JWT claims cannot restore revoked membership."""
        database.create_camp(name="Revoked Camp", expected_records=10, camp_id="camp_revoked_37", community_id="comm_revoked_37", db_path=self.temp_db_path)
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO records (id, camp_id, community_id, person_name, status, created_at, updated_at)
                VALUES ('rec_revoked_37', 'camp_revoked_37', 'comm_revoked_37', 'Patient Revoked', 'needs_review', '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        stale_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000037",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000037",
            "email": "dr.former@clinic.org",
            "role": "doctor",
            "account_type": "doctor",
            "community_ids": ["comm_revoked_37"],
            "app_metadata": {"community_id": "comm_revoked_37", "community_ids": ["comm_revoked_37"]}
        }

        # Supabase is enabled, but live query confirms membership was revoked (empty active memberships)
        self.app.config["SUPABASE_ENABLED"] = True
        try:
            with patch.object(auth_service, "safe_decode_token", return_value=(stale_claims, None)), \
                 patch.object(SupabaseService, "get_user_community_memberships", return_value=[]):
                res = self.client.get("/api/records/rec_revoked_37", headers={"Authorization": "Bearer token.stale"})
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.json.get("error"), "forbidden")
        finally:
            self.app.config.pop("SUPABASE_ENABLED", None)

    def test_38_revoked_pending_or_rejected_membership_is_denied(self):
        """Requirement C: A revoked, pending, or rejected membership is strictly denied."""
        database.create_camp(name="Camp Pen", expected_records=10, camp_id="camp_pen_38", community_id="comm_pen_38", db_path=self.temp_db_path)
        database.create_camp(name="Camp Rej", expected_records=10, camp_id="camp_rej_38", community_id="comm_rej_38", db_path=self.temp_db_path)
        database.create_camp(name="Camp Rev", expected_records=10, camp_id="camp_rev_38", community_id="comm_rev_38", db_path=self.temp_db_path)
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO records (id, camp_id, community_id, person_name, status, created_at, updated_at)
                VALUES 
                    ('rec_pen_38', 'camp_pen_38', 'comm_pen_38', 'Pending Patient', 'needs_review', '2026-10-01', '2026-10-01'),
                    ('rec_rej_38', 'camp_rej_38', 'comm_rej_38', 'Rejected Patient', 'needs_review', '2026-10-01', '2026-10-01'),
                    ('rec_rev_38', 'camp_rev_38', 'comm_rev_38', 'Revoked Patient', 'needs_review', '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        user_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000038",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000038",
            "email": "applicant@clinic.org",
            "role": "doctor",
            "account_type": "doctor"
        }

        mock_non_active = [
            {"community_id": "comm_pen_38", "status": "pending", "role": "doctor"},
            {"community_id": "comm_rej_38", "status": "rejected", "role": "doctor"},
            {"community_id": "comm_rev_38", "status": "revoked", "role": "doctor"}
        ]

        self.app.config["SUPABASE_ENABLED"] = True
        try:
            with patch.object(auth_service, "safe_decode_token", return_value=(user_claims, None)), \
                 patch.object(SupabaseService, "get_user_community_memberships", return_value=mock_non_active):
                for rec_id in ["rec_pen_38", "rec_rej_38", "rec_rev_38"]:
                    res = self.client.get(f"/api/records/{rec_id}", headers={"Authorization": "Bearer token.non_active"})
                    self.assertEqual(res.status_code, 403, f"{rec_id} must be denied")
        finally:
            self.app.config.pop("SUPABASE_ENABLED", None)

    def test_39_supabase_query_failure_denies_without_sqlite_or_jwt_fallback(self):
        """Requirement D: A Supabase membership-query failure does not trigger SQLite or JWT fallback."""
        database.create_camp(name="Fail Camp", expected_records=10, camp_id="camp_fail_39", community_id="comm_fail_39", db_path=self.temp_db_path)
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO records (id, camp_id, community_id, person_name, status, created_at, updated_at)
                VALUES ('rec_fail_39', 'camp_fail_39', 'comm_fail_39', 'Fail Subject', 'needs_review', '2026-10-01', '2026-10-01');
            """)
            # SQLite doctor record exists
            cur.execute("""
                INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, community_id, community_role, is_active, created_at, updated_at)
                VALUES ('doc_39', 'e4b3c2a1-0000-4000-8000-000000000039', 'dr.fail39@clinic.org', 'Dr. Fallback', 'comm_fail_39', 'doctor', 1, '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000039",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000039",
            "email": "dr.fail39@clinic.org",
            "role": "doctor",
            "account_type": "doctor",
            "community_ids": ["comm_fail_39"]
        }

        self.app.config["SUPABASE_ENABLED"] = True
        try:
            with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
                 patch.object(SupabaseService, "get_user_community_memberships", side_effect=Exception("Supabase network error")):
                res = self.client.get("/api/records/rec_fail_39", headers={"Authorization": "Bearer token.fail"})
                # Fail-closed: Must return 403, NOT fall back to SQLite or JWT
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.json.get("error"), "forbidden")
        finally:
            self.app.config.pop("SUPABASE_ENABLED", None)

    def test_40_sqlite_fallback_unavailable_while_supabase_enabled(self):
        """Requirement E: SQLite fallback is unavailable while Supabase is enabled."""
        database.create_camp(name="Sqlite Camp", expected_records=10, camp_id="camp_sqlite_40", community_id="comm_sqlite_40", db_path=self.temp_db_path)
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO records (id, camp_id, community_id, person_name, status, created_at, updated_at)
                VALUES ('rec_sqlite_40', 'camp_sqlite_40', 'comm_sqlite_40', 'SQLite Subject', 'needs_review', '2026-10-01', '2026-10-01');
            """)
            cur.execute("""
                INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, community_id, community_role, is_active, created_at, updated_at)
                VALUES ('doc_40', 'e4b3c2a1-0000-4000-8000-000000000040', 'dr.sqlite40@clinic.org', 'Dr. SqliteOnly', 'comm_sqlite_40', 'doctor', 1, '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000040",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000040",
            "email": "dr.sqlite40@clinic.org",
            "role": "doctor",
            "account_type": "doctor"
        }

        # Supabase is enabled and returns no memberships for this user
        self.app.config["SUPABASE_ENABLED"] = True
        try:
            with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
                 patch.object(SupabaseService, "get_user_community_memberships", return_value=[]):
                res = self.client.get("/api/records/rec_sqlite_40", headers={"Authorization": "Bearer token.sqlite40"})
                # Must reject with 403; SQLite cannot grant access while Supabase is enabled
                self.assertEqual(res.status_code, 403)
        finally:
            self.app.config.pop("SUPABASE_ENABLED", None)

    def test_41_doctor_cannot_manage_another_community(self):
        """Requirement F: A Doctor/User cannot manage another community's invitations, members, or join code."""
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, community_id, community_role, is_active, created_at, updated_at)
                VALUES ('doc_41', 'e4b3c2a1-0000-4000-8000-000000000041', 'dr.own41@clinic.org', 'Dr. Own', 'comm_own_41', 'doctor', 1, '2026-10-01', '2026-10-01');
            """)
            cur.execute("""
                INSERT OR REPLACE INTO communities (id, name, code, creator_name, creator_email, created_at)
                VALUES ('comm_other_41', 'Other Clinic', 'COMM-OTHER', 'Other Admin', 'admin@other.org', '2026-10-01');
            """)
            conn.commit()

        doctor_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000041",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000041",
            "email": "dr.own41@clinic.org",
            "role": "doctor",
            "account_type": "doctor"
        }
        auth_hdr = {"Authorization": "Bearer token.doc41"}

        with patch.object(auth_service, "safe_decode_token", return_value=(doctor_claims, None)):
            # 1. Attempt to create invite for comm_other_41 -> 403
            r_inv = self.client.post("/api/community/invites", json={"community_id": "comm_other_41", "role": "doctor"}, headers=auth_hdr)
            self.assertEqual(r_inv.status_code, 403)

            # 2. Attempt to rotate code for comm_other_41 -> 403
            r_rot = self.client.post("/api/community/rotate-code", json={"community_id": "comm_other_41"}, headers=auth_hdr)
            self.assertEqual(r_rot.status_code, 403)

            # 3. Attempt to view members of comm_other_41 -> 403
            r_mem = self.client.get("/api/community/members?community_id=comm_other_41", headers=auth_hdr)
            self.assertEqual(r_mem.status_code, 403)

    def test_42_community_admin_cannot_manage_different_community(self):
        """Requirement G: A community Admin cannot manage a different community."""
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, community_id, community_role, is_active, created_at, updated_at)
                VALUES 
                    ('admin_42', 'e4b3c2a1-0000-4000-8000-000000000042', 'admin.alpha@clinic.org', 'Admin Alpha', 'comm_alpha_42', 'admin', 1, '2026-10-01', '2026-10-01'),
                    ('target_doc_42', 'e4b3c2a1-0000-4000-8000-000000000099', 'dr.beta@clinic.org', 'Dr. Beta', 'comm_beta_42', 'doctor', 1, '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        admin_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000042",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000042",
            "email": "admin.alpha@clinic.org",
            "role": "doctor",
            "account_type": "doctor"
        }
        auth_hdr = {"Authorization": "Bearer token.admin42"}

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)):
            # 1. Rotate code for comm_beta_42 -> 403
            res_rot = self.client.post("/api/community/rotate-code", json={"community_id": "comm_beta_42"}, headers=auth_hdr)
            self.assertEqual(res_rot.status_code, 403)

            # 2. Revoke member in comm_beta_42 -> 403
            res_rev = self.client.post("/api/community/members/revoke", json={"doctor_id": "target_doc_42"}, headers=auth_hdr)
            self.assertEqual(res_rev.status_code, 403)

    def test_43_invite_deletion_checks_actual_community(self):
        """Requirement H: Invite deletion checks the invitation's actual community."""
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, community_id, community_role, is_active, created_at, updated_at)
                VALUES 
                    ('admin_alpha_43', 'e4b3c2a1-0000-4000-8000-000000000043', 'admin@alpha43.org', 'Admin Alpha', 'comm_alpha_43', 'admin', 1, '2026-10-01', '2026-10-01'),
                    ('admin_beta_43', 'e4b3c2a1-0000-4000-8000-000000000044', 'admin@beta43.org', 'Admin Beta', 'comm_beta_43', 'admin', 1, '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        # Create invite belonging to comm_beta_43
        inv_beta = database.create_invite("comm_beta_43", "invitee@test.org", "doctor", 72, "admin@beta43.org", db_path=self.temp_db_path)

        # Admin Alpha attempts to delete Beta's invite -> 403
        alpha_claims = {"sub": "e4b3c2a1-0000-4000-8000-000000000043", "email": "admin@alpha43.org", "role": "doctor"}
        with patch.object(auth_service, "safe_decode_token", return_value=(alpha_claims, None)):
            res_unauth = self.client.delete(f"/api/community/invites/{inv_beta['id']}", headers={"Authorization": "Bearer token.alpha"})
            self.assertEqual(res_unauth.status_code, 403)

        # Admin Beta deletes own invite -> 200
        beta_claims = {"sub": "e4b3c2a1-0000-4000-8000-000000000044", "email": "admin@beta43.org", "role": "doctor"}
        with patch.object(auth_service, "safe_decode_token", return_value=(beta_claims, None)):
            res_auth = self.client.delete(f"/api/community/invites/{inv_beta['id']}", headers={"Authorization": "Bearer token.beta"})
            self.assertEqual(res_auth.status_code, 200)
            self.assertTrue(res_auth.json.get("success"))

    def test_44_community_members_never_leaks_unrelated_doctors(self):
        """Requirement I: Community member listing never leaks doctors or members from other communities."""
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, specialization, community_id, community_role, is_active, created_at, updated_at)
                VALUES 
                    ('doc_leak_1', 'e4b3c2a1-0000-4000-8000-000000000051', 'doc1@other.org', 'Dr. Other 1', 'General', 'comm_busy_44', 'doctor', 1, '2026-10-01', '2026-10-01'),
                    ('doc_leak_2', 'e4b3c2a1-0000-4000-8000-000000000052', 'doc2@other.org', 'Dr. Other 2', 'Dental', 'comm_busy_44', 'doctor', 1, '2026-10-01', '2026-10-01');
            """)
            # Creator of empty community
            cur.execute("""
                INSERT OR REPLACE INTO communities (id, name, code, creator_name, creator_email, created_at)
                VALUES ('comm_empty_44', 'Empty Community', 'COMM-EMPTY', 'Dr. Solo', 'dr.solo@clinic.org', '2026-10-01');
            """)
            conn.commit()

        solo_claims = {
            "sub": "e4b3c2a1-0000-4000-8000-000000000053",
            "user_id": "e4b3c2a1-0000-4000-8000-000000000053",
            "email": "dr.solo@clinic.org",
            "role": "doctor"
        }
        with patch.object(auth_service, "safe_decode_token", return_value=(solo_claims, None)):
            res = self.client.get("/api/community/members?community_id=comm_empty_44", headers={"Authorization": "Bearer token.solo"})
            self.assertEqual(res.status_code, 200)
            data = res.json
            self.assertEqual(data.get("total"), 0)
            self.assertEqual(len(data.get("members", [])), 0)

    def test_45_legitimate_admin_and_doctor_permissions_succeed(self):
        """Requirement J: Legitimate Admin and Doctor permissions still work according to approved I2I rules."""
        with database.get_db_connection(self.temp_db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO communities (id, name, code, creator_name, creator_email, created_at)
                VALUES ('comm_legit_45', 'Legit Community', 'COMM-LEGIT', 'Dr. Admin', 'admin@legit45.org', '2026-10-01');
            """)
            cur.execute("""
                INSERT OR REPLACE INTO doctors (id, auth_id, email, full_name, community_id, community_role, is_active, created_at, updated_at)
                VALUES 
                    ('doc_admin_45', 'e4b3c2a1-0000-4000-8000-000000000061', 'admin@legit45.org', 'Dr. Admin', 'comm_legit_45', 'admin', 1, '2026-10-01', '2026-10-01'),
                    ('doc_staff_45', 'e4b3c2a1-0000-4000-8000-000000000062', 'staff@legit45.org', 'Dr. Staff', 'comm_legit_45', 'doctor', 1, '2026-10-01', '2026-10-01');
            """)
            conn.commit()

        admin_claims = {"sub": "e4b3c2a1-0000-4000-8000-000000000061", "email": "admin@legit45.org", "name": "Dr. Admin", "role": "doctor"}
        staff_claims = {"sub": "e4b3c2a1-0000-4000-8000-000000000062", "email": "staff@legit45.org", "name": "Dr. Staff", "role": "doctor"}

        # 1. Admin creates invite
        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)):
            r_inv_admin = self.client.post("/api/community/invites", json={"community_id": "comm_legit_45", "role": "doctor"}, headers={"Authorization": "Bearer admin"})
            self.assertEqual(r_inv_admin.status_code, 201)

        # 2. Staff creates invite
        with patch.object(auth_service, "safe_decode_token", return_value=(staff_claims, None)):
            r_inv_staff = self.client.post("/api/community/invites", json={"community_id": "comm_legit_45", "role": "doctor"}, headers={"Authorization": "Bearer staff"})
            self.assertEqual(r_inv_staff.status_code, 201)

        # 3. Staff views invites: sees only their own invite (total: 1)
        with patch.object(auth_service, "safe_decode_token", return_value=(staff_claims, None)):
            r_list_staff = self.client.get("/api/community/invites?community_id=comm_legit_45", headers={"Authorization": "Bearer staff"})
            self.assertEqual(r_list_staff.status_code, 200)
            self.assertEqual(r_list_staff.json.get("total"), 1)

        # 4. Admin views invites: sees all invites in community (total >= 2)
        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)):
            r_list_admin = self.client.get("/api/community/invites?community_id=comm_legit_45", headers={"Authorization": "Bearer admin"})
            self.assertEqual(r_list_admin.status_code, 200)
            self.assertGreaterEqual(r_list_admin.json.get("total"), 2)

        # 5. Staff cannot rotate code (403)
        with patch.object(auth_service, "safe_decode_token", return_value=(staff_claims, None)):
            r_rot_staff = self.client.post("/api/community/rotate-code", json={"community_id": "comm_legit_45"}, headers={"Authorization": "Bearer staff"})
            self.assertEqual(r_rot_staff.status_code, 403)

        # 6. Admin can rotate code (200)
        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)):
            r_rot_admin = self.client.post("/api/community/rotate-code", json={"community_id": "comm_legit_45"}, headers={"Authorization": "Bearer admin"})
            self.assertEqual(r_rot_admin.status_code, 200)
            self.assertTrue(r_rot_admin.json.get("success"))

        # 7. Staff cannot revoke members (403)
        with patch.object(auth_service, "safe_decode_token", return_value=(staff_claims, None)):
            r_rev_staff = self.client.post("/api/community/members/revoke", json={"doctor_id": "doc_admin_45"}, headers={"Authorization": "Bearer staff"})
            self.assertEqual(r_rev_staff.status_code, 403)

        # 8. Admin cannot revoke last admin (400 cannot_revoke_last_admin)
        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)):
            r_rev_last = self.client.post("/api/community/members/revoke", json={"doctor_id": "doc_admin_45"}, headers={"Authorization": "Bearer admin"})
            self.assertEqual(r_rev_last.status_code, 400)
            self.assertEqual(r_rev_last.json.get("error"), "cannot_revoke_last_admin")

        # 9. Admin can revoke staff member (200)
        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)):
            r_rev_staff_ok = self.client.post("/api/community/members/revoke", json={"doctor_id": "doc_staff_45"}, headers={"Authorization": "Bearer admin"})
            self.assertEqual(r_rev_staff_ok.status_code, 200)
            self.assertTrue(r_rev_staff_ok.json.get("success"))

    # -----------------------------------------------------------------------
    # Task 4 Tests: Profile & Signup Synchronization Verification (Conditions 1-10)
    # -----------------------------------------------------------------------

    def test_46_doctor_signup_creates_users_and_user_profiles(self):
        """Condition 1: Doctor signup creates public.users and public.user_profiles."""
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000071"

        posted_urls = []
        posted_payloads = {}

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = []
            return m

        def mock_post(url, *args, **kwargs):
            posted_urls.append(url)
            posted_payloads[url] = kwargs.get("json", {})
            m = MagicMock()
            m.status_code = 201
            m.json.return_value = kwargs.get("json", {})
            return m

        with patch("requests.get", side_effect=mock_get), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.put", return_value=MagicMock(status_code=200)):
            res = svc.sync_user_profile(
                user_id=mock_uid,
                email="doc.cardio@clinic.org",
                full_name="Dr. Cardio",
                account_type="doctor",
                specialization="Cardiology"
            )
            self.assertTrue(res)
            # Verify public.users was created
            users_url = f"{svc.url}/rest/v1/users"
            self.assertIn(users_url, posted_urls)
            self.assertEqual(posted_payloads[users_url]["id"], mock_uid)
            self.assertEqual(posted_payloads[users_url]["email"], "doc.cardio@clinic.org")
            self.assertEqual(posted_payloads[users_url]["full_name"], "Dr. Cardio")

            # Verify public.user_profiles was created for Doctor
            profiles_url = f"{svc.url}/rest/v1/user_profiles"
            self.assertIn(profiles_url, posted_urls)
            self.assertEqual(posted_payloads[profiles_url]["user_id"], mock_uid)
            self.assertEqual(posted_payloads[profiles_url]["specialization"], "Cardiology")

    def test_47_user_signup_creates_users_without_clinical_user_profiles(self):
        """Condition 2: User signup creates public.users without clinical user_profiles."""
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000072"

        posted_urls = []

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = []
            return m

        def mock_post(url, *args, **kwargs):
            posted_urls.append(url)
            m = MagicMock()
            m.status_code = 201
            m.json.return_value = kwargs.get("json", {})
            return m

        with patch("requests.get", side_effect=mock_get), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.put", return_value=MagicMock(status_code=200)):
            res = svc.sync_user_profile(
                user_id=mock_uid,
                email="worker.joe@clinic.org",
                full_name="Worker Joe",
                account_type="user",
                specialization="Community Health Worker"
            )
            self.assertTrue(res)
            # Verify public.users was posted
            self.assertIn(f"{svc.url}/rest/v1/users", posted_urls)
            # Verify public.user_profiles was NOT posted for ordinary User
            self.assertNotIn(f"{svc.url}/rest/v1/user_profiles", posted_urls)

    def test_48_argument_mismatch_eliminated_no_typeerror(self):
        """Condition 3: Exact signature match between auth_signup and sync_user_profile without TypeError."""
        import inspect
        sig = inspect.signature(SupabaseService.sync_user_profile)
        params = list(sig.parameters.keys())
        expected_params = ["self", "user_id", "email", "full_name", "account_type", "specialization"]
        self.assertEqual(params, expected_params)
        self.assertNotIn("role", params)
        self.assertNotIn("community_id", params)
        self.assertNotIn("kwargs", [p.name for p in sig.parameters.values() if p.kind == inspect.Parameter.VAR_KEYWORD])

        # Test calling with the exact arguments used in app.py
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        with patch.object(svc, "is_available", return_value=False):
            try:
                svc.sync_user_profile(
                    user_id="test_uid",
                    email="test@org.org",
                    full_name="Test User",
                    account_type="doctor",
                    specialization="General Physician"
                )
            except TypeError as te:
                self.fail(f"sync_user_profile raised TypeError: {te}")

    def test_49_sync_failure_detected_and_handled_properly(self):
        """Condition 4: Profile sync failure returns HTTP 502 with code PROFILE_SYNC_FAILED while preserving auth account."""
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000074"
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_up", return_value={
                 "success": True,
                 "user": {"id": mock_uid, "email": "sync.fail@clinic.org"}
             }), \
             patch.object(SupabaseService, "sync_user_profile", return_value=False):

            resp = self.client.post("/api/auth/signup", json={
                "email": "sync.fail@clinic.org",
                "password": "SecurePassword123!",
                "full_name": "Dr. Failed Sync",
                "account_type": "doctor"
            })
            self.assertEqual(resp.status_code, 502)
            data = resp.json
            self.assertFalse(data.get("success"))
            self.assertEqual(data.get("code"), "PROFILE_SYNC_FAILED")
            self.assertIn("user", data)
            self.assertEqual(data.get("user", {}).get("id"), mock_uid)
            # Ensure no internal error strings or secrets leaked
            self.assertNotIn("Bearer", json.dumps(data))
            self.assertNotIn("secret", json.dumps(data).lower())

    def test_50_existing_email_verified_preserved_on_profile_sync(self):
        """Condition 5: Existing email_verified flag on public.users is never overwritten with False."""
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000075"

        patch_calls = []

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = [{"id": mock_uid, "email_verified": True, "created_at": "2026-01-01T00:00:00Z"}]
            return m

        def mock_patch(url, *args, **kwargs):
            patch_calls.append((url, kwargs.get("json", {})))
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = kwargs.get("json", {})
            return m

        with patch("requests.get", side_effect=mock_get), \
             patch("requests.patch", side_effect=mock_patch), \
             patch("requests.put", return_value=MagicMock(status_code=200)):
            res = svc.sync_user_profile(
                user_id=mock_uid,
                email="verified.user@clinic.org",
                full_name="Verified User",
                account_type="user"
            )
            self.assertTrue(res)
            # Verify PATCH payload did NOT include email_verified (preserving True)
            self.assertTrue(len(patch_calls) > 0)
            target_patch = next(p for p in patch_calls if "rest/v1/users" in p[0])
            self.assertNotIn("email_verified", target_patch[1])

    def test_51_existing_created_at_preserved_on_retry(self):
        """Condition 6: Existing created_at timestamp is preserved on retry sync."""
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000076"

        patch_calls = []

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = [{"id": mock_uid, "created_at": "2026-01-01T12:00:00Z"}]
            return m

        def mock_patch(url, *args, **kwargs):
            patch_calls.append((url, kwargs.get("json", {})))
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = kwargs.get("json", {})
            return m

        with patch("requests.get", side_effect=mock_get), \
             patch("requests.patch", side_effect=mock_patch), \
             patch("requests.put", return_value=MagicMock(status_code=200)):
            res = svc.sync_user_profile(
                user_id=mock_uid,
                email="existing@clinic.org",
                full_name="Existing User",
                account_type="doctor"
            )
            self.assertTrue(res)
            for url, payload in patch_calls:
                self.assertNotIn("created_at", payload)

    def test_52_existing_doctor_profile_fields_not_overwritten(self):
        """Condition 7: Existing doctor specialization, license, qualification, and bio not overwritten on retry."""
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000077"

        user_profiles_patch = []

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            if "rest/v1/users" in url:
                m.json.return_value = [{"id": mock_uid}]
            elif "rest/v1/user_profiles" in url:
                m.json.return_value = [{
                    "user_id": mock_uid,
                    "specialization": "Pediatric Oncology",
                    "medical_license_number": "MCI-998877",
                    "qualification": "MBBS, MD, DM",
                    "bio": "Specialist in childhood cancers."
                }]
            return m

        def mock_patch(url, *args, **kwargs):
            if "rest/v1/user_profiles" in url:
                user_profiles_patch.append(kwargs.get("json", {}))
            m = MagicMock()
            m.status_code = 200
            return m

        with patch("requests.get", side_effect=mock_get), \
             patch("requests.patch", side_effect=mock_patch), \
             patch("requests.put", return_value=MagicMock(status_code=200)):
            res = svc.sync_user_profile(
                user_id=mock_uid,
                email="oncologist@clinic.org",
                full_name="Dr. Oncologist",
                account_type="doctor",
                specialization="General Physician"
            )
            self.assertTrue(res)
            if user_profiles_patch:
                self.assertNotIn("specialization", user_profiles_patch[0])
                self.assertNotIn("medical_license_number", user_profiles_patch[0])
                self.assertNotIn("qualification", user_profiles_patch[0])
                self.assertNotIn("bio", user_profiles_patch[0])

    def test_53_repeated_synchronization_is_safe_and_idempotent(self):
        """Condition 8: Repeated synchronization is safe, idempotent, and succeeds without duplication."""
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000078"

        mock_db = {"users": {}, "profiles": {}}

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            if "rest/v1/users" in url:
                m.json.return_value = [mock_db["users"][mock_uid]] if mock_uid in mock_db["users"] else []
            elif "rest/v1/user_profiles" in url:
                m.json.return_value = [mock_db["profiles"][mock_uid]] if mock_uid in mock_db["profiles"] else []
            return m

        def mock_post(url, *args, **kwargs):
            payload = kwargs.get("json", {})
            if "rest/v1/users" in url:
                mock_db["users"][payload["id"]] = payload
            elif "rest/v1/user_profiles" in url:
                mock_db["profiles"][payload["user_id"]] = payload
            m = MagicMock()
            m.status_code = 201
            m.json.return_value = payload
            return m

        def mock_patch(url, *args, **kwargs):
            payload = kwargs.get("json", {})
            if "rest/v1/users" in url:
                mock_db["users"][mock_uid].update(payload)
            elif "rest/v1/user_profiles" in url:
                mock_db["profiles"][mock_uid].update(payload)
            m = MagicMock()
            m.status_code = 200
            return m

        with patch("requests.get", side_effect=mock_get), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.patch", side_effect=mock_patch), \
             patch("requests.put", return_value=MagicMock(status_code=200)):

            res1 = svc.sync_user_profile(
                user_id=mock_uid,
                email="idempotent@clinic.org",
                full_name="Dr. Idempotent",
                account_type="doctor",
                specialization="Cardiology"
            )
            self.assertTrue(res1)
            self.assertEqual(len(mock_db["users"]), 1)
            self.assertEqual(len(mock_db["profiles"]), 1)

            res2 = svc.sync_user_profile(
                user_id=mock_uid,
                email="idempotent@clinic.org",
                full_name="Dr. Idempotent",
                account_type="doctor",
                specialization="Cardiology"
            )
            self.assertTrue(res2)
            self.assertEqual(len(mock_db["users"]), 1)
            self.assertEqual(len(mock_db["profiles"]), 1)

    def test_54_signup_without_community_code_creates_no_membership(self):
        """Condition 9: Signup without community_code creates no active community membership."""
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000079"
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_up", return_value={
                 "success": True,
                 "user": {"id": mock_uid, "email": "no.community@clinic.org"}
             }), \
             patch.object(SupabaseService, "sync_user_profile", return_value=True):

            resp = self.client.post("/api/auth/signup", json={
                "email": "no.community@clinic.org",
                "password": "SecurePassword123!",
                "full_name": "Dr. Independent",
                "account_type": "doctor"
            })
            self.assertEqual(resp.status_code, 201)

            with database.get_db_connection(self.temp_db_path) as conn:
                row = conn.execute("SELECT community_id, community_code FROM doctors WHERE id = ?;", (mock_uid,)).fetchone()
                self.assertIsNotNone(row)
                self.assertIsNone(row["community_id"])
                self.assertIsNone(row["community_code"])

    def test_55_supplying_community_code_does_not_grant_active_membership(self):
        """Condition 10: Supplying community_code during signup does NOT grant active membership."""
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000080"
        with database.get_db_connection(self.temp_db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO communities (id, name, code, creator_name, creator_email, created_at)
                VALUES ('comm_pending_55', 'Camp 2026 Community', 'CAMP-2026', 'Creator', 'creator@c55.org', '2026-10-01');
            """)
            conn.commit()

        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_up", return_value={
                 "success": True,
                 "user": {"id": mock_uid, "email": "applicant@clinic.org"}
             }), \
             patch.object(SupabaseService, "sync_user_profile", return_value=True), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=[]):

            resp = self.client.post("/api/auth/signup", json={
                "email": "applicant@clinic.org",
                "password": "SecurePassword123!",
                "full_name": "Dr. Applicant",
                "account_type": "doctor",
                "community_code": "CAMP-2026"
            })
            self.assertEqual(resp.status_code, 201)

            with database.get_db_connection(self.temp_db_path) as conn:
                row = conn.execute("SELECT community_id, community_code FROM doctors WHERE id = ?;", (mock_uid,)).fetchone()
                self.assertIsNotNone(row)
                self.assertIsNone(row["community_id"])
                self.assertEqual(row["community_code"], "CAMP-2026")

    # -----------------------------------------------------------------------
    # Task 4 Final Metadata Tests: app_metadata Validation & Error Handling (56-61)
    # -----------------------------------------------------------------------

    def test_56_metadata_sync_http_200_success(self):
        """Regression Test 1: Admin API returning HTTP 200 marks sync as successful."""
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000081"

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {}
            return m

        def mock_post(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 201
            m.json.return_value = kwargs.get("json", {})
            return m

        def mock_put(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {"id": mock_uid}
            return m

        with patch("requests.get", side_effect=mock_get), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.put", side_effect=mock_put):
            res = svc.sync_user_profile(
                user_id=mock_uid,
                email="meta.ok@clinic.org",
                full_name="Dr. Meta Ok",
                account_type="doctor"
            )
            self.assertTrue(res)

    def test_57_metadata_sync_http_401_and_403_failure(self):
        """Regression Test 2: Admin API returning HTTP 401 or 403 fails synchronization."""
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000082"

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {}
            return m

        def mock_post(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 201
            m.json.return_value = kwargs.get("json", {})
            return m

        # 1. Test 401 Unauthorized
        with patch("requests.get", side_effect=mock_get), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.put", return_value=MagicMock(status_code=401, text="Unauthorized")):
            res_401 = svc.sync_user_profile(
                user_id=mock_uid,
                email="meta.401@clinic.org",
                full_name="Dr. 401",
                account_type="doctor"
            )
            self.assertFalse(res_401)

        # 2. Test 403 Forbidden
        with patch("requests.get", side_effect=mock_get), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.put", return_value=MagicMock(status_code=403, text="Forbidden")):
            res_403 = svc.sync_user_profile(
                user_id=mock_uid,
                email="meta.403@clinic.org",
                full_name="Dr. 403",
                account_type="doctor"
            )
            self.assertFalse(res_403)

    def test_58_metadata_sync_http_500_failure(self):
        """Regression Test 3: Admin API returning HTTP 500 fails synchronization."""
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000083"

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {}
            return m

        def mock_post(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 201
            m.json.return_value = kwargs.get("json", {})
            return m

        with patch("requests.get", side_effect=mock_get), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.put", return_value=MagicMock(status_code=500, text="Internal Server Error")):
            res_500 = svc.sync_user_profile(
                user_id=mock_uid,
                email="meta.500@clinic.org",
                full_name="Dr. 500",
                account_type="doctor"
            )
            self.assertFalse(res_500)

    def test_59_metadata_sync_network_timeout(self):
        """Regression Test 4: Network timeout on Admin API call fails synchronization safely."""
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000084"

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {}
            return m

        def mock_post(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 201
            m.json.return_value = kwargs.get("json", {})
            return m

        # PUT timeout
        with patch("requests.get", side_effect=mock_get), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.put", side_effect=requests.Timeout("Connection timed out")):
            res_timeout = svc.sync_user_profile(
                user_id=mock_uid,
                email="meta.timeout@clinic.org",
                full_name="Dr. Timeout",
                account_type="doctor"
            )
            self.assertFalse(res_timeout)

        # GET timeout
        def mock_get_timeout(url, *args, **kwargs):
            if "admin/users" in url:
                raise requests.Timeout("Admin GET timeout")
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = []
            return m

        with patch("requests.get", side_effect=mock_get_timeout), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.put", return_value=MagicMock(status_code=200)):
            res_get_timeout = svc.sync_user_profile(
                user_id=mock_uid,
                email="meta.get.timeout@clinic.org",
                full_name="Dr. Get Timeout",
                account_type="doctor"
            )
            self.assertFalse(res_get_timeout)

    def test_60_metadata_sync_preserves_existing_metadata(self):
        """Regression Test 5: Updating account_type preserves unrelated existing app_metadata fields."""
        svc = SupabaseService(url="https://test.supabase.co", key="service_role_key")
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000085"

        put_payloads = []

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            if "admin/users" in url:
                m.json.return_value = {
                    "id": mock_uid,
                    "app_metadata": {
                        "provider": "email",
                        "custom_role": "cardiothoracic_surgeon",
                        "facility_access": "tier_1",
                        "license_verified": True
                    }
                }
            else:
                m.json.return_value = [{"id": mock_uid}]
            return m

        def mock_patch(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            return m

        def mock_put(url, *args, **kwargs):
            put_payloads.append(kwargs.get("json", {}))
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = kwargs.get("json", {})
            return m

        with patch("requests.get", side_effect=mock_get), \
             patch("requests.patch", side_effect=mock_patch), \
             patch("requests.put", side_effect=mock_put):
            res = svc.sync_user_profile(
                user_id=mock_uid,
                email="preserve.meta@clinic.org",
                full_name="Dr. Preserved",
                account_type="doctor"
            )
            self.assertTrue(res)
            self.assertTrue(len(put_payloads) > 0)
            target_meta = put_payloads[0].get("app_metadata", {})
            self.assertEqual(target_meta.get("account_type"), "doctor")
            self.assertEqual(target_meta.get("provider"), "email")
            self.assertEqual(target_meta.get("custom_role"), "cardiothoracic_surgeon")
            self.assertEqual(target_meta.get("facility_access"), "tier_1")
            self.assertTrue(target_meta.get("license_verified"))

    def test_61_signup_returns_502_when_metadata_sync_fails(self):
        """Regression Test 6: Signup returns HTTP 502 with PROFILE_SYNC_FAILED when metadata update fails."""
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000086"

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = []
            return m

        def mock_post(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 201
            m.json.return_value = kwargs.get("json", {})
            return m

        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_up", return_value={
                 "success": True,
                 "user": {"id": mock_uid, "email": "meta.fail@clinic.org"}
             }), \
             patch("requests.get", side_effect=mock_get), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.put", return_value=MagicMock(status_code=403, text="Forbidden")):

            resp = self.client.post("/api/auth/signup", json={
                "email": "meta.fail@clinic.org",
                "password": "SecurePassword123!",
                "full_name": "Dr. Meta Failure",
                "account_type": "doctor"
            })
            self.assertEqual(resp.status_code, 502)
            data = resp.json
            self.assertFalse(data.get("success"))
            self.assertEqual(data.get("code"), "PROFILE_SYNC_FAILED")
            self.assertIn("user", data)
            self.assertEqual(data.get("user", {}).get("id"), mock_uid)
            self.assertEqual(data.get("user", {}).get("email"), "meta.fail@clinic.org")

            with database.get_db_connection(self.temp_db_path) as conn:
                row = conn.execute("SELECT id FROM doctors WHERE id = ?;", (mock_uid,)).fetchone()
                self.assertIsNone(row)

    # -----------------------------------------------------------------------
    # Task: Fix MED-01 Focused Regression Tests (62-65)
    # -----------------------------------------------------------------------

    def test_62_doctor_signup_end_to_end_sync_success(self):
        """MED-01 Test 1: Doctor signup end-to-end creates public.users and public.user_profiles with server app_metadata."""
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000087"
        posted_urls = []
        posted_payloads = {}
        put_payloads = []

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = []
            return m

        def mock_post(url, *args, **kwargs):
            posted_urls.append(url)
            posted_payloads[url] = kwargs.get("json", {})
            m = MagicMock()
            m.status_code = 201
            m.json.return_value = kwargs.get("json", {})
            return m

        def mock_put(url, *args, **kwargs):
            put_payloads.append(kwargs.get("json", {}))
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = kwargs.get("json", {})
            return m

        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_up", return_value={
                 "success": True,
                 "user": {"id": mock_uid, "email": "e2e.doc@clinic.org"}
             }), \
             patch("requests.get", side_effect=mock_get), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.put", side_effect=mock_put):

            resp = self.client.post("/api/auth/signup", json={
                "email": "e2e.doc@clinic.org",
                "password": "SecurePassword123!",
                "full_name": "Dr. EndToEnd",
                "account_type": "doctor",
                "specialization": "Pediatrics"
            })
            self.assertEqual(resp.status_code, 201)
            data = resp.json
            self.assertTrue(data.get("success"))
            self.assertTrue(data.get("email_verification_required"))
            self.assertEqual(data.get("user", {}).get("id"), mock_uid)
            self.assertEqual(data.get("user", {}).get("account_type"), "doctor")
            self.assertFalse(data.get("user", {}).get("email_confirmed"))

            # Verify public.users was created
            users_url = next((u for u in posted_urls if "rest/v1/users" in u), None)
            self.assertIsNotNone(users_url)
            self.assertEqual(posted_payloads[users_url]["id"], mock_uid)
            self.assertEqual(posted_payloads[users_url]["email"], "e2e.doc@clinic.org")
            self.assertFalse(posted_payloads[users_url]["email_verified"])

            # Verify public.user_profiles was created for Doctor
            profiles_url = next((u for u in posted_urls if "rest/v1/user_profiles" in u), None)
            self.assertIsNotNone(profiles_url)
            self.assertEqual(posted_payloads[profiles_url]["user_id"], mock_uid)
            self.assertEqual(posted_payloads[profiles_url]["specialization"], "Pediatrics")

            # Verify server-controlled app_metadata was set to 'doctor'
            self.assertTrue(len(put_payloads) > 0)
            self.assertEqual(put_payloads[0].get("app_metadata", {}).get("account_type"), "doctor")

            # Verify no community membership was created during signup
            with database.get_db_connection(self.temp_db_path) as conn:
                row = conn.execute("SELECT community_id FROM doctors WHERE id = ?;", (mock_uid,)).fetchone()
                self.assertIsNotNone(row)
                self.assertIsNone(row["community_id"])

    def test_63_user_signup_end_to_end_sync_success(self):
        """MED-01 Test 2: User signup end-to-end creates public.users WITHOUT user_profiles and sets server app_metadata."""
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000088"
        posted_urls = []
        posted_payloads = {}
        put_payloads = []

        def mock_get(url, *args, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = []
            return m

        def mock_post(url, *args, **kwargs):
            posted_urls.append(url)
            posted_payloads[url] = kwargs.get("json", {})
            m = MagicMock()
            m.status_code = 201
            m.json.return_value = kwargs.get("json", {})
            return m

        def mock_put(url, *args, **kwargs):
            put_payloads.append(kwargs.get("json", {}))
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = kwargs.get("json", {})
            return m

        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_up", return_value={
                 "success": True,
                 "user": {"id": mock_uid, "email": "e2e.worker@clinic.org"}
             }), \
             patch("requests.get", side_effect=mock_get), \
             patch("requests.post", side_effect=mock_post), \
             patch("requests.put", side_effect=mock_put):

            resp = self.client.post("/api/auth/signup", json={
                "email": "e2e.worker@clinic.org",
                "password": "SecurePassword123!",
                "full_name": "Worker Jane",
                "account_type": "user"
            })
            self.assertEqual(resp.status_code, 201)
            data = resp.json
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("user", {}).get("id"), mock_uid)
            self.assertEqual(data.get("user", {}).get("account_type"), "user")

            # Verify public.users was created
            users_url = next((u for u in posted_urls if "rest/v1/users" in u), None)
            self.assertIsNotNone(users_url)
            self.assertEqual(posted_payloads[users_url]["id"], mock_uid)
            self.assertEqual(posted_payloads[users_url]["email"], "e2e.worker@clinic.org")

            # Verify public.user_profiles was NEVER called for user
            profiles_url = next((u for u in posted_urls if "rest/v1/user_profiles" in u), None)
            self.assertIsNone(profiles_url)

            # Verify server-controlled app_metadata was set to 'user'
            self.assertTrue(len(put_payloads) > 0)
            self.assertEqual(put_payloads[0].get("app_metadata", {}).get("account_type"), "user")

    def test_64_sync_typeerror_handled_as_502_profile_sync_failed(self):
        """MED-01 Test 3: TypeError in sync_user_profile is caught and returns HTTP 502 with PROFILE_SYNC_FAILED."""
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000089"
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_up", return_value={
                 "success": True,
                 "user": {"id": mock_uid, "email": "typeerror.fail@clinic.org"}
             }), \
             patch.object(SupabaseService, "sync_user_profile", side_effect=TypeError("sync_user_profile() got unexpected keyword argument")):

            resp = self.client.post("/api/auth/signup", json={
                "email": "typeerror.fail@clinic.org",
                "password": "SecurePassword123!",
                "full_name": "Dr. Type Error",
                "account_type": "doctor"
            })
            self.assertEqual(resp.status_code, 502)
            data = resp.json
            self.assertFalse(data.get("success"))
            self.assertEqual(data.get("code"), "PROFILE_SYNC_FAILED")
            self.assertIn("user", data)
            self.assertEqual(data.get("user", {}).get("id"), mock_uid)
            self.assertEqual(data.get("user", {}).get("email"), "typeerror.fail@clinic.org")

            # Verify no record created in SQLite
            with database.get_db_connection(self.temp_db_path) as conn:
                row = conn.execute("SELECT id FROM doctors WHERE id = ?;", (mock_uid,)).fetchone()
                self.assertIsNone(row)

    def test_65_login_prioritizes_server_controlled_app_metadata(self):
        """MED-01 Test 4: /api/auth/login prioritizes server-controlled app_metadata over client user_metadata."""
        mock_uid = "e4b3c2a1-0000-4000-8000-000000000090"
        with patch.object(SupabaseService, "is_configured", return_value=True), \
             patch.object(SupabaseService, "auth_sign_in", return_value={
                 "success": True,
                 "access_token": "valid_token_65",
                 "refresh_token": "valid_refresh_65",
                 "user": {
                     "id": mock_uid,
                     "email": "claimed.doctor@clinic.org",
                     # Untrusted client assertion claiming doctor:
                     "user_metadata": {"account_type": "doctor", "full_name": "Claimed Doctor"},
                     # Authoritative server metadata designating user:
                     "app_metadata": {"account_type": "user"}
                 }
             }), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=[]):

            resp = self.client.post("/api/auth/login", json={
                "email": "claimed.doctor@clinic.org",
                "password": "ValidPassword123!"
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json
            self.assertTrue(data.get("success"))
            # Must prioritize app_metadata ("user") over user_metadata ("doctor")
            self.assertEqual(data.get("user", {}).get("account_type"), "user")


if __name__ == "__main__":
    unittest.main(verbosity=2)
