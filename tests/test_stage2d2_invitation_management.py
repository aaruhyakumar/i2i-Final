"""I2I Phase 2D-2 - Supabase Invitation Management Test Suite

Tests all Phase 2D-2 invitation requirements:
1. Admin creating an invitation (HTTP 201, requires_admin_approval=False, created_by=admin_id).
2. Doctor member creating an invitation (HTTP 201, requires_admin_approval=True, created_by=doctor_id).
3. User / field worker being denied invitation creation (HTTP 403 INSUFFICIENT_ROLE).
4. Non-member being denied invitation creation (HTTP 403 COMMUNITY_ACCESS_DENIED).
5. Cross-community invitation isolation (cannot list or revoke across communities).
6. Admin viewing all invitations in community (GET /api/community/invites).
7. Doctor viewing only their own invitations in community.
8. User listing restrictions (HTTP 403 INSUFFICIENT_ROLE).
9. Admin revoking any invitation in their community (HTTP 200, is_revoked=True, no hard delete).
10. Doctor revoking their own invitation vs. denied revoking another's (HTTP 403 FORBIDDEN).
11. Expired, revoked, and exhausted status derivation from database fields.
12. Configurable maximum uses and expiry validation (HTTP 400 on invalid input).
13. Approval requirement derived strictly from creator role (client input ignored).
14. Privileged role prohibition (community_admin / super_admin blocked).
15. Unauthenticated and invalid token requests rejected (HTTP 401).
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


class TestStage2D2InvitationManagement(unittest.TestCase):
    """Focused backend tests for Phase 2D-2 Supabase Invitation Management."""

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
    def _make_claims(self, user_id, email, name, role="doctor", account_type="doctor"):
        raw = {
            "sub": user_id,
            "email": email,
            "user_metadata": {"full_name": name},
            "app_metadata": {"account_type": account_type, "role": role},
            "email_confirmed_at": "2026-11-15T00:00:00Z"
        }
        return {
            "sub": user_id,
            "user_id": user_id,
            "role": role,
            "account_type": account_type,
            "email": email,
            "name": name,
            "app_metadata": {"account_type": account_type, "role": role, "email_verified": True},
            "raw": raw
        }

    # -------------------------------------------------------------------------
    # Test 1: Admin creating an invitation
    # -------------------------------------------------------------------------
    def test_01_admin_creates_invitation(self):
        """Community Admin creates invitation -> HTTP 201, requires_admin_approval=False, created_by=admin_id."""
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

        mock_inv = {
            "id": "inv-0001",
            "code": "INV-DOC-TEST1234",
            "community_id": comm_id,
            "role_id": "doctor",
            "created_by": admin_id,
            "requires_admin_approval": False,
            "max_uses": 1,
            "use_count": 0,
            "is_revoked": False,
            "status": "active"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=admin_memberships), \
             patch.object(SupabaseService, "create_invitation", return_value={"success": True, "invitation": mock_inv}) as mock_create:

            res = self.client.post("/api/community/invites", json={
                "community_id": comm_id,
                "role": "doctor"
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 201)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            inv = data.get("invitation", {})
            self.assertFalse(inv.get("requires_admin_approval"))
            self.assertEqual(inv.get("created_by"), admin_id)
            self.assertEqual(inv.get("status"), "active")

            # Check that create_invitation was invoked with requires_admin_approval=False
            mock_create.assert_called_once()
            _, kwargs = mock_create.call_args
            self.assertEqual(kwargs.get("requires_admin_approval"), False)
            self.assertEqual(kwargs.get("created_by"), admin_id)
            self.assertEqual(kwargs.get("community_id"), comm_id)
            self.assertEqual(kwargs.get("role_id"), "doctor")

    # -------------------------------------------------------------------------
    # Test 2: Doctor member creating an invitation
    # -------------------------------------------------------------------------
    def test_02_doctor_member_creates_invitation(self):
        """Doctor member creates invitation -> HTTP 201, requires_admin_approval=True, created_by=doctor_id."""
        doc_id = "d0000000-0000-4000-8000-000000000002"
        doc_claims = self._make_claims(doc_id, "doc@clinic.org", "Dr. Bob")
        auth_hdr = {"Authorization": "Bearer token.doc"}
        comm_id = "c0000000-0000-4000-8000-000000000001"

        doc_memberships = [{
            "community_id": comm_id,
            "role": "doctor",
            "role_id": "doctor",
            "status": "active"
        }]

        mock_inv = {
            "id": "inv-0002",
            "code": "INV-WRK-TEST5678",
            "community_id": comm_id,
            "role_id": "field_worker",
            "created_by": doc_id,
            "requires_admin_approval": True,
            "max_uses": 1,
            "use_count": 0,
            "is_revoked": False,
            "status": "active"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=doc_memberships), \
             patch.object(SupabaseService, "create_invitation", return_value={"success": True, "invitation": mock_inv}) as mock_create:

            res = self.client.post("/api/community/invites", json={
                "community_id": comm_id,
                "role": "field_worker"
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 201)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            inv = data.get("invitation", {})
            self.assertTrue(inv.get("requires_admin_approval"))
            self.assertEqual(inv.get("created_by"), doc_id)

            # Check that create_invitation was invoked with requires_admin_approval=True
            mock_create.assert_called_once()
            _, kwargs = mock_create.call_args
            self.assertEqual(kwargs.get("requires_admin_approval"), True)
            self.assertEqual(kwargs.get("created_by"), doc_id)

    # -------------------------------------------------------------------------
    # Test 3: User / field worker denied invitation creation
    # -------------------------------------------------------------------------
    def test_03_user_denied_invitation_creation(self):
        """User / field worker cannot create invitations -> HTTP 403 INSUFFICIENT_ROLE."""
        user_id = "u0000000-0000-4000-8000-000000000003"
        user_claims = self._make_claims(user_id, "user@clinic.org", "Worker Carol", role="field_worker", account_type="user")
        auth_hdr = {"Authorization": "Bearer token.user"}
        comm_id = "c0000000-0000-4000-8000-000000000001"

        user_memberships = [{
            "community_id": comm_id,
            "role": "field_worker",
            "role_id": "field_worker",
            "status": "active"
        }]

        with patch.object(auth_service, "safe_decode_token", return_value=(user_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=user_memberships), \
             patch.object(SupabaseService, "create_invitation") as mock_create:

            res = self.client.post("/api/community/invites", json={"community_id": comm_id}, headers=auth_hdr)
            self.assertEqual(res.status_code, 403)
            data = res.get_json()
            self.assertEqual(data.get("code"), "INSUFFICIENT_ROLE")
            mock_create.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 4: Non-member denied invitation creation
    # -------------------------------------------------------------------------
    def test_04_non_member_denied_invitation_creation(self):
        """Doctor who does not belong to target community is denied -> HTTP 403 COMMUNITY_ACCESS_DENIED."""
        doc_id = "d0000000-0000-4000-8000-000000000004"
        doc_claims = self._make_claims(doc_id, "doc4@clinic.org", "Dr. Outsider")
        auth_hdr = {"Authorization": "Bearer token.doc4"}
        target_comm = "c_unauthorized_target"

        # Member only of c_own_clinic
        own_memberships = [{
            "community_id": "c_own_clinic",
            "role": "doctor",
            "role_id": "doctor",
            "status": "active"
        }]

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=own_memberships), \
             patch.object(SupabaseService, "create_invitation") as mock_create:

            res = self.client.post("/api/community/invites", json={"community_id": target_comm}, headers=auth_hdr)
            self.assertEqual(res.status_code, 403)
            data = res.get_json()
            self.assertEqual(data.get("code"), "COMMUNITY_ACCESS_DENIED")
            mock_create.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 5: Cross-community invitation isolation
    # -------------------------------------------------------------------------
    def test_05_cross_community_invitation_isolation(self):
        """Caller in Community A cannot list or revoke invitations in Community B."""
        doc_id = "d0000000-0000-4000-8000-000000000005"
        doc_claims = self._make_claims(doc_id, "doc5@clinic.org", "Dr. Five")
        auth_hdr = {"Authorization": "Bearer token.doc5"}

        # Member ONLY of comm_a
        memberships_a = [{
            "community_id": "comm_a_5",
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        mock_inv_b = {
            "id": "inv_b_001",
            "community_id": "comm_b_5",
            "created_by": "someone_else"
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships_a), \
             patch.object(SupabaseService, "get_invitation_by_id_or_code", return_value=mock_inv_b):

            # 1. Attempt to list invitations of Community B -> 403
            res_list = self.client.get("/api/community/invites?community_id=comm_b_5", headers=auth_hdr)
            self.assertEqual(res_list.status_code, 403)
            self.assertEqual(res_list.get_json().get("code"), "COMMUNITY_ACCESS_DENIED")

            # 2. Attempt to revoke an invitation of Community B -> 403
            res_revoke = self.client.delete("/api/community/invites/inv_b_001", headers=auth_hdr)
            self.assertEqual(res_revoke.status_code, 403)
            self.assertEqual(res_revoke.get_json().get("code"), "COMMUNITY_ACCESS_DENIED")

    # -------------------------------------------------------------------------
    # Test 6: Admin viewing all invitations
    # -------------------------------------------------------------------------
    def test_06_admin_views_all_invitations(self):
        """Community Admin views all invitations in their community, regardless of creator."""
        admin_id = "a0000000-0000-4000-8000-000000000006"
        admin_claims = self._make_claims(admin_id, "admin6@clinic.org", "Admin Six")
        auth_hdr = {"Authorization": "Bearer token.admin6"}
        comm_id = "comm_alpha_6"

        memberships = [{
            "community_id": comm_id,
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        all_invitations = [
            {"id": "inv1", "code": "INV-1", "community_id": comm_id, "created_by": admin_id, "status": "active"},
            {"id": "inv2", "code": "INV-2", "community_id": comm_id, "created_by": "doc_other", "status": "active"}
        ]

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships), \
             patch.object(SupabaseService, "list_invitations", return_value=all_invitations) as mock_list:

            res = self.client.get(f"/api/community/invites?community_id={comm_id}", headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(len(data.get("invites", [])), 2)
            # Admin list_invitations is called with created_by=None (view all)
            mock_list.assert_called_once_with(community_id=comm_id, created_by=None)

    # -------------------------------------------------------------------------
    # Test 7: Doctor viewing only their own invitations
    # -------------------------------------------------------------------------
    def test_07_doctor_views_only_own_invitations(self):
        """Doctor member views only invitations they created in their community."""
        doc_id = "d0000000-0000-4000-8000-000000000007"
        doc_claims = self._make_claims(doc_id, "doc7@clinic.org", "Dr. Seven")
        auth_hdr = {"Authorization": "Bearer token.doc7"}
        comm_id = "comm_alpha_7"

        memberships = [{
            "community_id": comm_id,
            "role": "doctor",
            "role_id": "doctor",
            "status": "active"
        }]

        doc_own_invitations = [
            {"id": "inv_own", "code": "INV-DOC-OWN", "community_id": comm_id, "created_by": doc_id, "status": "active"}
        ]

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships), \
             patch.object(SupabaseService, "list_invitations", return_value=doc_own_invitations) as mock_list:

            res = self.client.get(f"/api/community/invites?community_id={comm_id}", headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(len(data.get("invites", [])), 1)
            self.assertEqual(data["invites"][0]["created_by"], doc_id)
            # Doctor list_invitations is called with created_by=doc_id (scoped to own)
            mock_list.assert_called_once_with(community_id=comm_id, created_by=doc_id)

    # -------------------------------------------------------------------------
    # Test 8: User listing restrictions
    # -------------------------------------------------------------------------
    def test_08_user_listing_restrictions(self):
        """User / field worker cannot list invitations -> HTTP 403 INSUFFICIENT_ROLE."""
        user_id = "u0000000-0000-4000-8000-000000000008"
        user_claims = self._make_claims(user_id, "user8@clinic.org", "Worker Eight", role="field_worker", account_type="user")
        auth_hdr = {"Authorization": "Bearer token.user8"}
        comm_id = "comm_alpha_8"

        memberships = [{
            "community_id": comm_id,
            "role": "field_worker",
            "role_id": "field_worker",
            "status": "active"
        }]

        with patch.object(auth_service, "safe_decode_token", return_value=(user_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships):

            res = self.client.get(f"/api/community/invites?community_id={comm_id}", headers=auth_hdr)
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.get_json().get("code"), "INSUFFICIENT_ROLE")

    # -------------------------------------------------------------------------
    # Test 9: Admin revoking an invitation
    # -------------------------------------------------------------------------
    def test_09_admin_revokes_invitation(self):
        """Community Admin can revoke any invitation in their community (sets is_revoked, no hard delete)."""
        admin_id = "a0000000-0000-4000-8000-000000000009"
        admin_claims = self._make_claims(admin_id, "admin9@clinic.org", "Admin Nine")
        auth_hdr = {"Authorization": "Bearer token.admin9"}
        comm_id = "comm_alpha_9"

        memberships = [{
            "community_id": comm_id,
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        target_inv = {
            "id": "inv_to_revoke_9",
            "code": "INV-REV-9",
            "community_id": comm_id,
            "created_by": "doc_someone_else",
            "is_revoked": False
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships), \
             patch.object(SupabaseService, "get_invitation_by_id_or_code", return_value=target_inv), \
             patch.object(SupabaseService, "revoke_invitation", return_value=True) as mock_revoke:

            res = self.client.delete("/api/community/invites/inv_to_revoke_9", headers=auth_hdr)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertTrue(data.get("revoked"))
            mock_revoke.assert_called_once_with("inv_to_revoke_9")

    # -------------------------------------------------------------------------
    # Test 10: Doctor revoking their own invitation vs. unauthorized revocation
    # -------------------------------------------------------------------------
    def test_10_doctor_revoking_own_vs_unauthorized(self):
        """Doctor can revoke their own invitation, but is denied revoking another's."""
        doc_id = "d0000000-0000-4000-8000-000000000010"
        doc_claims = self._make_claims(doc_id, "doc10@clinic.org", "Dr. Ten")
        auth_hdr = {"Authorization": "Bearer token.doc10"}
        comm_id = "comm_alpha_10"

        memberships = [{
            "community_id": comm_id,
            "role": "doctor",
            "role_id": "doctor",
            "status": "active"
        }]

        own_inv = {
            "id": "inv_own_10",
            "code": "INV-OWN-10",
            "community_id": comm_id,
            "created_by": doc_id,
            "is_revoked": False
        }

        other_inv = {
            "id": "inv_other_10",
            "code": "INV-OTHER-10",
            "community_id": comm_id,
            "created_by": "another_doctor_id",
            "is_revoked": False
        }

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships):

            # 1. Doctor revokes their OWN invitation -> 200 OK
            with patch.object(SupabaseService, "get_invitation_by_id_or_code", return_value=own_inv), \
                 patch.object(SupabaseService, "revoke_invitation", return_value=True) as mock_revoke:
                res_own = self.client.delete("/api/community/invites/inv_own_10", headers=auth_hdr)
                self.assertEqual(res_own.status_code, 200)
                self.assertTrue(res_own.get_json().get("revoked"))
                mock_revoke.assert_called_once_with("inv_own_10")

            # 2. Doctor attempts to revoke ANOTHER doctor's invitation -> 403 FORBIDDEN
            with patch.object(SupabaseService, "get_invitation_by_id_or_code", return_value=other_inv), \
                 patch.object(SupabaseService, "revoke_invitation") as mock_revoke_other:
                res_other = self.client.delete("/api/community/invites/inv_other_10", headers=auth_hdr)
                self.assertEqual(res_other.status_code, 403)
                self.assertEqual(res_other.get_json().get("code"), "FORBIDDEN")
                mock_revoke_other.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 11: Expired, revoked, and exhausted status derivation
    # -------------------------------------------------------------------------
    def test_11_status_derivation(self):
        """Status is dynamically derived from database fields without adding a column."""
        now = datetime.now(timezone.utc)
        future_iso = (now + timedelta(days=2)).isoformat()
        past_iso = (now - timedelta(days=2)).isoformat()

        # 1. Active: not revoked, uses remain, future expiry
        inv_active = {"is_revoked": False, "max_uses": 1, "use_count": 0, "expires_at": future_iso}
        self.assertEqual(SupabaseService.derive_invitation_status(inv_active, now=now), "active")

        # 2. Revoked: is_revoked is True (takes highest precedence)
        inv_revoked = {"is_revoked": True, "max_uses": 5, "use_count": 0, "expires_at": future_iso}
        self.assertEqual(SupabaseService.derive_invitation_status(inv_revoked, now=now), "revoked")

        # 3. Exhausted: use_count >= max_uses
        inv_exhausted = {"is_revoked": False, "max_uses": 3, "use_count": 3, "expires_at": future_iso}
        self.assertEqual(SupabaseService.derive_invitation_status(inv_exhausted, now=now), "exhausted")

        # 4. Expired: expires_at in the past
        inv_expired = {"is_revoked": False, "max_uses": 5, "use_count": 1, "expires_at": past_iso}
        self.assertEqual(SupabaseService.derive_invitation_status(inv_expired, now=now), "expired")

    # -------------------------------------------------------------------------
    # Test 12: Configurable maximum uses and expiry validation
    # -------------------------------------------------------------------------
    def test_12_configurable_max_uses_and_expiry_validation(self):
        """Supports configurable max_uses and expires_at, and rejects invalid inputs with HTTP 400."""
        admin_id = "a0000000-0000-4000-8000-000000000012"
        admin_claims = self._make_claims(admin_id, "admin12@clinic.org", "Admin Twelve")
        auth_hdr = {"Authorization": "Bearer token.admin12"}
        comm_id = "comm_alpha_12"

        memberships = [{
            "community_id": comm_id,
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships):

            # 1. Invalid max_uses (zero or negative) -> 400 INVALID_MAX_USES
            res_bad_max = self.client.post("/api/community/invites", json={
                "community_id": comm_id,
                "max_uses": 0
            }, headers=auth_hdr)
            self.assertEqual(res_bad_max.status_code, 400)
            self.assertEqual(res_bad_max.get_json().get("code"), "INVALID_MAX_USES")

            # 2. Invalid max_uses (non-integer string) -> 400 INVALID_MAX_USES
            res_str_max = self.client.post("/api/community/invites", json={
                "community_id": comm_id,
                "max_uses": "invalid_number"
            }, headers=auth_hdr)
            self.assertEqual(res_str_max.status_code, 400)
            self.assertEqual(res_str_max.get_json().get("code"), "INVALID_MAX_USES")

            # 3. Invalid expiry (past date) -> 400 INVALID_EXPIRY
            res_past_exp = self.client.post("/api/community/invites", json={
                "community_id": comm_id,
                "expires_at": "2020-01-01T00:00:00Z"
            }, headers=auth_hdr)
            self.assertEqual(res_past_exp.status_code, 400)
            self.assertEqual(res_past_exp.get_json().get("code"), "INVALID_EXPIRY")

            # 4. Valid configurable max_uses and expires_hours -> 201 Created
            with patch.object(SupabaseService, "create_invitation", return_value={"success": True, "invitation": {
                "id": "inv_conf", "max_uses": 10, "code": "INV-DOC-CONF1234", "status": "active"
            }}) as mock_create:
                res_ok = self.client.post("/api/community/invites", json={
                    "community_id": comm_id,
                    "max_uses": 10,
                    "expires_hours": 48
                }, headers=auth_hdr)
                self.assertEqual(res_ok.status_code, 201)
                mock_create.assert_called_once()
                _, kwargs = mock_create.call_args
                self.assertEqual(kwargs.get("max_uses"), 10)

    # -------------------------------------------------------------------------
    # Test 13: Approval requirement derived strictly from creator role
    # -------------------------------------------------------------------------
    def test_13_approval_requirement_derived_from_creator_role(self):
        """Frontend cannot override requires_admin_approval; it is derived strictly from community role."""
        doc_id = "d0000000-0000-4000-8000-000000000013"
        doc_claims = self._make_claims(doc_id, "doc13@clinic.org", "Dr. Thirteen")
        auth_hdr = {"Authorization": "Bearer token.doc13"}
        comm_id = "comm_alpha_13"

        doc_memberships = [{
            "community_id": comm_id,
            "role": "doctor",
            "role_id": "doctor",
            "status": "active"
        }]

        with patch.object(auth_service, "safe_decode_token", return_value=(doc_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=doc_memberships), \
             patch.object(SupabaseService, "create_invitation", return_value={"success": True, "invitation": {"id": "i"}}) as mock_create:

            # Doctor attempts to set requires_admin_approval: false in payload
            res = self.client.post("/api/community/invites", json={
                "community_id": comm_id,
                "requires_admin_approval": False
            }, headers=auth_hdr)

            self.assertEqual(res.status_code, 201)
            # Server strictly enforced requires_admin_approval=True for doctor role!
            mock_create.assert_called_once()
            _, kwargs = mock_create.call_args
            self.assertEqual(kwargs.get("requires_admin_approval"), True)

    # -------------------------------------------------------------------------
    # Test 14: Privileged roles prohibited from invitations
    # -------------------------------------------------------------------------
    def test_14_privileged_roles_prohibited(self):
        """Invitations cannot grant community_admin or super_admin roles -> HTTP 400 PRIVILEGED_ROLE_PROHIBITED."""
        admin_id = "a0000000-0000-4000-8000-000000000014"
        admin_claims = self._make_claims(admin_id, "admin14@clinic.org", "Admin Fourteen")
        auth_hdr = {"Authorization": "Bearer token.admin14"}
        comm_id = "comm_alpha_14"

        memberships = [{
            "community_id": comm_id,
            "role": "community_admin",
            "role_id": "community_admin",
            "status": "active"
        }]

        with patch.object(auth_service, "safe_decode_token", return_value=(admin_claims, None)), \
             patch.object(SupabaseService, "get_user_community_memberships", return_value=memberships):

            for bad_role in ["community_admin", "super_admin", "admin"]:
                res = self.client.post("/api/community/invites", json={
                    "community_id": comm_id,
                    "role": bad_role
                }, headers=auth_hdr)
                self.assertEqual(res.status_code, 400, f"Role {bad_role} must be rejected")
                self.assertEqual(res.get_json().get("code"), "PRIVILEGED_ROLE_PROHIBITED")

    # -------------------------------------------------------------------------
    # Test 15: Unauthenticated and invalid token requests rejected
    # -------------------------------------------------------------------------
    def test_15_unauthenticated_and_invalid_tokens_rejected(self):
        """Missing token -> 401 MISSING_TOKEN; invalid token -> 401 INVALID_TOKEN."""
        # 1. Missing token
        res_create = self.client.post("/api/community/invites", json={"community_id": "comm_1"})
        self.assertEqual(res_create.status_code, 401)
        self.assertEqual(res_create.get_json().get("code"), "MISSING_TOKEN")

        res_list = self.client.get("/api/community/invites?community_id=comm_1")
        self.assertEqual(res_list.status_code, 401)
        self.assertEqual(res_list.get_json().get("code"), "MISSING_TOKEN")

        res_del = self.client.delete("/api/community/invites/inv_1")
        self.assertEqual(res_del.status_code, 401)
        self.assertEqual(res_del.get_json().get("code"), "MISSING_TOKEN")

        # 2. Invalid token
        bad_hdr = {"Authorization": "Bearer garbage.jwt.token"}
        with patch.object(auth_service, "safe_decode_token", return_value=(None, "Signature expired")):
            res_bad = self.client.post("/api/community/invites", json={"community_id": "comm_1"}, headers=bad_hdr)
            self.assertEqual(res_bad.status_code, 401)
            self.assertEqual(res_bad.get_json().get("code"), "INVALID_TOKEN")


if __name__ == "__main__":
    unittest.main()
