"""I2I Phase 2D-1 - Supabase Community Backend Test Suite

Tests all Phase 2D-1 community backend requirements:
1. Verified Doctor successfully creating a community (HTTP 201, creator becomes community_admin).
2. User account being denied community creation (HTTP 403 DOCTOR_REQUIRED).
3. Unverified Doctor being denied community creation (HTTP 403 EMAIL_NOT_VERIFIED).
4. Client-supplied identity in request JSON being strictly ignored (identity derived exclusively from verified JWT & profile).
5. Creator receiving community_admin membership.
6. Retrieval of multiple active community memberships (GET /api/communities).
7. Retrieval of all memberships including pending/rejected/revoked (GET /api/community/memberships).
8. Non-active memberships (pending, rejected, revoked, inactive) not granting community access (HTTP 403).
9. Cross-community isolation enforced between distinct communities.
10. Community code collisions return HTTP 409 COMMUNITY_CODE_TAKEN.
11. Missing community name returns HTTP 400 COMMUNITY_NAME_REQUIRED.
12. Unauthenticated and invalid token requests return HTTP 401.
"""

import sys
import os
import types
import json
import re
import tempfile
import unittest
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
sys.modules["PIL.Image"] = pil_img
sys.modules["PIL.ImageOps"] = pil_ops

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


