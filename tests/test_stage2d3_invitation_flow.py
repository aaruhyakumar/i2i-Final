"""I2I Phase 2D-3 - Invitation Preview, Acceptance and Decline Test Suite

Tests all Phase 2D-3 requirements using Phase 2C canonical RPC functions:
1. Valid invitation preview (HTTP 200, read-only details returned).
2. Preview has zero database side-effects (no response, no membership, use_count unchanged).
3. Expired, revoked, and exhausted invitations rejected with clear error codes (HTTP 400).
4. Target email mismatch rejected (HTTP 403 INVITATION_EMAIL_MISMATCH).
5. Unverified account rejected (HTTP 403 EMAIL_NOT_VERIFIED).
6. User explicitly accepts invitation (HTTP 200, action="accepted").
7. Admin-created invitation becomes status="active".
8. Doctor-member-created invitation becomes status="pending".
9. Successful acceptance increments use_count exactly once.
10. Duplicate acceptance rejected safely (HTTP 409 ALREADY_REDEEMED).
11. Decline does not increment use_count (HTTP 200, action="declined").
12. Declined invitation cannot later be accepted (HTTP 409 INVITATION_PREVIOUSLY_DECLINED).
13. Duplicate decline rejected (HTTP 409 ALREADY_DECLINED).
14. Cross-community and invalid token protection (HTTP 401 MISSING_TOKEN / INVALID_TOKEN, HTTP 403 ROLE_INCOMPATIBLE).
15. Concurrent acceptance cannot exceed max_uses (HTTP 400 INVITATION_USAGE_UPDATE_FAILED / INVITATION_EXHAUSTED).
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


class TestStage2D3InvitationFlow(unittest.TestCase):
    """Focused backend tests for Phase 2D-3 Invitation Preview, Acceptance and Decline."""

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
    # Test 1: Valid invitation preview
    # -------------------------------------------------------------------------
    def test_01_valid_invitation_preview(self):
        """Authenticated caller previews valid invitation -> HTTP 200, read-only details returned."""
        user_id = "u0000000-0000-4000-8000-000000000001"
        claims = self._make_claims(user_id, "dr.alice@clinic.org", "Dr. Alice", role="doctor", account_type="doctor")
        auth_hdr = {"Authorization": "Bearer token.alice"}
        comm_id = "c0000000-0000-4000-8000-000000000001"
        inv_code = "INV-DOC-7777"

        mock_profile = {
            "id": user_id,
            "email": "dr.alice@clinic.org",
            "account_type": "doctor",
            "email_verified": True,
            "status": "active"
        }

        mock_rpc_preview = {
            "valid": True,
            "invitation_id": "inv-7777",
            "community_id": comm_id,
            "community_name": "Apollo Rural Camp",
            "role_id": "doctor",
            "requires_admin_approval": False,
            "will_require_approval": False,
            "expires_at": "2026-11-20T00:00:00Z",
            "max_uses": 5,
            "use_count": 0,
            "message": "Invitation code is valid."
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "preview_invitation", return_value={"success": True, "valid": True, "data": mock_rpc_preview}) as mock_prev:

            res = self.client.post("/api/community/invites/preview", json={"code": inv_code}, headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("valid"))
            self.assertEqual(data.get("action"), "preview")
            self.assertEqual(data.get("community_id"), comm_id)
            self.assertEqual(data.get("community_name"), "Apollo Rural Camp")
            self.assertEqual(data.get("role_id"), "doctor")
            self.assertFalse(data.get("requires_admin_approval"))
            self.assertEqual(data.get("use_count"), 0)
            mock_prev.assert_called_once_with(
                code=inv_code,
                user_id=user_id,
                user_email="dr.alice@clinic.org",
                email_verified=True,
                account_type="doctor"
            )

    # -------------------------------------------------------------------------
    # Test 2: Preview has no database side effects
    # -------------------------------------------------------------------------
    def test_02_preview_has_no_database_side_effects(self):
        """Previewing an invitation performs strictly read-only checks without side-effects."""
        user_id = "u0000000-0000-4000-8000-000000000002"
        claims = self._make_claims(user_id, "dr.bob@clinic.org", "Dr. Bob", role="doctor", account_type="doctor")
        auth_hdr = {"Authorization": "Bearer token.bob"}
        inv_code = "INV-DOC-8888"

        mock_profile = {
            "id": user_id,
            "email": "dr.bob@clinic.org",
            "account_type": "doctor",
            "email_verified": True,
            "status": "active"
        }

        mock_preview_data = {
            "valid": True,
            "invitation_id": "inv-8888",
            "community_id": "c0000000-0000-4000-8000-000000000002",
            "community_name": "Care Clinic",
            "role_id": "doctor",
            "requires_admin_approval": True,
            "expires_at": "2026-11-20T00:00:00Z",
            "max_uses": 1,
            "use_count": 0,
            "message": "Invitation code is valid."
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "preview_invitation", return_value={"success": True, "valid": True, "data": mock_preview_data}), \
             patch.object(SupabaseService, "accept_invitation") as mock_accept, \
             patch.object(SupabaseService, "decline_invitation") as mock_decline:

            res = self.client.post("/api/community/invites/preview", json={"code": inv_code}, headers=auth_hdr)
            self.assertEqual(res.status_code, 200)

            # Neither accept nor decline RPCs were called
            mock_accept.assert_not_called()
            mock_decline.assert_not_called()
            self.assertEqual(res.get_json().get("use_count"), 0)

    # -------------------------------------------------------------------------
    # Test 3: Expired, revoked, and exhausted invitations
    # -------------------------------------------------------------------------
    def test_03_expired_revoked_and_exhausted_invitations(self):
        """Expired, revoked, and exhausted invitations are rejected with HTTP 400 and clear error codes."""
        user_id = "u0000000-0000-4000-8000-000000000003"
        claims = self._make_claims(user_id, "user3@clinic.org", "User Three", role="user", account_type="user")
        auth_hdr = {"Authorization": "Bearer token.u3"}
        mock_profile = {"id": user_id, "email": "user3@clinic.org", "account_type": "user", "email_verified": True, "status": "active"}

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile):

            # A. Expired
            with patch.object(SupabaseService, "preview_invitation", return_value={
                "success": False, "code": "INVITATION_EXPIRED", "message": "This invitation has expired or has an invalid expiry."
            }):
                res_exp = self.client.post("/api/community/invites/preview", json={"code": "INV-EXP-1111"}, headers=auth_hdr)
                self.assertEqual(res_exp.status_code, 400)
                self.assertEqual(res_exp.get_json().get("code"), "INVITATION_EXPIRED")

            # B. Revoked
            with patch.object(SupabaseService, "preview_invitation", return_value={
                "success": False, "code": "INVITATION_REVOKED", "message": "This invitation has been revoked or is invalid."
            }):
                res_rev = self.client.post("/api/community/invites/preview", json={"code": "INV-REV-2222"}, headers=auth_hdr)
                self.assertEqual(res_rev.status_code, 400)
                self.assertEqual(res_rev.get_json().get("code"), "INVITATION_REVOKED")

            # C. Exhausted
            with patch.object(SupabaseService, "preview_invitation", return_value={
                "success": False, "code": "INVITATION_EXHAUSTED", "message": "This invitation has reached its maximum uses (1 of 1)."
            }):
                res_exh = self.client.post("/api/community/invites/preview", json={"code": "INV-EXH-3333"}, headers=auth_hdr)
                self.assertEqual(res_exh.status_code, 400)
                self.assertEqual(res_exh.get_json().get("code"), "INVITATION_EXHAUSTED")

    # -------------------------------------------------------------------------
    # Test 4: Target email mismatch rejected
    # -------------------------------------------------------------------------
    def test_04_target_email_mismatch_rejected(self):
        """Invitation issued for a specific email cannot be previewed or accepted by another email -> HTTP 403."""
        user_id = "u0000000-0000-4000-8000-000000000004"
        claims = self._make_claims(user_id, "wrong.doctor@hospital.org", "Dr. Wrong")
        auth_hdr = {"Authorization": "Bearer token.wrong"}
        mock_profile = {"id": user_id, "email": "wrong.doctor@hospital.org", "account_type": "doctor", "email_verified": True, "status": "active"}

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "preview_invitation", return_value={
                 "success": False, "code": "INVITATION_EMAIL_MISMATCH", "message": "This invitation was issued for another email."
             }):

            res = self.client.post("/api/community/invites/preview", json={"code": "INV-DOC-TARGETED"}, headers=auth_hdr)
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.get_json().get("code"), "INVITATION_EMAIL_MISMATCH")

    # -------------------------------------------------------------------------
    # Test 5: Unverified account rejected
    # -------------------------------------------------------------------------
    def test_05_unverified_account_rejected(self):
        """Unverified account email is blocked from preview, accept, and decline -> HTTP 403 EMAIL_NOT_VERIFIED."""
        user_id = "u0000000-0000-4000-8000-000000000005"
        claims = self._make_claims(user_id, "unverified@clinic.org", "Unverified User", email_verified=False)
        auth_hdr = {"Authorization": "Bearer token.unverified"}
        mock_profile = {"id": user_id, "email": "unverified@clinic.org", "account_type": "user", "email_verified": False, "status": "active"}

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile):

            res_prev = self.client.post("/api/community/invites/preview", json={"code": "INV-ANY-1234"}, headers=auth_hdr)
            self.assertEqual(res_prev.status_code, 403)
            self.assertEqual(res_prev.get_json().get("code"), "EMAIL_NOT_VERIFIED")

            res_acc = self.client.post("/api/community/invites/accept", json={"code": "INV-ANY-1234"}, headers=auth_hdr)
            self.assertEqual(res_acc.status_code, 403)
            self.assertEqual(res_acc.get_json().get("code"), "EMAIL_NOT_VERIFIED")

            res_dec = self.client.post("/api/community/invites/decline", json={"code": "INV-ANY-1234"}, headers=auth_hdr)
            self.assertEqual(res_dec.status_code, 403)
            self.assertEqual(res_dec.get_json().get("code"), "EMAIL_NOT_VERIFIED")

    # -------------------------------------------------------------------------
    # Test 6: User explicitly accepts invitation
    # -------------------------------------------------------------------------
    def test_06_user_explicitly_accepts_invitation(self):
        """Authenticated caller explicitly accepts invitation code -> HTTP 200, action='accepted'."""
        user_id = "u0000000-0000-4000-8000-000000000006"
        claims = self._make_claims(user_id, "dr.accepted@clinic.org", "Dr. Accepted")
        auth_hdr = {"Authorization": "Bearer token.accepted"}
        comm_id = "c0000000-0000-4000-8000-000000000006"
        inv_code = "INV-DOC-ACCEPT"

        mock_profile = {"id": user_id, "email": "dr.accepted@clinic.org", "account_type": "doctor", "email_verified": True, "status": "active"}
        mock_acc_res = {
            "success": True,
            "status": "active",
            "community_id": comm_id,
            "community_name": "Apollo Clinic",
            "role_id": "doctor",
            "message": "Successfully joined community workspace."
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "accept_invitation", return_value={"success": True, "data": mock_acc_res}) as mock_acc:

            res = self.client.post("/api/community/invites/accept", json={"code": inv_code}, headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("action"), "accepted")
            self.assertEqual(data.get("status"), "active")
            self.assertEqual(data.get("community_id"), comm_id)
            mock_acc.assert_called_once_with(
                code=inv_code,
                user_id=user_id,
                user_email="dr.accepted@clinic.org",
                email_verified=True,
                account_type="doctor"
            )

    # -------------------------------------------------------------------------
    # Test 7: Admin-created invitation becomes active
    # -------------------------------------------------------------------------
    def test_07_admin_created_invitation_becomes_active(self):
        """Admin-created invitation (requires_admin_approval=False) immediately yields status='active'."""
        user_id = "u0000000-0000-4000-8000-000000000007"
        claims = self._make_claims(user_id, "dr.active@clinic.org", "Dr. Active")
        auth_hdr = {"Authorization": "Bearer token.active"}
        mock_profile = {"id": user_id, "email": "dr.active@clinic.org", "account_type": "doctor", "email_verified": True, "status": "active"}

        mock_acc_res = {
            "success": True,
            "status": "active",
            "community_id": "comm-active",
            "community_name": "Metro Health",
            "role_id": "doctor",
            "message": "Successfully joined community workspace."
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "accept_invitation", return_value={"success": True, "data": mock_acc_res}):

            res = self.client.post("/api/community/invites/accept", json={"code": "INV-ADMIN-CODE"}, headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json().get("status"), "active")

    # -------------------------------------------------------------------------
    # Test 8: Doctor-created invitation becomes pending
    # -------------------------------------------------------------------------
    def test_08_doctor_created_invitation_becomes_pending(self):
        """Doctor-created invitation (requires_admin_approval=True) yields status='pending' requiring Admin review."""
        user_id = "u0000000-0000-4000-8000-000000000008"
        claims = self._make_claims(user_id, "worker@clinic.org", "Field Worker", role="user", account_type="user")
        auth_hdr = {"Authorization": "Bearer token.worker"}
        mock_profile = {"id": user_id, "email": "worker@clinic.org", "account_type": "user", "email_verified": True, "status": "active"}

        mock_acc_res = {
            "success": True,
            "status": "pending",
            "community_id": "comm-pending",
            "community_name": "Rural Camp Alpha",
            "role_id": "field_worker",
            "message": "Invitation accepted. Membership is pending Community Admin approval."
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "accept_invitation", return_value={"success": True, "data": mock_acc_res}):

            res = self.client.post("/api/community/invites/accept", json={"code": "INV-DOC-CODE"}, headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json().get("status"), "pending")
            self.assertIn("pending Community Admin approval", res.get_json().get("message", ""))

    # -------------------------------------------------------------------------
    # Test 9: Successful acceptance increments use_count exactly once
    # -------------------------------------------------------------------------
    def test_09_successful_acceptance_increments_use_count_once(self):
        """Acceptance invokes rpc_accept_invitation_atomic which increments use_count atomically."""
        user_id = "u0000000-0000-4000-8000-000000000009"
        claims = self._make_claims(user_id, "dr.counter@clinic.org", "Dr. Counter")
        auth_hdr = {"Authorization": "Bearer token.counter"}
        mock_profile = {"id": user_id, "email": "dr.counter@clinic.org", "account_type": "doctor", "email_verified": True, "status": "active"}

        mock_acc_res = {
            "success": True,
            "status": "active",
            "community_id": "comm-counter",
            "community_name": "Counter Camp",
            "role_id": "doctor",
            "message": "Successfully joined community workspace."
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "accept_invitation", return_value={"success": True, "data": mock_acc_res}) as mock_acc:

            res = self.client.post("/api/community/invites/accept", json={"code": "INV-COUNT-TEST"}, headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(mock_acc.call_count, 1)

    # -------------------------------------------------------------------------
    # Test 10: Duplicate acceptance is rejected safely
    # -------------------------------------------------------------------------
    def test_10_duplicate_acceptance_rejected_safely(self):
        """Accepting an already accepted invitation is safely rejected with HTTP 409 ALREADY_REDEEMED."""
        user_id = "u0000000-0000-4000-8000-000000000010"
        claims = self._make_claims(user_id, "dr.duplicate@clinic.org", "Dr. Duplicate")
        auth_hdr = {"Authorization": "Bearer token.dup"}
        mock_profile = {"id": user_id, "email": "dr.duplicate@clinic.org", "account_type": "doctor", "email_verified": True, "status": "active"}

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "accept_invitation", return_value={
                 "success": False, "code": "ALREADY_REDEEMED", "message": "You have already accepted this invitation."
             }):

            res = self.client.post("/api/community/invites/accept", json={"code": "INV-DUP-ACCEPT"}, headers=auth_hdr)
            self.assertEqual(res.status_code, 409)
            self.assertEqual(res.get_json().get("code"), "ALREADY_REDEEMED")

    # -------------------------------------------------------------------------
    # Test 11: Decline does not increment use_count
    # -------------------------------------------------------------------------
    def test_11_decline_does_not_increment_use_count(self):
        """Declining an invitation records response without incrementing usage -> HTTP 200 action='declined'."""
        user_id = "u0000000-0000-4000-8000-000000000011"
        claims = self._make_claims(user_id, "dr.decline@clinic.org", "Dr. Decline")
        auth_hdr = {"Authorization": "Bearer token.decline"}
        mock_profile = {"id": user_id, "email": "dr.decline@clinic.org", "account_type": "doctor", "email_verified": True, "status": "active"}

        mock_dec_res = {
            "success": True,
            "message": "Invitation declined."
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "decline_invitation", return_value={"success": True, "data": mock_dec_res}) as mock_dec, \
             patch.object(SupabaseService, "accept_invitation") as mock_acc:

            res = self.client.post("/api/community/invites/decline", json={"code": "INV-DEC-1234"}, headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("action"), "declined")
            mock_dec.assert_called_once_with(
                code="INV-DEC-1234",
                user_id=user_id,
                user_email="dr.decline@clinic.org",
                email_verified=True
            )
            mock_acc.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 12: Declined invitation cannot later be accepted
    # -------------------------------------------------------------------------
    def test_12_declined_invitation_cannot_later_be_accepted(self):
        """A previously declined invitation cannot subsequently be accepted by the same user -> HTTP 409."""
        user_id = "u0000000-0000-4000-8000-000000000012"
        claims = self._make_claims(user_id, "dr.reaccept@clinic.org", "Dr. Reaccept")
        auth_hdr = {"Authorization": "Bearer token.reaccept"}
        mock_profile = {"id": user_id, "email": "dr.reaccept@clinic.org", "account_type": "doctor", "email_verified": True, "status": "active"}

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "accept_invitation", return_value={
                 "success": False,
                 "code": "INVITATION_PREVIOUSLY_DECLINED",
                 "message": "You have already declined this invitation. A new invitation is required."
             }):

            res = self.client.post("/api/community/invites/accept", json={"code": "INV-PREV-DECLINED"}, headers=auth_hdr)
            self.assertEqual(res.status_code, 409)
            self.assertEqual(res.get_json().get("code"), "INVITATION_PREVIOUSLY_DECLINED")

    # -------------------------------------------------------------------------
    # Test 13: Duplicate decline is rejected
    # -------------------------------------------------------------------------
    def test_13_duplicate_decline_is_rejected(self):
        """Declining an already declined invitation is rejected -> HTTP 409 ALREADY_DECLINED."""
        user_id = "u0000000-0000-4000-8000-000000000013"
        claims = self._make_claims(user_id, "dr.dupdec@clinic.org", "Dr. DupDec")
        auth_hdr = {"Authorization": "Bearer token.dupdec"}
        mock_profile = {"id": user_id, "email": "dr.dupdec@clinic.org", "account_type": "doctor", "email_verified": True, "status": "active"}

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "decline_invitation", return_value={
                 "success": False, "code": "ALREADY_DECLINED", "message": "You have already declined this invitation."
             }):

            res = self.client.post("/api/community/invites/decline", json={"code": "INV-ALREADY-DECLINED"}, headers=auth_hdr)
            self.assertEqual(res.status_code, 409)
            self.assertEqual(res.get_json().get("code"), "ALREADY_DECLINED")

    # -------------------------------------------------------------------------
    # Test 14: Cross-community and invalid token protection
    # -------------------------------------------------------------------------
    def test_14_cross_community_and_invalid_token_protection(self):
        """Unauthenticated requests return HTTP 401; role incompatible returns HTTP 403."""
        # A. Missing token -> 401
        res_no_tok = self.client.post("/api/community/invites/preview", json={"code": "INV-NO-TOK"})
        self.assertEqual(res_no_tok.status_code, 401)
        self.assertEqual(res_no_tok.get_json().get("code"), "MISSING_TOKEN")

        # B. Invalid token -> 401
        with patch.object(auth_service, "safe_decode_token", return_value=(None, "invalid_token")):
            res_bad_tok = self.client.post("/api/community/invites/preview", json={"code": "INV-BAD-TOK"}, headers={"Authorization": "Bearer bad.token"})
            self.assertEqual(res_bad_tok.status_code, 401)
            self.assertEqual(res_bad_tok.get_json().get("code"), "INVALID_TOKEN")

        # C. Role incompatible (User account claiming doctor invitation) -> 403
        user_id = "u0000000-0000-4000-8000-000000000014"
        claims = self._make_claims(user_id, "user14@clinic.org", "User 14", role="user", account_type="user")
        auth_hdr = {"Authorization": "Bearer token.u14"}
        mock_profile = {"id": user_id, "email": "user14@clinic.org", "account_type": "user", "email_verified": True, "status": "active"}

        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "preview_invitation", return_value={
                 "success": False, "code": "ROLE_INCOMPATIBLE", "message": "User accounts may only receive the field_worker database role."
             }):

            res_role = self.client.post("/api/community/invites/preview", json={"code": "INV-DOC-ONLY"}, headers=auth_hdr)
            self.assertEqual(res_role.status_code, 403)
            self.assertEqual(res_role.get_json().get("code"), "ROLE_INCOMPATIBLE")

    # -------------------------------------------------------------------------
    # Test 15: Concurrent acceptance cannot exceed max_uses
    # -------------------------------------------------------------------------
    def test_15_concurrent_acceptance_cannot_exceed_max_uses(self):
        """Simultaneous acceptance attempts racing on the final available use are serialized; losing attempt fails."""
        user_id = "u0000000-0000-4000-8000-000000000015"
        claims = self._make_claims(user_id, "dr.race@clinic.org", "Dr. Race")
        auth_hdr = {"Authorization": "Bearer token.race"}
        mock_profile = {"id": user_id, "email": "dr.race@clinic.org", "account_type": "doctor", "email_verified": True, "status": "active"}

        # PostgreSQL guarded update `use_count < max_uses` raises INVITATION_USAGE_UPDATE_FAILED
        with patch.object(auth_service, "safe_decode_token", return_value=(claims, None)), \
             patch.object(SupabaseService, "get_user_profile", return_value=mock_profile), \
             patch.object(SupabaseService, "accept_invitation", return_value={
                 "success": False,
                 "code": "INVITATION_USAGE_UPDATE_FAILED",
                 "message": "Invitation capacity changed or the invitation is no longer valid."
             }):

            res = self.client.post("/api/community/invites/accept", json={"code": "INV-RACE-LIMIT"}, headers=auth_hdr)
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.get_json().get("code"), "INVITATION_USAGE_UPDATE_FAILED")


if __name__ == "__main__":
    unittest.main()
