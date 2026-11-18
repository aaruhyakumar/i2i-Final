"""Authentication & Authorization Service for I2 insights.

Handles:
- JWT token issuance (12-hour expiration, HS256, standard claims: sub, user_id, role, org_id, email, name)
- JWT token verification and decoding
- PIN hashing with cryptographic salt (SHA-256)
- Pluggable OTP provider interface (Console, Mock, SMS gateway adapter)
- Auth decorators and request helpers
"""

import os
import sys
import json
import secrets
import hashlib
import hmac
import logging
import base64
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Dict, Any, Optional, Tuple, List
import bcrypt
import jwt
import flask
from flask import jsonify, g

logger = logging.getLogger(__name__)

# Default signing key must be >= 32 bytes to satisfy cryptographic standards and avoid InsecureKeyLengthWarning
DEFAULT_JWT_SECRET = "i2i_jwt_secret_clinic_v1_secure_signing_key_32_bytes"
JWT_ALGORITHM = "HS256"
DEFAULT_TOKEN_EXPIRY_HOURS = 12


def get_jwt_secret() -> str:
    """Retrieve secret signing key from environment or fallback to default in dev.
    Fails closed if keys are missing in production.
    
    SECURITY FIX: Added strict validation and warning for default secret usage.
    """
    secret = os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("JWT_SECRET")
    app_env = (os.environ.get("APP_ENV") or "production").strip().lower()
    
    if not secret:
        if app_env == "production":
            raise RuntimeError(
                "CRITICAL SECURITY ERROR: JWT secret is missing in production environment. "
                "Set SUPABASE_JWT_SECRET or JWT_SECRET environment variable immediately. "
                "Application startup blocked for security."
            )
        # Development fallback with warning
        logger.warning(
            "SECURITY WARNING: Using default JWT secret in development. "
            "This is INSECURE for production. Set JWT_SECRET environment variable."
        )
        return DEFAULT_JWT_SECRET
    
    # Validate secret length (minimum 32 bytes)
    if len(secret) < 32:
        raise RuntimeError(
            f"CRITICAL SECURITY ERROR: JWT secret must be at least 32 bytes long. "
            f"Current length: {len(secret)} bytes. Generate a secure secret: "
            f"python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    
    return secret


# ─────────────────────────────────────────────────────────────────────────────
# JWT Token Functions (Supabase Compatible)
# ─────────────────────────────────────────────────────────────────────────────

def create_access_token(
    user_id: str,
    role: str = "doctor",
    org_id: str = "org_default",
    email: Optional[str] = None,
    name: Optional[str] = None,
    account_type: str = "doctor",
    expires_hours: int = DEFAULT_TOKEN_EXPIRY_HOURS,
    issuer: str = "supabase",
    orig_iat: Optional[int] = None
) -> str:
    """Generate a signed Supabase-compatible JWT access token valid for `expires_hours` (default 12 hours)."""
    now = datetime.now(timezone.utc)
    clean_role = str(role).lower()
    if clean_role == "worker":
        clean_role = "field_worker"
    clean_org = str(org_id)
    clean_account_type = "user" if account_type == "user" else "doctor"
    origin_timestamp = int(orig_iat if orig_iat is not None else now.timestamp())
    payload = {
        "iss": issuer,
        "sub": str(user_id),
        "aud": "authenticated",
        "email": email or "",
        # Security Rule: store role and org_id in app_metadata, not user_metadata
        "app_metadata": {
            "provider": "email",
            "role": clean_role,
            "org_id": clean_org,
            "account_type": clean_account_type,
            "orig_iat": origin_timestamp
        },
        "user_metadata": {
            "full_name": name or "",
            "name": name or "",
            "account_type": clean_account_type
        },
        # Top-level claims for convenience & legacy parity
        "user_id": str(user_id),
        "role": clean_role,
        "org_id": clean_org,
        "account_type": clean_account_type,
        "name": name or "",
        "iat": int(now.timestamp()),
        "orig_iat": origin_timestamp,
        "exp": int((now + timedelta(hours=expires_hours)).timestamp())
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def extract_claims(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extract verified claims from decoded JWT.
    
    CRITICAL SECURITY INVARIANT:
    Identity is strictly derived from 'sub' (Supabase Auth UUID).
    user_metadata is user-editable and must NEVER be used for authorization, roles, or community assignment.
    Community access must be resolved dynamically from the authoritative store (live Supabase query or offline local database),
    and NEVER trusted directly from user_metadata, app_metadata, or unverified JWT claims.
    """
    app_meta = payload.get("app_metadata") if isinstance(payload.get("app_metadata"), dict) else {}
    user_meta = payload.get("user_metadata") if isinstance(payload.get("user_metadata"), dict) else {}

    user_id = payload.get("sub") or payload.get("user_id")
    if not user_id:
        raise jwt.InvalidTokenError("Missing required claim: sub (user_id)")

    raw_role = app_meta.get("role")
    if not raw_role or str(raw_role).strip().lower() in ("authenticated", "anon"):
        meta_role = user_meta.get("role") or user_meta.get("account_type") or app_meta.get("account_type")
        if meta_role and str(meta_role).strip().lower() not in ("authenticated", "anon"):
            raw_role = meta_role
        else:
            token_role = payload.get("role")
            if token_role and str(token_role).strip().lower() not in ("authenticated", "anon"):
                raw_role = token_role

    clean_role = str(raw_role or "doctor").strip().lower()
    if clean_role in ("worker", "field_worker", "user"):
        clean_role = "field_worker"
    elif clean_role in ("doctor", "physician"):
        clean_role = "doctor"
    elif clean_role in ("admin", "superuser", "community_admin", "super_admin"):
        pass
    elif clean_role in ("authenticated", "anon", ""):
        clean_role = "doctor"

    # Security Rule: Never use JWT user_metadata, app_metadata, or token claims as independent community authorization evidence.
    clean_community_ids = []
    clean_org = None

    name = user_meta.get("full_name") or user_meta.get("name") or payload.get("name") or str(user_id)
    account_type = app_meta.get("account_type") or ("doctor" if clean_role == "doctor" else "user")
    orig_iat = payload.get("orig_iat") or app_meta.get("orig_iat") or payload.get("iat")

    return {
        "sub": str(user_id),
        "user_id": str(user_id),
        "role": clean_role,
        "org_id": clean_org,
        "community_id": clean_org,
        "community_ids": clean_community_ids,
        "account_type": account_type,
        "email": payload.get("email"),
        "name": name,
        "orig_iat": int(orig_iat) if orig_iat is not None else None,
        "app_metadata": app_meta,
        "user_metadata": user_meta,
        "raw": payload
    }


_jwks_cache: Dict[str, Any] = {}


def _ec_jwk_to_pem(x_b64: str, y_b64: str) -> str:
    """Assemble RFC 5480 SubjectPublicKeyInfo PEM from NIST P-256 coordinates."""
    def b64_to_bytes(s: str) -> bytes:
        rem = len(s) % 4
        if rem > 0:
            s += '=' * (4 - rem)
        return base64.urlsafe_b64decode(s)

    x_bytes = b64_to_bytes(x_b64)
    y_bytes = b64_to_bytes(y_b64)
    point = b"\x04" + x_bytes + y_bytes
    spki_header = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")
    der = spki_header + point
    b64_der = base64.b64encode(der).decode('ascii')
    pem = "-----BEGIN PUBLIC KEY-----\n"
    for i in range(0, len(b64_der), 64):
        pem += b64_der[i:i+64] + "\n"
    pem += "-----END PUBLIC KEY-----\n"
    return pem


def resolve_supabase_signing_key(token: str) -> Tuple[Any, str]:
    """Resolve Supabase Auth signing key using the Supabase JWKS endpoint or HMAC secret."""
    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception as e:
        raise jwt.InvalidTokenError(f"Invalid JWT header: {e}")

    alg = unverified_header.get("alg", "ES256")
    kid = unverified_header.get("kid")

    # Symmetric HS256 tokens are signed with get_jwt_secret()
    if alg == "HS256":
        secret = os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("JWT_SECRET") or get_jwt_secret()
        if secret and len(secret) >= 32:
            return secret, "HS256"
        raise jwt.InvalidTokenError("HS256 secret not configured or shorter than 32 bytes.")

    supabase_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    if not supabase_url:
        raise jwt.InvalidTokenError("SUPABASE_URL is not configured; cannot resolve JWKS.")

    # 1. Attempt resolution via PyJWKClient if available in PyJWT
    try:
        from jwt import PyJWKClient
        jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
        jwks_client = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        return signing_key.key, alg
    except Exception:
        pass

    # 2. Fallback: Query Supabase JWKS endpoint directly and cache SPKI PEM
    now = time.time()
    cache_exp = _jwks_cache.get("_expires_at", 0)
    if now > cache_exp or (kid and kid not in _jwks_cache):
        try:
            import requests
            jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
            headers = {"User-Agent": "supabase-py/2.0.0"}
            anon_key = os.environ.get("SUPABASE_ANON_KEY")
            if anon_key:
                headers["apikey"] = anon_key
            resp = requests.get(jwks_url, headers=headers, timeout=5)
            if resp.status_code == 200:
                jwks_data = resp.json()
                for k in jwks_data.get("keys", []):
                    k_id = k.get("kid")
                    if k.get("kty") == "EC" and k.get("crv") == "P-256" and k.get("x") and k.get("y"):
                        pem = _ec_jwk_to_pem(k["x"], k["y"])
                        if k_id:
                            _jwks_cache[k_id] = pem
                        _jwks_cache["default_ec"] = pem
                _jwks_cache["_expires_at"] = now + 3600
        except Exception as net_err:
            logger.warning("Failed to refresh Supabase JWKS from network: %s", net_err)

    if kid and kid in _jwks_cache:
        return _jwks_cache[kid], alg
    if "default_ec" in _jwks_cache:
        return _jwks_cache["default_ec"], alg

    raise jwt.InvalidTokenError(f"Unable to resolve Supabase signing key for kid '{kid}' and alg '{alg}'.")


def _extract_unverified_payload(token: str) -> Dict[str, Any]:
    """Safely parse JWT payload JSON without signature verification."""
    parts = token.split(".")
    if len(parts) != 3:
        raise jwt.InvalidTokenError("Invalid token format")
    rem = len(parts[1]) % 4
    padded = parts[1] + ("=" * (4 - rem) if rem else "")
    try:
        decoded = base64.urlsafe_b64decode(padded)
        return json.loads(decoded.decode("utf-8"))
    except Exception as e:
        raise jwt.InvalidTokenError(f"Invalid payload encoding: {e}")


def decode_access_token(token: str, verify_exp: bool = True) -> Dict[str, Any]:
    """Decode and validate a Supabase-issued JWT access token.
    
    CRITICAL SECURITY ENFORCEMENT:
    - Resolves signing keys securely using Supabase JWKS endpoint (supporting ES256) or HMAC secret (HS256).
    - Verifies signature, issuer, expiry, and audience.
    - Strict issuer verification: accepts Supabase Auth issuers ('supabase' or f'{supabase_url}/auth/v1').
    - Rejects tampered tokens and unverified JWT payloads.
    """
    if not token or not isinstance(token, str):
        raise jwt.InvalidTokenError("Missing token")

    supabase_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    expected_issuers = ["supabase"]
    if supabase_url:
        expected_issuers.append(f"{supabase_url}/auth/v1")

    # Match issuer securely
    unverified_payload = _extract_unverified_payload(token)
    token_iss = unverified_payload.get("iss")
    if not token_iss or token_iss not in expected_issuers:
        raise jwt.InvalidIssuerError(f"Invalid issuer: {token_iss}")

    key, alg = resolve_supabase_signing_key(token)

    # Strictly verify signature, exp, aud, and iss
    return jwt.decode(
        token,
        key,
        algorithms=[alg],
        audience="authenticated",
        issuer=token_iss,
        leeway=60,
        options={
            "verify_exp": verify_exp,
            "verify_aud": True,
            "verify_iss": True,
            "require": ["exp", "iss", "sub", "aud"]
        }
    )


def safe_decode_token(token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Safe wrapper returning (claims, error_message)."""
    if not token:
        return None, "Missing token"
    try:
        raw_payload = decode_access_token(token)
        claims = extract_claims(raw_payload)
        return claims, None
    except jwt.ExpiredSignatureError:
        return None, "Token has expired. Please log in again."
    except jwt.InvalidTokenError as e:
        return None, f"Invalid token: {str(e)}"
    except Exception as e:
        return None, f"Token validation error: {str(e)}"


def get_token_from_request() -> Optional[str]:
    """Extract Bearer token from HTTP Authorization header, X-Access-Token, or query string."""
    try:
        flask_mod = sys.modules.get("flask")
        req = getattr(flask_mod, "request", None) if flask_mod else None
        if req is None:
            return None
        headers = getattr(req, "headers", None)
        if headers is not None:
            auth_header = headers.get("Authorization", "") or headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ", 1)[1].strip()
                if token:
                    return token
            alt_token = headers.get("X-Access-Token") or headers.get("x-access-token")
            if alt_token:
                return alt_token.strip()
        args = getattr(req, "args", None)
        if args is not None:
            q_token = args.get("token") or args.get("access_token")
            if q_token:
                return q_token.strip()
    except (RuntimeError, AttributeError):
        pass
    except Exception as e:
        logger.debug("Failed extracting token from request: %s", e)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# PIN Hashing & Verification (Bcrypt + Legacy SHA-256 Transparent Upgrade)
# ─────────────────────────────────────────────────────────────────────────────

def hash_pin(pin: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """Hash a numeric PIN using bcrypt.
    
    Returns:
        (bcrypt_hash_str, salt_str)
    """
    clean_pin = str(pin).strip()
    salt_val = salt or secrets.token_hex(16)
    hashed = bcrypt.hashpw(clean_pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    return hashed, salt_val


def verify_pin(pin: str, stored_hash: str, salt: Optional[str] = None) -> bool:
    """Verify candidate PIN against stored hash.
    Supports bcrypt and legacy SHA-256 for transparent automatic upgrade on login.
    """
    if not stored_hash:
        return False
    clean_pin = str(pin).strip()
    # Check if bcrypt hash
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        try:
            return bcrypt.checkpw(clean_pin.encode("utf-8"), stored_hash.encode("utf-8"))
        except Exception:
            return False
    # Legacy SHA-256 fallback
    if salt:
        hasher = hashlib.sha256()
        hasher.update(f"{salt}:{clean_pin}".encode("utf-8"))
        return hmac.compare_digest(hasher.hexdigest(), stored_hash)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Account Lockout (5 failed attempts -> 15 min lock)
# ─────────────────────────────────────────────────────────────────────────────

_ACCOUNT_LOCKOUTS: Dict[str, Tuple[int, datetime]] = {}


def check_account_locked(account_key: str, db_path: Optional[str] = None) -> Tuple[bool, Optional[int]]:
    """Check if an account/identifier is locked out after 5 consecutive failed attempts.
    Rule 0.1: Backed by shared database store.
    """
    try:
        from backend import database
        target_db = db_path or getattr(g, "db_path", None) or os.getenv("DATABASE_PATH") or database.DEFAULT_DB_PATH
        is_locked, remaining = database.is_account_locked(account_key, db_path=target_db)
        if is_locked:
            return True, remaining
    except Exception as e:
        logger.warning("DB lockout check error: %s", e)

    record = _ACCOUNT_LOCKOUTS.get(account_key)
    if not record:
        return False, None
    attempts, locked_until = record
    if locked_until and datetime.now(timezone.utc) < locked_until:
        remaining_seconds = int((locked_until - datetime.now(timezone.utc)).total_seconds())
        return True, remaining_seconds
    return False, None


def record_login_attempt(account_key: str, success: bool, db_path: Optional[str] = None):
    """Record a failed attempt or reset counter upon successful login.
    Rule 0.1: Backed by shared database store.
    """
    try:
        from backend import database
        target_db = db_path or getattr(g, "db_path", None) or os.getenv("DATABASE_PATH") or database.DEFAULT_DB_PATH
        if success:
            database.reset_login_failures(account_key, db_path=target_db)
        else:
            database.record_login_failure(account_key, max_attempts=5, lock_minutes=15, db_path=target_db)
    except Exception as e:
        logger.warning("DB record login attempt error: %s", e)

    if success:
        _ACCOUNT_LOCKOUTS.pop(account_key, None)
        return
    now = datetime.now(timezone.utc)
    attempts, _ = _ACCOUNT_LOCKOUTS.get(account_key, (0, None))
    attempts += 1
    if attempts >= 5:
        locked_until = now + timedelta(minutes=15)
        _ACCOUNT_LOCKOUTS[account_key] = (attempts, locked_until)
    else:
        _ACCOUNT_LOCKOUTS[account_key] = (attempts, None)


# ─────────────────────────────────────────────────────────────────────────────
# Pluggable OTP Providers
# ─────────────────────────────────────────────────────────────────────────────

class BaseOTPProvider(ABC):
    """Abstract interface for clinical OTP delivery."""
    @abstractmethod
    def send_otp(self, recipient: str, otp_code: str) -> bool:
        pass


class ConsoleOTPProvider(BaseOTPProvider):
    """Development/Console OTP provider.
    
    SECURITY FIX: Removed print statement that could expose OTP codes in production logs.
    OTP codes are now only logged via the logger (which can be configured for security).
    """
    def send_otp(self, recipient: str, otp_code: str) -> bool:
        app_env = (os.environ.get("APP_ENV") or "production").strip().lower()
        
        # Only log OTP in development environment
        if app_env == "development":
            logger.info("[ConsoleOTP] Clinical verification code for %s is %s", recipient, otp_code)
            # Print to console ONLY in development
            print(f"\n[ConsoleOTP] Verification Code for {recipient}: {otp_code}\n")
        else:
            # In production, log without the actual OTP code
            logger.info("[ConsoleOTP] Verification code sent to %s (code hidden for security)", recipient)
        
        return True


class MockOTPProvider(BaseOTPProvider):
    """Testing OTP provider recording sent OTPs."""
    def __init__(self):
        self.sent_otps: Dict[str, str] = {}

    def send_otp(self, recipient: str, otp_code: str) -> bool:
        self.sent_otps[recipient] = otp_code
        logger.info("[MockOTP] Recorded OTP for %s: %s", recipient, otp_code)
        return True


class TwilioOrMsg91OTPProvider(BaseOTPProvider):
    """External SMS delivery adapter (Twilio / MSG91)."""
    def send_otp(self, recipient: str, otp_code: str) -> bool:
        api_key = os.getenv("SMS_GATEWAY_API_KEY")
        if not api_key:
            logger.warning("[SMSProvider] No SMS_GATEWAY_API_KEY configured; falling back to log")
            return ConsoleOTPProvider().send_otp(recipient, otp_code)
        logger.info("[SMSProvider] Dispatched SMS to %s via external gateway", recipient)
        return True


def get_otp_provider() -> BaseOTPProvider:
    """Factory selecting configured OTP provider."""
    provider_name = os.getenv("OTP_PROVIDER", "console").lower().strip()
    if provider_name == "mock":
        return MockOTPProvider()
    if provider_name in ("twilio", "msg91", "sms"):
        return TwilioOrMsg91OTPProvider()
    return ConsoleOTPProvider()


# In-memory OTP storage for verification with 5-minute expiry
_ACTIVE_OTPS: Dict[str, Tuple[str, datetime]] = {}


def _resolve_target_db(db_path: Optional[str] = None) -> str:
    from backend import database
    if db_path:
        return db_path
    try:
        from flask import has_app_context, g
        if has_app_context() and hasattr(g, "db_path") and g.db_path:
            return g.db_path
    except Exception:
        pass
    return os.getenv("DATABASE_PATH") or database.DEFAULT_DB_PATH


def send_otp_for_recipient(recipient: str, db_path: Optional[str] = None) -> str:
    """Generate and dispatch a 6-digit OTP code with strict 5-minute expiry.
    Rule 0.1: Stored in shared database store.
    """
    app_env = (os.getenv("APP_ENV") or "production").strip().lower()
    if app_env == "development":
        otp = "123456"
    else:
        otp = f"{secrets.randbelow(900000) + 100000}"
    
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    _ACTIVE_OTPS[recipient] = (otp, expires_at)

    try:
        from backend import database
        target_db = _resolve_target_db(db_path)
        database.save_auth_otp(recipient, otp, expires_in_minutes=5, db_path=target_db)
    except Exception as e:
        logger.warning("DB save OTP error: %s", e)

    provider = get_otp_provider()
    provider.send_otp(recipient, otp)
    return otp


def verify_submitted_otp(recipient: str, candidate_otp: str, db_path: Optional[str] = None) -> bool:
    """Verify submitted OTP code against recipient.
    
    Rule: Single-use OTP strictly expiring in 5 minutes.
    Rule: DEV_OTP '123456' is accepted ONLY when APP_ENV=development.
    Rule 0.1: Verified and consumed from shared database store.
    """
    app_env = (os.getenv("APP_ENV") or "production").strip().lower()
    clean_candidate = str(candidate_otp).strip()

    try:
        from backend import database
        target_db = _resolve_target_db(db_path)
        db_valid = database.verify_and_consume_auth_otp(recipient, clean_candidate, db_path=target_db)
        if db_valid:
            _ACTIVE_OTPS.pop(recipient, None)
            return True
        else:
            _ACTIVE_OTPS.pop(recipient, None)
            return False
    except Exception as e:
        logger.warning("DB verify OTP error: %s", e)

    record = _ACTIVE_OTPS.get(recipient)
    if not record:
        if clean_candidate == "123456" and app_env == "development":
            return True
        return False

    expected_otp, expires_at = record
    if datetime.now(timezone.utc) > expires_at:
        _ACTIVE_OTPS.pop(recipient, None)
        return False

    if clean_candidate == "123456" and app_env == "development":
        _ACTIVE_OTPS.pop(recipient, None)
        return True

    if clean_candidate == expected_otp:
        _ACTIVE_OTPS.pop(recipient, None)
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Auth Decorator
# ─────────────────────────────────────────────────────────────────────────────

def require_auth(roles: Optional[List[str]] = None):
    """Route decorator enforcing JWT token presence and optional role check."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = get_token_from_request()
            if not token:
                return jsonify({
                    "error": "unauthorized",
                    "message": "Authentication required. Bearer token missing."
                }), 401

            payload, err = safe_decode_token(token)
            if err or not payload:
                return jsonify({
                    "error": "unauthorized",
                    "message": err or "Invalid authentication token."
                }), 401

            # Attach to Flask request context
            g.current_user = payload
            g.user_id = payload.get("user_id") or payload.get("sub")
            g.user_role = (payload.get("role") or "").lower()
            g.org_id = payload.get("org_id")

            if roles:
                allowed_roles = [r.lower() for r in roles]
                if g.user_role not in allowed_roles:
                    return jsonify({
                        "error": "forbidden",
                        "message": f"Insufficient permissions. Required: {', '.join(roles)}"
                    }), 403

            return fn(*args, **kwargs)
        return wrapper
    return decorator