class TestStage2D1CommunityBackend(unittest.TestCase):
    """Focused backend tests for Phase 2D-1 Supabase Community implementation."""

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
    def _make_doctor_claims(self, user_id="11111111-2222-4333-8444-555555555555",
                            email="dr.verified@hospital.org",
                            name="Dr. Verified",
                            email_verified=True):
        raw = {
            "sub": user_id,
            "email": email,
            "user_metadata": {"full_name": name},
            "app_metadata": {"account_type": "doctor", "role": "doctor"}
        }
        if email_verified:
            raw["email_confirmed_at"] = "2026-11-15T00:00:00Z"
        return {
            "sub": user_id,
            "user_id": user_id,
            "role": "doctor",
            "account_type": "doctor",
            "email": email,
            "name": name,
            "app_metadata": {"account_type": "doctor", "role": "doctor", "email_verified": email_verified},
            "raw": raw
        }

    def _make_user_claims(self, user_id="22222222-3333-4444-8555-666666666666",
                          email="worker@field.org",
                          name="Field Worker"):
        raw = {
            "sub": user_id,
            "email": email,
            "user_metadata": {"full_name": name},
            "app_metadata": {"account_type": "user", "role": "field_worker"}
        }
        return {
            "sub": user_id,
            "user_id": user_id,
            "role": "field_worker",
            "account_type": "user",
            "email": email,
            "name": name,
            "app_metadata": {"account_type": "user", "role": "field_worker"},
            "raw": raw
        }

    # -------------------------------------------------------------------------
    # Test 1: Verified Doctor successfully creates a community
    # -------------------------------------------------------------------------
    def test_01_verified_doctor_creates_community_success(self):
        """Verified doctor calls POST /api/community/create -> HTTP 201 and creator becomes community_admin."""
        doc_id = "11111111-2222-4333-8444-555555555555"
        doc_claims = self._make_doctor_claims(user_id=doc_id, email="dr.alice@clinic.org", name="Dr. Alice Smith")
        auth_hdr = {"Authorization": "Bearer token.doc.verified"}

        mock_profile = {
            "id": doc_id,
            "email": "dr.alice@clinic.org",
            "full_name": "Dr. Alice Smith",
            "status": "active",
            "email_verified": True
        }

        mock_rpc_return = {
            "success": True,
            "community": {
                "id": "c0000001-0000-4000-8000-000000000001",
                "name": "Alice Rural Clinic",
                "code": "ARC001",
                "role": "community_admin",
                "role_id": "community_admin",
                "status": "active"
            }
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "create_community_atomic", return_value=mock_rpc_return) as mock_rpc:

            res = self.client.post("/api/community/create", json={
                "name": "Alice Rural Clinic",
                "code": "ARC001"
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 201)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            comm = data.get("community", {})
            self.assertEqual(comm.get("name"), "Alice Rural Clinic")
            self.assertEqual(comm.get("code"), "ARC001")
            self.assertEqual(comm.get("role"), "community_admin")
            self.assertEqual(comm.get("id"), "c0000001-0000-4000-8000-000000000001")

            # Verify rpc_create_community_atomic was invoked with exact trusted arguments
            mock_rpc.assert_called_once_with(
                name="Alice Rural Clinic",
                code="ARC001",
                creator_id=doc_id,
                creator_name="Dr. Alice Smith",
                creator_email="dr.alice@clinic.org",
                user_role="doctor",
                email_verified=True
            )

    # -------------------------------------------------------------------------
    # Test 2: User account denied community creation (403 DOCTOR_REQUIRED)
    # -------------------------------------------------------------------------
    def test_02_user_denied_community_creation(self):
        """Worker / user account is strictly denied community creation with 403 DOCTOR_REQUIRED."""
        user_claims = self._make_user_claims()
        auth_hdr = {"Authorization": "Bearer token.user"}

        with patch.object(auth_service, "safe_decode_token", return_value=(user_claims, None)), \
             patch.object(SupabaseService, "create_community_atomic") as mock_rpc:

            res = self.client.post("/api/community/create", json={
                "name": "Unauthorized Outreach Network",
                "code": "UON001"
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 403)
            data = res.get_json()
            self.assertEqual(data.get("code"), "DOCTOR_REQUIRED")
            self.assertEqual(data.get("error"), "forbidden")
            mock_rpc.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 3: Unverified Doctor denied community creation (403 EMAIL_NOT_VERIFIED)
    # -------------------------------------------------------------------------
    def test_03_unverified_doctor_denied_community_creation(self):
        """Doctor account without verified email is denied with 403 EMAIL_NOT_VERIFIED."""
        doc_claims = self._make_doctor_claims(email_verified=False)
        auth_hdr = {"Authorization": "Bearer token.doc.unverified"}

        mock_profile = {
            "id": doc_claims["sub"],
            "email": doc_claims["email"],
            "full_name": doc_claims["name"],
            "status": "active",
            "email_verified": False
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "create_community_atomic") as mock_rpc:

            res = self.client.post("/api/community/create", json={
                "name": "Unverified Clinic",
                "code": "UVC001"
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 403)
            data = res.get_json()
            self.assertEqual(data.get("code"), "EMAIL_NOT_VERIFIED")
            self.assertEqual(data.get("error"), "forbidden")
            mock_rpc.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 4: Client-supplied identity in request JSON strictly ignored
    # -------------------------------------------------------------------------
    def test_04_client_supplied_identity_ignored(self):
        """Spoofed identity in request JSON is completely ignored in favor of verified token claims & profile."""
        doc_id = "11111111-2222-4333-8444-555555555555"
        real_email = "dr.legit@hospital.org"
        real_name = "Dr. Legit"
        doc_claims = self._make_doctor_claims(user_id=doc_id, email=real_email, name=real_name)
        auth_hdr = {"Authorization": "Bearer token.doc.verified"}

        mock_profile = {
            "id": doc_id,
            "email": real_email,
            "full_name": real_name,
            "status": "active",
            "email_verified": True
        }

        mock_rpc_return = {
            "success": True,
            "community": {
                "id": "c0000004-0000-4000-8000-000000000004",
                "name": "Tamper Resistant Community",
                "code": "TRC001",
                "role": "community_admin"
            }
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "create_community_atomic", return_value=mock_rpc_return) as mock_rpc:

            # Malicious payload attempting to spoof creator_id, creator_name, email, role
            malicious_payload = {
                "name": "Tamper Resistant Community",
                "code": "TRC001",
                "creator_id": "99999999-9999-9999-9999-999999999999",
                "creator_name": "Injected Hacker",
                "email": "hacker@evil.org",
                "role": "super_admin",
                "account_type": "super_admin",
                "email_verified": True
            }

            res = self.client.post("/api/community/create", json=malicious_payload, headers=auth_hdr)

            self.assertEqual(res.status_code, 201)
            # Assert that the RPC received the legitimate, trusted parameters from token/profile, NOT the spoofed payload!
            mock_rpc.assert_called_once_with(
                name="Tamper Resistant Community",
                code="TRC001",
                creator_id=doc_id,
                creator_name=real_name,
                creator_email=real_email,
                user_role="doctor",
                email_verified=True
            )

    # -------------------------------------------------------------------------
    # Test 5: Community code collision returns HTTP 409 COMMUNITY_CODE_TAKEN
    # -------------------------------------------------------------------------
    def test_05_community_code_collision_returns_409(self):
        """When code is already taken in PostgreSQL, endpoint returns HTTP 409 COMMUNITY_CODE_TAKEN."""
        doc_claims = self._make_doctor_claims()
        auth_hdr = {"Authorization": "Bearer token.doc.verified"}

        mock_profile = {
            "id": doc_claims["sub"],
            "email": doc_claims["email"],
            "full_name": doc_claims["name"],
            "status": "active",
            "email_verified": True
        }

        mock_rpc_error = {
            "success": False,
            "error": "rpc_error",
            "code": "COMMUNITY_CODE_TAKEN",
            "message": 'Community code "TAKEN1" is already in use.',
            "status_code": 400
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "create_community_atomic", return_value=mock_rpc_error):

            res = self.client.post("/api/community/create", json={
                "name": "Duplicate Code Clinic",
                "code": "TAKEN1"
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 409)
            data = res.get_json()
            self.assertEqual(data.get("code"), "COMMUNITY_CODE_TAKEN")
            self.assertEqual(data.get("error"), "conflict")

    # -------------------------------------------------------------------------
    # Test 6: Missing community name returns HTTP 400 COMMUNITY_NAME_REQUIRED
    # -------------------------------------------------------------------------
    def test_06_missing_name_returns_400(self):
        """Missing or whitespace-only name returns HTTP 400 COMMUNITY_NAME_REQUIRED."""
        doc_claims = self._make_doctor_claims()
        auth_hdr = {"Authorization": "Bearer token.doc.verified"}

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)):
            res = self.client.post("/api/community/create", json={"name": "   "}, headers=auth_hdr)
            self.assertEqual(res.status_code, 400)
            data = res.get_json()
            self.assertEqual(data.get("code"), "COMMUNITY_NAME_REQUIRED")

    # -------------------------------------------------------------------------
    # Test 7: Retrieve multiple active communities (GET /api/communities)
    # -------------------------------------------------------------------------
    def test_07_retrieve_user_active_communities(self):
        """User receives all active communities where they have legitimate active membership."""
        user_claims = self._make_doctor_claims()
        auth_hdr = {"Authorization": "Bearer token.doc.verified"}

        mock_memberships = [
            {
                "membership_id": "mem_001",
                "community_id": "c0000001-0000-4000-8000-000000000001",
                "role_id": "community_admin",
                "role": "community_admin",
                "status": "active",
                "joined_at": "2026-11-01T10:00:00Z",
                "community_name": "Clinic Alpha",
                "community_code": "ALPHA01"
            },
            {
                "membership_id": "mem_002",
                "community_id": "c0000002-0000-4000-8000-000000000002",
                "role_id": "doctor",
                "role": "doctor",
                "status": "active",
                "joined_at": "2026-11-05T12:00:00Z",
                "community_name": "Clinic Beta",
                "community_code": "BETA02"
            }
        ]

        with patch.object(auth_service, "safe_decode_token", return_value=(user_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=mock_memberships) as mock_get:

            res = self.client.get("/api/communities", headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            comms = data.get("communities", [])
            self.assertEqual(len(comms), 2)
            self.assertEqual(comms[0]["name"], "Clinic Alpha")
            self.assertEqual(comms[0]["role"], "community_admin")
            self.assertEqual(comms[1]["name"], "Clinic Beta")
            self.assertEqual(comms[1]["role"], "doctor")
            mock_get.assert_called_once_with(user_claims["sub"], active_only=True)

    # -------------------------------------------------------------------------
    # Test 8: Retrieve all user memberships (GET /api/community/memberships)
    # -------------------------------------------------------------------------
    def test_08_retrieve_user_all_memberships_multi_community(self):
        """User can retrieve full list of memberships with statuses and roles."""
        user_claims = self._make_doctor_claims()
        auth_hdr = {"Authorization": "Bearer token.doc.verified"}

        all_mock_memberships = [
            {
                "membership_id": "mem_act",
                "community_id": "comm_act",
                "community_name": "Active Clinic",
                "community_code": "ACT01",
                "role_id": "community_admin",
                "role": "community_admin",
                "status": "active",
                "joined_at": "2026-11-01T10:00:00Z"
            },
            {
                "membership_id": "mem_pend",
                "community_id": "comm_pend",
                "community_name": "Pending Clinic",
                "community_code": "PEND02",
                "role_id": "doctor",
                "role": "doctor",
                "status": "pending",
                "joined_at": "2026-11-10T10:00:00Z"
            },
            {
                "membership_id": "mem_rev",
                "community_id": "comm_rev",
                "community_name": "Revoked Clinic",
                "community_code": "REV03",
                "role_id": "doctor",
                "role": "doctor",
                "status": "revoked",
                "joined_at": "2026-10-01T10:00:00Z"
            }
        ]

        with patch.object(auth_service, "safe_decode_token", return_value=(user_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=all_mock_memberships):

            # 1. Without query filter: returns all memberships
            res = self.client.get("/api/community/memberships", headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(len(data.get("memberships", [])), 3)

            # 2. With status=pending filter: returns only pending
            res_pend = self.client.get("/api/community/memberships?status=pending", headers=auth_hdr)
            self.assertEqual(res_pend.status_code, 200)
            data_pend = res_pend.get_json()
            self.assertEqual(len(data_pend.get("memberships", [])), 1)
            self.assertEqual(data_pend["memberships"][0]["status"], "pending")

    # -------------------------------------------------------------------------
    # Test 9: Pending / rejected / revoked memberships do NOT grant access
    # -------------------------------------------------------------------------
    def test_09_pending_rejected_revoked_memberships_do_not_grant_access(self):
        """Non-active memberships (pending, rejected, revoked) never appear in active communities or grant access."""
        user_claims = self._make_doctor_claims()
        auth_hdr = {"Authorization": "Bearer token.doc.verified"}

        # Return only non-active memberships
        non_active_memberships = [
            {"membership_id": "m1", "community_id": "comm_pend", "status": "pending", "role_id": "doctor"},
            {"membership_id": "m2", "community_id": "comm_rej", "status": "rejected", "role_id": "doctor"},
            {"membership_id": "m3", "community_id": "comm_rev", "status": "revoked", "role_id": "doctor"},
            {"membership_id": "m4", "community_id": "comm_inact", "status": "inactive", "role_id": "doctor"}
        ]

        with patch.object(auth_service, "safe_decode_token", return_value=(user_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=non_active_memberships):

            # GET /api/communities must filter out non-active statuses
            res = self.client.get("/api/communities", headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data.get("communities"), [])

            # Attempting to access members of pending community must be rejected with 403
            res_members = self.client.get("/api/community/members?community_id=comm_pend", headers=auth_hdr)
            self.assertEqual(res_members.status_code, 403)

            # Attempting to access members of revoked community must be rejected with 403
            res_rev = self.client.get("/api/community/members?community_id=comm_rev", headers=auth_hdr)
            self.assertEqual(res_rev.status_code, 403)

    # -------------------------------------------------------------------------
    # Test 10: Cross-community isolation enforced
    # -------------------------------------------------------------------------
    def test_10_cross_community_isolation(self):
        """Caller in Community A is strictly forbidden from accessing Community B resources."""
        doc_claims = self._make_doctor_claims()
        auth_hdr = {"Authorization": "Bearer token.doc.verified"}

        # Caller is an active member ONLY of comm_alpha
        active_memberships = [
            {
                "membership_id": "m_alpha",
                "community_id": "comm_alpha_10",
                "role_id": "doctor",
                "role": "doctor",
                "status": "active",
                "community_name": "Alpha Community",
                "community_code": "ALPHA10"
            }
        ]

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=active_memberships):

            # Accessing Community A members succeeds
            res_a = self.client.get("/api/community/members?community_id=comm_alpha_10", headers=auth_hdr)
            self.assertEqual(res_a.status_code, 200)

            # Accessing Community B members is blocked with 403
            res_b = self.client.get("/api/community/members?community_id=comm_beta_10", headers=auth_hdr)
            self.assertEqual(res_b.status_code, 403)

    # -------------------------------------------------------------------------
    # Test 11: Unauthenticated request rejected (401 MISSING_TOKEN)
    # -------------------------------------------------------------------------
    def test_11_unauthenticated_request_rejected(self):
        """Endpoints require authentication and reject unauthenticated calls with 401 MISSING_TOKEN."""
        res_create = self.client.post("/api/community/create", json={"name": "Test Clinic"})
        self.assertEqual(res_create.status_code, 401)
        self.assertEqual(res_create.get_json().get("code"), "MISSING_TOKEN")

        res_comm = self.client.get("/api/communities")
        self.assertEqual(res_comm.status_code, 401)
        self.assertEqual(res_comm.get_json().get("code"), "MISSING_TOKEN")

        res_mem = self.client.get("/api/community/memberships")
        self.assertEqual(res_mem.status_code, 401)
        self.assertEqual(res_mem.get_json().get("code"), "MISSING_TOKEN")

    # -------------------------------------------------------------------------
    # Test 12: Invalid token rejected (401 INVALID_TOKEN)
    # -------------------------------------------------------------------------
    def test_12_invalid_token_rejected(self):
        """Calls with invalid or expired tokens are rejected with 401 INVALID_TOKEN."""
        bad_hdr = {"Authorization": "Bearer bad.token.here"}
        with patch.object(auth_service, "safe_decode_token", return_value=(None, "Signature expired")):
            res = self.client.post("/api/community/create", json={"name": "Test Clinic"}, headers=bad_hdr)
            self.assertEqual(res.status_code, 401)
            self.assertEqual(res.get_json().get("code"), "INVALID_TOKEN")


if __name__ == "__main__":
    unittest.main()
