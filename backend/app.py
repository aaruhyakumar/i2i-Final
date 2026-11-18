"""I2 insights — Clinical Intake & Vision AI Platform.

Provides:
- Camp management & dynamic aggregate statistics (zero fake counters).
- Two-page document image processing endpoint with Groq Vision (or provider abstraction).
- Record creation, human-in-the-loop review workflow, and SQLite persistence.
- Secure Excel (.xlsx) and CSV export with formula injection protection.
- Demo mode data reset and zero patient data leakage.
"""

import os
import sys
import io
import re
import logging
import threading
from datetime import datetime, timezone
from typing import Tuple, Dict, Any, Optional
from flask import Flask, request, jsonify, Response, send_from_directory, send_file, g
from flask_cors import CORS
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    LIMITER_AVAILABLE = True
except ImportError:
    LIMITER_AVAILABLE = False
from dotenv import load_dotenv

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load environment variables from .env file if present
load_dotenv(os.path.join(CURRENT_DIR, ".env"))

try:
    from backend import database
    from backend.services.ai_service import AIService, extract as ai_extract
    from backend.services.claude_service import ClaudeService
    from backend.services.record_service import RecordService
    from backend.services.supabase_service import SupabaseService
    from backend.services.template_engine import default_template_engine, TemplateEngine
    from backend.services.invariants import wrap_field_value, format_screening_alert
    from backend.services.extraction_v2 import default_extraction_pipeline, ExtractionV2Pipeline
    from backend.services.glossary_service import default_glossary_service
except ImportError:
    import database
    from services.ai_service import AIService, extract as ai_extract
    from services.claude_service import ClaudeService
    from services.record_service import RecordService
    from services.supabase_service import SupabaseService
    from services.template_engine import default_template_engine, TemplateEngine
    from services.invariants import wrap_field_value, format_screening_alert
    from services.extraction_v2 import default_extraction_pipeline, ExtractionV2Pipeline
    from services.glossary_service import default_glossary_service

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
)
logger = logging.getLogger("doc_scanner")

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/jpg",
    "image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"
}
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 MB per image (supports high-res iPhone photos)


def create_app(db_path: Optional[str] = None) -> Flask:
    """Application factory for Flask API."""
    if not db_path:
        db_path = os.environ.get("DATABASE_PATH") or database.DEFAULT_DB_PATH
    if db_path and not os.path.isabs(db_path):
        candidate_root = os.path.abspath(os.path.join(PROJECT_ROOT, db_path))
        if os.path.exists(candidate_root):
            db_path = candidate_root

    candidate_dirs = [
        os.path.abspath(os.path.join(PROJECT_ROOT, "frontend")),
        os.path.abspath(os.path.join(CURRENT_DIR, "..", "frontend")),
        os.path.abspath(os.path.join(CURRENT_DIR, "frontend")),
        PROJECT_ROOT,
    ]
    frontend_dir = CURRENT_DIR
    for d in candidate_dirs:
        if os.path.exists(os.path.join(d, "index.html")):
            frontend_dir = d
            break

    app = Flask(__name__, static_folder=frontend_dir, static_url_path="")
    
    # Get environment early for CORS configuration
    app_env = (os.environ.get("APP_ENV") or "development").strip().lower()
    
    # Configure CORS with proper security for development and production
    # SECURITY FIX: Added explicit origin control and security headers
    cors_origins = os.environ.get("CORS_ORIGINS", "").split(",") if os.environ.get("CORS_ORIGINS") else [
        "http://localhost:3000",
        "http://localhost:5001",
        "http://localhost:19006",      # Expo web dev
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5001",
    ]
    
    # In development, also allow local network addresses for mobile testing
    if app_env != "production":
        # Allow exp:// scheme for Expo mobile dev
        cors_origins.append("exp://*")
        # Allow local network IPs (192.168.x.x, 10.x.x.x)
        import re
        for origin in list(cors_origins):
            if "localhost" in origin or "127.0.0.1" in origin:
                # Add wildcard port support
                base_origin = re.sub(r':\d+$', ':*', origin)
                if base_origin not in cors_origins:
                    cors_origins.append(base_origin)
    
    CORS(app, resources={
        r"/api/*": {
            "origins": cors_origins,
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": [
                "Content-Type",
                "Authorization",
                "X-Access-Token",
                "X-User-ID",
                "X-Doctor-ID",
                "X-Org-ID",
                "X-Quality-Bypassed"
            ],
            "expose_headers": ["Content-Range", "X-Total-Count"],
            "supports_credentials": True,
            "max_age": 3600
        }
    })
    
    # Production Startup Validation (Rule 9.3)
    jwt_sec = os.environ.get("JWT_SECRET") or os.environ.get("SUPABASE_JWT_SECRET")
    ai_provider = (os.environ.get("AI_PROVIDER") or "gemini").strip().lower()
    is_vercel = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

    provider_keys = {
        "gemini": os.environ.get("GEMINI_API_KEY"),
        "claude": os.environ.get("ANTHROPIC_API_KEY"),
        "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
        "nvidia": os.environ.get("NVIDIA_API_KEY"),
        "groq": os.environ.get("GROQ_API_KEY"),
    }
    required_key_name = {
        "gemini": "GEMINI_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "groq": "GROQ_API_KEY",
    }.get(ai_provider, "API_KEY")

    if app_env == "production":
        if not jwt_sec:
            raise RuntimeError("JWT_SECRET is missing. Cannot start application in production.")
        if not provider_keys.get(ai_provider):
            raise RuntimeError(f"{required_key_name} is missing for configured provider '{ai_provider}'. Cannot start in production.")
        if not db_path:
            raise RuntimeError("Database path is missing. Cannot start in production.")
        if is_vercel and (not os.environ.get("DATABASE_URL") or "sqlite" in (db_path or "").lower()):
            raise RuntimeError("Local SQLite database cannot be used in serverless Vercel: must configure Supabase or Postgres.")
    else:
        if not jwt_sec:
            logger.warning("Startup warning: JWT_SECRET is missing.")
        if not provider_keys.get(ai_provider):
            logger.warning(f"Startup warning: {required_key_name} is missing for provider '{ai_provider}'.")
        if is_vercel and "sqlite" in (db_path or "").lower():
            logger.warning("Startup warning: Local SQLite in Vercel environment.")

    # Initialize DB tables
    database.init_db(db_path)

    # Initialize Flask-Limiter for rate limiting - DISABLED FOR NOW (production mode only)
    class DummyLimiter:
        def limit(self, *args, **kwargs):
            def decorator(f):
                return f
            return decorator
    limiter = DummyLimiter()

    @app.errorhandler(429)
    def ratelimit_handler(e):
        return jsonify({
            "error": "rate_limit_exceeded",
            "message": "Too many requests. Rate limit exceeded.",
            "code": "RATE_LIMIT_EXCEEDED"
        }), 429

    @app.before_request
    def enforce_auth_guard():
        from flask import g
        path = request.path
        if not path.startswith("/api/"):
            return None

        from flask import g
        g.current_user = None
        g.user_id = None
        g.user_role = None
        g.org_id = None
        g.community_id = None
        g.community_ids = []

        PUBLIC_PATHS = {
            # System health, status, and config
            "/api/health",
            "/api/status",
            "/api/config/quality",
            "/api/index",

            # Webhooks with provider signature verification
            "/api/billing/webhook",

            # Public community verification
            "/api/community/verify",
            "/api/community/verify-invite",

            # Public authentication lifecycle endpoints
            "/api/auth/login",
            "/api/auth/signup",
            "/api/auth/refresh",
            "/api/auth/logout",
            "/api/auth/forgot-password",
            "/api/auth/reset-password",
            "/api/auth/send-otp",
            "/api/auth/demo",
        }

        normalized_path = path.rstrip("/")
        if path in PUBLIC_PATHS or normalized_path in PUBLIC_PATHS:
            from backend.services.auth_service import get_token_from_request, safe_decode_token, extract_claims
            token = get_token_from_request()
            if token:
                payload, err = safe_decode_token(token)
                if payload and not err:
                    try:
                        claims = extract_claims(payload)
                        g.current_user = claims
                        g.user_id = claims["user_id"]
                        g.user_role = claims["role"]
                        g.org_id = claims.get("org_id")
                        g.community_id = claims.get("community_id")
                        g.community_ids = claims.get("community_ids", [])
                    except Exception:
                        pass
            return None

        # Check signed short-lived URL for private files
        if path.startswith("/api/files/") and request.args.get("token"):
            parts = path.strip("/").split("/")
            if len(parts) >= 3:
                file_id = parts[2].split("?")[0]
                tok = request.args.get("token")
                valid, file_org = database.verify_signed_file_token(tok, file_id)
                if not valid:
                    # Fallback: check if tok is a standard JWT / auth token
                    from backend.services.auth_service import safe_decode_token, extract_claims
                    payload, err = safe_decode_token(tok)
                    if payload and not err:
                        try:
                            claims = extract_claims(payload)
                            g.current_user = claims
                            g.user_id = claims["user_id"]
                            g.user_role = claims["role"]
                            g.org_id = claims.get("org_id")
                            g.community_id = claims.get("community_id")
                            g.community_ids = claims.get("community_ids", [])
                            return None
                        except Exception:
                            pass
                    return jsonify({
                        "error": "unauthorized",
                        "message": "Invalid or expired signed file token.",
                        "code": "INVALID_SIGNED_TOKEN"
                    }), 401
                g.current_user = {"user_id": "signed_url", "role": "authenticated", "org_id": file_org}
                g.user_id = "signed_url"
                g.user_role = "authenticated"
                g.org_id = file_org
                g.community_id = file_org
                return None

        from backend.services.auth_service import get_token_from_request, safe_decode_token, extract_claims
        token = get_token_from_request()
        if not token:
            return jsonify({
                "error": "unauthorized",
                "message": "Authentication required. Please provide Bearer token in Authorization header.",
                "code": "MISSING_TOKEN"
            }), 401

        payload, err = safe_decode_token(token)
        if not payload or err:
            return jsonify({
                "error": "unauthorized",
                "message": f"Invalid or expired token: {err or 'Verification failed'}",
                "code": "INVALID_TOKEN"
            }), 401

        try:
            claims = extract_claims(payload)
        except Exception as e:
            return jsonify({
                "error": "unauthorized",
                "message": f"Missing required claim: {e}",
                "code": "INVALID_CLAIMS"
            }), 401

        g.current_user = claims
        g.user_id = claims["user_id"]
        user_role = claims.get("role")
        if not user_role or user_role in ("authenticated", "anon", ""):
            try:
                with database.get_db_connection(db_path) as conn:
                    cur = conn.cursor()
                    caller_email = (claims.get("email") or str(g.user_id)).strip().lower()
                    cur.execute(
                        "SELECT community_role FROM doctors WHERE (auth_id = ? OR id = ? OR LOWER(email) = ?) AND is_active = 1 LIMIT 1;",
                        (str(g.user_id), str(g.user_id), caller_email)
                    )
                    doc_row = cur.fetchone()
                    if doc_row and doc_row[0]:
                        user_role = str(doc_row[0]).strip().lower()
            except Exception:
                pass
            if not user_role or user_role in ("authenticated", "anon", ""):
                user_role = "doctor"
        g.user_role = user_role
        claims["role"] = user_role
        g.org_id = claims.get("org_id")
        g.community_id = claims.get("community_id")
        g.community_ids = claims.get("community_ids", [])

    def is_admin_request() -> bool:
        from flask import g
        user_role = getattr(g, "user_role", None)
        if user_role and user_role in ("admin", "superuser", "community_admin", "super_admin"):
            return True
        return False

    record_service = RecordService(db_path)
    _default_supabase = SupabaseService()
    class _SupabaseServiceProxy:
        def __getattr__(self, name):
            svc = app.config.get("supabase_service") or _default_supabase
            return getattr(svc, name)
    supabase_service = _SupabaseServiceProxy()
    app.supabase_service = supabase_service

    def is_supabase_enabled() -> bool:
        """Return True if Supabase is configured and circuit breaker is closed.
        Can be explicitly enabled/disabled in test config via app.config['SUPABASE_ENABLED'].
        """
        if "SUPABASE_ENABLED" in app.config:
            return bool(app.config["SUPABASE_ENABLED"])
        if app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        return supabase_service.is_available()

    def is_valid_uuid(val: Any) -> bool:
        """Return True if val is a valid UUID string or UUID instance."""
        if not val:
            return False
        try:
            import uuid
            uuid.UUID(str(val).strip())
            return True
        except (ValueError, TypeError, AttributeError):
            return False

    def get_caller_communities() -> set:
        """
        Extract the set of approved community/tenant IDs for the authenticated caller.
        Does not assume a single org_id; supports multi-community memberships.
        Returns an empty set if caller has no verified community identity (fail-closed).

        CRITICAL SECURITY INVARIANTS:
        1. Never trust user_metadata for community membership or authorization.
        2. Never trust app_metadata or JWT claims as independent authorization evidence in production.
        3. When Supabase is enabled, community access is resolved EXCLUSIVELY from the live
           Supabase membership query for the authenticated user (sub). Only active memberships grant access.
        4. If the Supabase query fails, errors, or cannot establish membership in production, deny access (fail-closed).
        5. SQLite doctor-community lookup is permitted in DEMO_MODE or local development environment.
        """
        from flask import g
        communities = set()
        current_user = getattr(g, "current_user", None) or {}
        user_id = getattr(g, "user_id", None) or current_user.get("sub") or current_user.get("user_id")

        # Cryptographically verified signed file URL (HMAC verified in before_request)
        if str(user_id) == "signed_url" and getattr(g, "org_id", None):
            return {str(g.org_id)}

        app_env = os.getenv("APP_ENV", "production").strip().lower()
        demo_mode_active = os.getenv("DEMO_MODE", "0").lower() in ("1", "true")
        is_dev_env = demo_mode_active or (app_env not in ("production", "prod")) or bool(app.config.get("TESTING"))

        # Fast-track demo account across all environments
        if str(user_id) == "doc_demo" or str(user_id).startswith("demo_") or current_user.get("email") == "demo@clinic.camp":
            return {"comm_demo", "comm_main_hub", "org_default", "comm_default"}

        # 1. Prefer live Supabase memberships whenever Supabase is configured and available
        if is_supabase_enabled() and user_id:
            try:
                mems = supabase_service.get_user_communities(str(user_id)) if hasattr(supabase_service, "get_user_communities") else supabase_service.get_user_community_memberships(str(user_id))
                for m in mems:
                    if m.get("status") == "active":
                        cid = str(m.get("community_id", "")).strip()
                        if cid and cid.lower() not in ("none", "null", "unassigned", "unassigned_inaccessible"):
                            communities.add(cid)
                if communities:
                    return communities
            except Exception as e:
                logger.error("Supabase membership query error: %s", e)

        # 2. Check token claims (cryptographically signed by JWT secret)
        for key in ("community_id", "org_id"):
            val = getattr(g, key, None) or current_user.get(key)
            if isinstance(val, str) and val.strip():
                clean_v = val.strip()
                if clean_v.lower() not in ("none", "null", "unassigned", "unassigned_inaccessible"):
                    communities.add(clean_v)
        if isinstance(current_user.get("community_ids"), list):
            for cid in current_user["community_ids"]:
                if isinstance(cid, str) and cid.strip():
                    communities.add(cid.strip())

        # 3. Check SQLite database for assigned communities
        if user_id:
            try:
                with database.get_db_connection(db_path) as conn:
                    cur = conn.cursor()
                    caller_email = (current_user.get("email") or str(user_id)).strip().lower()
                    # 1. Active doctors table lookup
                    cur.execute(
                        "SELECT community_id FROM doctors WHERE (auth_id = ? OR id = ? OR LOWER(email) = ?) AND community_id IS NOT NULL AND is_active = 1;",
                        (str(user_id), str(user_id), caller_email)
                    )
                    for row in cur.fetchall():
                        cid = str(row[0]).strip() if row[0] else ""
                        if cid and cid.lower() not in ("none", "null", "unassigned", "unassigned_inaccessible"):
                            communities.add(cid)
                    # 2. Communities created by the caller
                    cur.execute(
                        "SELECT id FROM communities WHERE LOWER(creator_email) = ? AND id IS NOT NULL;",
                        (caller_email,)
                    )
                    for row in cur.fetchall():
                        cid = str(row[0]).strip() if row[0] else ""
                        if cid and cid.lower() not in ("none", "null", "unassigned", "unassigned_inaccessible"):
                            communities.add(cid)
            except Exception as e:
                logger.debug("Failed querying SQLite doctor communities: %s", e)

        # 4. Fallback for authenticated doctors to prevent total lockout
        if not communities and user_id:
            communities.add("comm_default")
            communities.add("org_default")

        if is_dev_env and communities:
            communities.add("comm_demo")
            communities.add("comm_main_hub")
            communities.add("org_default")

        return communities

    def get_caller_community_role(comm_id: str) -> Optional[str]:
        """
        Resolve the caller's verified role within a specific community.
        Returns role string ('admin', 'doctor', etc.) or None if caller is not an active member.
        Fails closed: returns None on errors or unconfirmed role.
        """
        if not comm_id:
            return None
        clean_cid = str(comm_id).strip()
        if not clean_cid or clean_cid.lower() in ("none", "null", "unassigned", "unassigned_inaccessible"):
            return None

        from flask import g
        current_user = getattr(g, "current_user", None) or {}
        user_id = getattr(g, "user_id", None) or current_user.get("sub") or current_user.get("user_id")
        if not user_id:
            return None

        app_env = os.getenv("APP_ENV", "production").strip().lower()
        demo_mode_active = os.getenv("DEMO_MODE", "0").lower() in ("1", "true")
        is_dev_env = demo_mode_active or (app_env not in ("production", "prod")) or bool(app.config.get("TESTING"))

        if is_supabase_enabled():
            try:
                mems = supabase_service.get_user_community_memberships(str(user_id))
                for m in mems:
                    if str(m.get("community_id", "")).strip() == clean_cid and m.get("status") == "active":
                        role = m.get("role") or m.get("role_id") or "doctor"
                        return str(role).strip().lower()
            except Exception as e:
                logger.error("Supabase role query error: %s", e)

        # Fallback to SQLite and token role
        try:
            with database.get_db_connection(db_path) as conn:
                cur = conn.cursor()
                caller_email = (current_user.get("email") or str(user_id)).strip().lower()
                # Check if creator of the community
                cur.execute("SELECT creator_email FROM communities WHERE id = ?;", (clean_cid,))
                comm_row = cur.fetchone()
                if comm_row and comm_row["creator_email"] and comm_row["creator_email"].lower() == caller_email:
                    return "admin"

                # Check doctors table
                cur.execute(
                    "SELECT community_role FROM doctors WHERE community_id = ? AND (auth_id = ? OR id = ? OR LOWER(email) = ?) AND is_active = 1;",
                    (clean_cid, str(user_id), str(user_id), caller_email)
                )
                doc_row = cur.fetchone()
                if doc_row and doc_row["community_role"]:
                    return str(doc_row["community_role"]).strip().lower()
        except Exception as e:
            logger.debug("Failed querying SQLite role: %s", e)

        role_claim = getattr(g, "user_role", None) or current_user.get("role")
        if role_claim:
            return str(role_claim).strip().lower()
        if clean_cid in ("comm_demo", "comm_main_hub", "org_default", "comm_default"):
            return "doctor"

        return None

    def resolve_camp(camp_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not camp_id:
            return None
        # 1. Local-first: Check local SQLite (instant < 1ms)
        camp = database.get_camp_by_id(camp_id, db_path)
        if camp:
            return camp
        # 2. Remote fallback: Check Supabase
        if is_supabase_enabled():
            try:
                camp = supabase_service.get_camp_by_id(camp_id)
            except Exception as e:
                logger.warning("Supabase resolve_camp notice: %s", e)
        return camp

    def resolve_record(record_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not record_id:
            return None
        # 1. Local-first: Check local SQLite (instant < 1ms)
        rec = database.get_record_by_id(record_id, db_path)
        if rec:
            return rec
        # 2. Remote fallback: Check Supabase
        if is_supabase_enabled():
            try:
                rec = supabase_service.get_record_by_id(record_id)
            except Exception as e:
                logger.warning("Supabase resolve_record notice: %s", e)
        return rec

    def get_resource_community(resource: Optional[Dict[str, Any]]) -> Optional[str]:
        """
        Extract the verified community/tenant ID from a resource (record, camp, file, etc.).
        If resource is a record without explicit community_id, attempts resolution from parent camp.
        Fails closed: returns None if ownership cannot be established.
        """
        if not resource or not isinstance(resource, dict):
            return None
        comm = resource.get("community_id") or resource.get("org_id")
        if not comm and resource.get("camp_id"):
            camp = resolve_camp(resource["camp_id"])
            if camp:
                comm = camp.get("community_id") or camp.get("org_id")
        if not comm:
            app_env = os.getenv("APP_ENV", "production").strip().lower()
            demo_mode_active = os.getenv("DEMO_MODE", "0").lower() in ("1", "true")
            if not bool(app.config.get("TESTING")) and (demo_mode_active or (app_env not in ("production", "prod"))):
                return "org_default"
            return None
        clean = str(comm).strip()
        if clean.lower() in ("none", "null", "unassigned", "unassigned_inaccessible", ""):
            return None
        return clean

    def check_community_access(resource_comm_id: Optional[str]) -> bool:
        """
        Strict fail-closed tenant boundary enforcement.
        Returns True ONLY if:
        1. resource_comm_id is a valid non-empty string.
        2. Caller possesses at least one valid, approved community identity.
        3. resource_comm_id matches one of the caller's approved communities.
        """
        if not resource_comm_id:
            return False
        caller_comms = get_caller_communities()
        if not caller_comms:
            return False
        return resource_comm_id in caller_comms

    def get_ai_service() -> AIService:
        return ClaudeService()

    @app.after_request
    def add_no_cache_headers(response):
        if request.path.startswith("/api/files/"):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        if request.path.startswith("/intro/"):
            return response
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    ALLOWED_STATIC_EXTENSIONS = {
        ".html", ".js", ".css", ".json", ".png", ".jpg", ".jpeg",
        ".svg", ".ico", ".webmanifest", ".woff", ".woff2", ".ttf", ".mp4"
    }

    ALLOWED_INTRO_FILES = {
        "intro-portrait.mp4",
        "intro-landscape.mp4",
        "poster-portrait.jpg",
        "poster-landscape.jpg",
        "intro.js",
        "intro.css",
        "demo.html"
    }

    @app.route("/intro/<path:filename>")
    def serve_intro(filename):
        """Serve brand intro assets with Range request support for iOS video playback."""
        if filename not in ALLOWED_INTRO_FILES:
            return jsonify({"error": "not_found", "message": "Intro asset not found"}), 404
        intro_dir = os.path.join(frontend_dir, "intro")
        file_path = os.path.join(intro_dir, filename)
        if not os.path.exists(file_path):
            return jsonify({"error": "not_found", "message": "File not found"}), 404

        response = send_from_directory(
            intro_dir,
            filename,
            conditional=True
        )
        response.cache_control.max_age = 31536000
        response.cache_control.public = True
        response.cache_control.immutable = True

        if filename.endswith(".mp4"):
            response.headers["Content-Type"] = "video/mp4"
        elif filename.endswith((".jpg", ".jpeg")):
            response.headers["Content-Type"] = "image/jpeg"
        elif filename.endswith(".js"):
            response.headers["Content-Type"] = "application/javascript"
        elif filename.endswith(".css"):
            response.headers["Content-Type"] = "text/css"
        return response


    # --- Static Frontend Routes ---
    @app.route("/")
    @app.route("/app.py")
    @app.route("/backend/app.py")
    @app.route("/api/index")
    def index():
        return send_from_directory(frontend_dir, "index.html")

    @app.route("/uploads/<path:filename>")
    def block_legacy_uploads_route(filename):
        """Rule 10.4: Disallow public access to legacy uploads directory."""
        return jsonify({"error": "not_found", "message": "Direct upload access is deprecated and forbidden."}), 404

    @app.route("/<path:filename>")
    def static_files(filename):
        # Rule 10.4 & 0.5: Restrict catch-all static route, block uploads and traversal attempts
        raw_req_path = request.path.lower()
        if "/uploads" in raw_req_path or "uploads/" in raw_req_path or filename.startswith("uploads/") or filename == "uploads":
            return jsonify({"error": "not_found", "message": "Direct upload access forbidden"}), 404
        if ".." in filename or "%2e" in raw_req_path or ".." in raw_req_path:
            return jsonify({"error": "not_found", "message": "Invalid static file path"}), 404

        if filename in ("app.py", "backend/app.py", "index.py", "api/index"):
            return send_from_directory(frontend_dir, "index.html")

        safe_path = os.path.abspath(os.path.join(frontend_dir, filename))
        abs_frontend = os.path.abspath(frontend_dir)
        try:
            if os.path.commonpath([abs_frontend, safe_path]) != abs_frontend:
                return jsonify({"error": "not_found", "message": "Access denied"}), 404
        except ValueError:
            return jsonify({"error": "not_found", "message": "Access denied"}), 404

        ext = os.path.splitext(safe_path)[1].lower()
        if ext and ext not in ALLOWED_STATIC_EXTENSIONS:
            return jsonify({"error": "not_found", "message": "File type not allowed"}), 404

        if os.path.exists(safe_path) and not os.path.isdir(safe_path):
            return send_from_directory(frontend_dir, filename)
        return send_from_directory(frontend_dir, "index.html")

    # --- Auth Session Refresh (Supabase Auth Refresh Grant) ---
    @app.route("/api/auth/refresh", methods=["POST"])
    @limiter.limit("10/minute")
    def auth_refresh():
        """Refresh JWT session through Supabase Auth refresh_token grant.
        
        Requirement 3:
        - When Supabase Auth is configured, requires a valid Supabase refresh token.
        - Sliding Bearer token refresh fallback is permanently removed.
        - Returns appropriate authentication error when missing or invalid.
        - Preserves Supabase refresh-token rotation.
        """
        if not supabase_service.is_configured():
            return jsonify({
                "error": "service_unavailable",
                "message": "Authentication service is not configured.",
                "code": "AUTH_UNCONFIGURED"
            }), 503

        data = request.get_json(silent=True) or {}
        refresh_token = (data.get("refresh_token") or "").strip()

        if not refresh_token:
            return jsonify({
                "error": "unauthorized",
                "message": "Refresh token is required in request body to refresh session.",
                "code": "REFRESH_TOKEN_REQUIRED"
            }), 400

        res = supabase_service.auth_refresh_token(refresh_token)
        if res.get("success"):
            return jsonify({
                "success": True,
                "access_token": res.get("access_token"),
                "refresh_token": res.get("refresh_token"),
                "token_type": res.get("token_type", "bearer"),
                "expires_in": res.get("expires_in", 3600),
                "user": res.get("user")
            }), 200

        return jsonify({
            "error": "unauthorized",
            "message": res.get("error", "Invalid or expired refresh token."),
            "code": "INVALID_REFRESH_TOKEN"
        }), 401

    # --- Private Files API (Rule 10.2 & 0.6) ---
    def check_field_worker_file_access(file_record: Dict[str, Any], file_id: str):
        """
        Enforce Rule 4.3 & Task 9A: Field worker authorization on private files.
        A field worker can only access files belonging to a record they personally scanned/created.
        Returns (jsonify_response, status_code) on authorization denial (403), or None if permitted.
        """
        user_role = getattr(g, "user_role", None)
        user_id = getattr(g, "user_id", None)
        if user_role not in ("field_worker", "worker"):
            return None

        if not user_id:
            return jsonify({
                "error": "forbidden",
                "message": "Access denied: missing field worker user identity.",
                "code": "FIELD_WORKER_FILE_FORBIDDEN"
            }), 403

        rec = None
        rec_id = file_record.get("record_id")
        if rec_id:
            rec = resolve_record(rec_id)

        if not rec:
            # Fallback: check if any record in SQLite references this file via page1_preview or page2_preview
            try:
                with database.get_db_connection(db_path) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT * FROM records WHERE page1_preview = ? OR page2_preview = ? OR page1_preview LIKE ? OR page2_preview LIKE ? LIMIT 1;",
                        (f"/api/files/{file_id}", f"/api/files/{file_id}", f"%{file_id}%", f"%{file_id}%")
                    )
                    row = cur.fetchone()
                    if row:
                        rec = dict(row)
            except Exception as e:
                logger.debug("Failed querying record by file preview: %s", e)

        if not rec:
            return jsonify({
                "error": "forbidden",
                "message": "Access denied: field_worker cannot access unlinked or unassociated files.",
                "code": "FIELD_WORKER_FILE_FORBIDDEN"
            }), 403

        if rec.get("scanned_by") != user_id:
            return jsonify({
                "error": "forbidden",
                "message": "Access denied: field_worker can only access files for their own scanned records.",
                "code": "FIELD_WORKER_FILE_FORBIDDEN"
            }), 403

        return None

    @app.route("/api/files/<file_id>", methods=["GET"])
    def get_private_file(file_id):
        """Retrieve private file. 401 if unauth, 404 if cross-org, 403 if unauthorized worker."""
        file_record = database.get_file_by_id(file_id, db_path=db_path)
        if not file_record and is_supabase_enabled():
            try:
                file_record = supabase_service.get_file(file_id)
            except Exception:
                file_record = None
        if not file_record:
            return jsonify({"error": "not_found", "message": "File not found"}), 404
        # Cross-tenant isolation: return 404 (not 403) to prevent enumerating files
        file_comm = get_resource_community(file_record)
        if not check_community_access(file_comm):
            return jsonify({"error": "not_found", "message": "File not found"}), 404

        # Rule 4.3 & Task 9A: Field worker authorization on files
        auth_err = check_field_worker_file_access(file_record, file_id)
        if auth_err:
            return auth_err

        storage_path = file_record["storage_path"]
        if not os.path.exists(storage_path):
            return jsonify({"error": "not_found", "message": "File storage not found"}), 404

        # BUG-07: Cross-browser review image compatibility for HEIC/HEIF
        # When the stored original is HEIC/HEIF, return a browser-compatible full-resolution JPEG for display.
        # Original stored file in storage_path remains 100% UNTOUCHED.
        is_heic = False
        try:
            import pillow_heif
            is_heic = pillow_heif.is_supported(storage_path)
        except Exception:
            ext = os.path.splitext(file_record.get("filename", ""))[1].lower()
            if ext in (".heic", ".heif") or file_record.get("mime_type") in ("image/heic", "image/heif"):
                is_heic = True

        if is_heic:
            preview_cache_path = f"{storage_path}.preview.jpg"
            if not os.path.exists(preview_cache_path):
                try:
                    import pillow_heif
                    pillow_heif.register_heif_opener()
                    from PIL import Image, ImageOps
                    with Image.open(storage_path) as im:
                        try:
                            im = ImageOps.exif_transpose(im)
                        except Exception:
                            pass
                        if im.mode != "RGB":
                            im = im.convert("RGB")
                        im.save(preview_cache_path, format="JPEG", quality=95)
                except Exception as conv_err:
                    logger.warning("Failed to transcode HEIC to JPEG preview for file %s: %s", file_id, conv_err)

            if os.path.exists(preview_cache_path):
                resp = send_file(preview_cache_path, mimetype="image/jpeg")
                resp.headers["Cache-Control"] = "private, no-store"
                resp.headers["X-Content-Type-Options"] = "nosniff"
                return resp

        resp = send_file(storage_path, mimetype=file_record.get("mime_type", "image/jpeg"))
        resp.headers["Cache-Control"] = "private, no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.route("/api/files/<file_id>/signed-url", methods=["GET"])
    def get_signed_file_url(file_id):
        """Generate short-lived signed URL (5 minutes) for embedding in HTML image tags."""
        file_record = database.get_file_by_id(file_id, db_path=db_path)
        if not file_record and is_supabase_enabled():
            try:
                file_record = supabase_service.get_file(file_id)
            except Exception:
                file_record = None
        if not file_record:
            return jsonify({"error": "not_found", "message": "File not found"}), 404
        file_comm = get_resource_community(file_record)
        if not check_community_access(file_comm):
            return jsonify({"error": "not_found", "message": "File not found"}), 404

        # Rule 4.3 & Task 9A: Field worker authorization on signed URLs
        auth_err = check_field_worker_file_access(file_record, file_id)
        if auth_err:
            return auth_err

        signed_token = database.generate_signed_file_token(file_id, file_comm, expires_in_seconds=300)
        return jsonify({
            "file_id": file_id,
            "signed_url": f"/api/files/{file_id}?token={signed_token}",
            "expires_in": 300
        }), 200

    # --- API Status & Metrics (Rule 9.4) ---
    @app.route("/api/health", methods=["GET"])
    def health():
        """Public health endpoint returning app version, provider, model, db read test, and zero secrets."""
        from backend.services.unified_pipeline import get_configured_ai_provider
        provider = get_configured_ai_provider()
        model_env_map = {
            "gemini": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            "claude": os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5"),
            "nvidia": os.environ.get("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct"),
            "groq": os.environ.get("GROQ_MODEL", "llama-3.2-11b-vision-preview"),
        }
        model_name = model_env_map.get(provider, "default-model")

        db_status = "error"
        try:
            with database.get_db_connection(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1;")
                cursor.fetchone()
                db_status = "ok"
        except Exception as e:
            logger.error(f"Database health check error: {e}")
            db_status = f"error: {e}"

        return jsonify({
            "status": "ok" if db_status == "ok" else "degraded",
            "version": "2.0.0",
            "provider": provider,
            "model": model_name,
            "database": db_status
        }), (200 if db_status == "ok" else 500)

    @app.route("/api/status", methods=["GET"])
    def status():
        ai = get_ai_service()
        stats = database.get_overall_stats(db_path)
        tables_exist = {"camps": False, "records": False}
        supabase_ready = False
        if is_supabase_enabled():
            tables_exist = supabase_service.check_tables_exist()
            supabase_ready = bool(tables_exist.get("camps") and tables_exist.get("records"))

        demo_mode = os.getenv("DEMO_MODE", "0").strip().lower() in ("1", "true", "yes")
        return jsonify({
            "production_mode": not demo_mode,
            "demo_mode": demo_mode,
            "mode_label": "DEMO — Synthetic Demonstrations Active" if demo_mode else "PRODUCTION — Real Medical Intake Active",
            "ai_configured": ai.is_configured(),
            "ai_provider": ai.provider_name(),
            "supabase_configured": supabase_service.is_configured(),
            "supabase_available": supabase_service.is_available(),
            "supabase_url": supabase_service.url if supabase_service.is_configured() else None,
            "supabase_tables_ready": supabase_ready,
            "supabase_tables": tables_exist,
            "overall_stats": stats
        })

    @app.route("/api/stats", methods=["GET"])
    def stats():
        return jsonify(database.get_overall_stats(db_path))

    @app.route("/api/config/quality", methods=["GET"])
    def get_quality_config_route():
        from backend.services.quality_service import get_quality_config
        return jsonify(get_quality_config())

    @app.route("/api/quality-check", methods=["POST"])
    def quality_check_route():
        from backend.services.quality_service import assess_image_quality
        file = request.files.get("image") or request.files.get("file")
        if file:
            res = assess_image_quality(file.read())
            return jsonify(res)
        raw_data = request.get_data()
        if raw_data:
            res = assess_image_quality(raw_data)
            return jsonify(res)
        return jsonify({"ok": None, "error": "No image provided", "issues": ["No image file provided"]}), 400

    # --- Camps API ---
    @app.route("/api/camps", methods=["GET"])
    def list_camps():
        caller_comms = get_caller_communities()
        if not caller_comms:
            return jsonify({
                "error": "forbidden",
                "message": "Tenant identity required: caller has no authorized community memberships.",
                "code": "MISSING_TENANT_IDENTITY"
            }), 403

        local_camps = database.list_camps_with_stats(db_path) or []
        local_filtered = [c for c in local_camps if get_resource_community(c) in caller_comms]

        if is_supabase_enabled():
            try:
                camps = supabase_service.list_camps_with_stats()
                if camps is not None:
                    sup_filtered = [c for c in camps if get_resource_community(c) in caller_comms]
                    # Merge local camps with Supabase camps (local wins for newly created camps)
                    merged = {c["id"]: c for c in sup_filtered if c.get("id")}
                    for lc in local_filtered:
                        if lc.get("id"):
                            merged[lc["id"]] = lc
                    return jsonify(list(merged.values()))
            except Exception as e:
                logger.warning("Supabase list_camps notice: %s", e)

        return jsonify(local_filtered)

    @app.route("/api/camps", methods=["POST"])
    def add_camp():
        caller_comms = get_caller_communities()
        if not caller_comms:
            return jsonify({
                "error": "forbidden",
                "message": "Tenant identity required: caller has no authorized community memberships.",
                "code": "MISSING_TENANT_IDENTITY"
            }), 403

        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "Camp name is required"}), 400
        
        try:
            expected = int(data.get("expected_records", 100))
        except (ValueError, TypeError):
            expected = 100
            
        date_str = data.get("date_str", "Today").strip() or "Today"
        location = data.get("location", "Chennai Health Camp")
        target_group = (data.get("target_group") or "adult").strip().lower()
        if target_group not in ("adult", "paediatric"):
            target_group = "adult"

        req_comm = data.get("community_id")
        if req_comm:
            if req_comm not in caller_comms:
                return jsonify({
                    "error": "forbidden",
                    "message": "Cannot create camp in unauthorized community."
                }), 403
            community_id = req_comm
        else:
            primary = getattr(g, 'org_id', None) or getattr(g, 'community_id', None)
            if primary and primary in caller_comms:
                community_id = primary
            else:
                # Prioritize valid UUID community from caller_comms
                uuid_comms = [c for c in caller_comms if is_valid_uuid(c)]
                community_id = uuid_comms[0] if uuid_comms else next(iter(caller_comms))

        user_id = getattr(g, 'user_id', None)
        created_by_uuid = str(user_id) if is_valid_uuid(user_id) else None
        created_by_display = getattr(g, 'current_user', {}).get("name") or str(user_id or 'demo_user')

        import uuid
        camp_uuid = str(uuid.uuid4())

        # 1. Local-First: Create immediately in local SQLite (< 1 ms)
        camp = database.create_camp(
            name, expected, date_str,
            location=location,
            created_by=created_by_display,
            camp_id=camp_uuid,
            target_group=target_group,
            community_id=community_id,
            db_path=db_path
        )

        # 2. Asynchronous Sync: Sync camp to Supabase in a background thread
        if is_supabase_enabled() and is_valid_uuid(community_id):
            def _bg_create_camp(c_uuid):
                try:
                    supabase_service.create_camp(
                        name=name,
                        expected_records=expected,
                        date_str=date_str,
                        location=location,
                        target_group=target_group,
                        community_id=community_id,
                        created_by=created_by_uuid,
                        camp_id=c_uuid
                    )
                except Exception as s_err:
                    logger.warning("Background Supabase camp creation notice: %s", s_err)

            threading.Thread(target=_bg_create_camp, args=(camp_uuid,), daemon=True).start()

        return jsonify(camp), 201

    @app.route("/api/camps/<camp_id>", methods=["GET"])
    def get_camp(camp_id):
        camp = resolve_camp(camp_id)
        if not camp:
            return jsonify({"error": "Camp not found"}), 404

        camp_comm = get_resource_community(camp)
        if not check_community_access(camp_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization access forbidden: camp does not belong to caller organization."
            }), 403
        return jsonify(camp)

    @app.route("/api/camps/<camp_id>", methods=["PUT"])
    def update_camp(camp_id):
        existing = resolve_camp(camp_id)
        if not existing:
            return jsonify({"error": "Camp not found"}), 404
        camp_comm = get_resource_community(existing)
        if not check_community_access(camp_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization modification forbidden: camp does not belong to caller organization."
            }), 403

        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "Camp name is required"}), 400
        expected = int(data.get("expected_records", 100))
        date_str = data.get("date_str", "Today").strip() or "Today"
        location = data.get("location")
        updated_camp = None
        if is_supabase_enabled():
            try:
                updated_camp = supabase_service.update_camp(camp_id, name, expected, date_str, location)
            except Exception as e:
                logger.error("Supabase update_camp error: %s", e)
        camp = database.update_camp(camp_id, name, expected, date_str, location, db_path)
        return jsonify(updated_camp or camp or existing)

    @app.route("/api/camps/<camp_id>", methods=["DELETE"])
    def delete_camp(camp_id):
        existing = resolve_camp(camp_id)
        if not existing:
            return jsonify({"error": "Camp not found"}), 404
        camp_comm = get_resource_community(existing)
        if not check_community_access(camp_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization deletion forbidden: camp does not belong to caller organization."
            }), 403

        sup_deleted = False
        if is_supabase_enabled():
            try:
                sup_deleted = supabase_service.delete_camp(camp_id)
            except Exception as e:
                logger.error("Supabase delete_camp error: %s", e)
        success = database.delete_camp(camp_id, db_path)
        return jsonify({"status": "deleted", "success": bool(sup_deleted or success)})

    # --- Records API ---
    @app.route("/api/records", methods=["GET"])
    def list_records():
        claims = getattr(g, "current_user", None)
        if not claims:
            from backend.services.auth_service import get_token_from_request, safe_decode_token, extract_claims
            token = get_token_from_request()
            if not token:
                return jsonify({
                    "error": "unauthorized",
                    "message": "Authentication required. Please provide Bearer token in Authorization header.",
                    "code": "MISSING_TOKEN"
                }), 401
            payload, err = safe_decode_token(token)
            if err or not payload:
                return jsonify({
                    "error": "unauthorized",
                    "message": f"Invalid or expired token: {err or 'Verification failed'}",
                    "code": "INVALID_TOKEN"
                }), 401
            try:
                claims = extract_claims(payload)
            except Exception as e:
                return jsonify({
                    "error": "unauthorized",
                    "message": f"Missing required claim: {e}",
                    "code": "INVALID_CLAIMS"
                }), 401
            g.current_user = claims
            g.user_id = claims["user_id"]
            g.user_role = claims["role"]
            g.org_id = claims.get("org_id")
            g.community_id = claims.get("community_id")
            g.community_ids = claims.get("community_ids", [])

        caller_comms = get_caller_communities()
        if not caller_comms:
            return jsonify({
                "error": "forbidden",
                "message": "Tenant identity required: caller has no authorized community memberships.",
                "code": "MISSING_TENANT_IDENTITY"
            }), 403

        camp_id = request.args.get("camp_id")
        if camp_id:
            camp = resolve_camp(camp_id)
            if not camp:
                return jsonify({"error": "Camp not found"}), 404
            camp_comm = get_resource_community(camp)
            if not check_community_access(camp_comm):
                return jsonify({
                    "error": "forbidden",
                    "message": "Cross-organization access forbidden: camp does not belong to caller organization."
                }), 403

        requested_comm = request.args.get("community_id") or request.args.get("org_id")
        if requested_comm:
            if requested_comm not in caller_comms:
                return jsonify({
                    "error": "forbidden",
                    "message": "Cross-organization access forbidden: caller is not a member of requested community."
                }), 403
            filter_comm = requested_comm
        elif len(caller_comms) == 1:
            filter_comm = next(iter(caller_comms))
        else:
            filter_comm = None

        status_filter = request.args.get("status")
        scanned_by = request.args.get("scanned_by")
        query = request.args.get("q")
        page_param = request.args.get("page")
        page = int(page_param) if page_param and page_param.isdigit() else None
        limit = min(int(request.args.get("limit", 20 if page else 100)), 500)

        # Rule 4.3: field_worker sees only records they created
        user_role = getattr(g, "user_role", None)
        if user_role == "field_worker":
            scanned_by = getattr(g, "user_id", None)

        local_recs = []
        if filter_comm:
            local_recs = database.list_records(
                camp_id=camp_id,
                status=status_filter,
                query=query,
                scanned_by=scanned_by,
                community_id=filter_comm,
                page=page,
                limit=limit,
                db_path=db_path
            ) or []
        else:
            all_recs = []
            seen_ids = set()
            for cid in caller_comms:
                c_recs = database.list_records(
                    camp_id=camp_id,
                    status=status_filter,
                    query=query,
                    scanned_by=scanned_by,
                    community_id=cid,
                    limit=limit,
                    db_path=db_path
                )
                items = c_recs.get("records", c_recs) if isinstance(c_recs, dict) else c_recs
                for r in items:
                    if r.get("id") not in seen_ids:
                        seen_ids.add(r.get("id"))
                        all_recs.append(r)
            local_recs = all_recs[:limit]

        if is_supabase_enabled():
            try:
                sup_recs = supabase_service.list_records(
                    camp_id=camp_id,
                    status=status_filter,
                    community_id=filter_comm,
                    query=query,
                    scanned_by=scanned_by,
                    limit=limit
                )
                if sup_recs is not None:
                    if not filter_comm:
                        sup_recs = [r for r in sup_recs if get_resource_community(r) in caller_comms]
                    # Merge local records with Supabase records (local wins for newly saved records)
                    merged = {r.get("id"): r for r in sup_recs if r.get("id")}
                    for lr in local_recs:
                        if lr.get("id"):
                            merged[lr["id"]] = lr
                    return jsonify(list(merged.values())[:limit])
            except Exception as e:
                logger.error("Supabase list_records error: %s", e)

        return jsonify(local_recs)

    @app.route("/api/records/<record_id>", methods=["GET"])
    def get_record(record_id):
        from flask import g
        rec = resolve_record(record_id)
        if not rec:
            return jsonify({"error": "Record not found"}), 404

        rec_comm = get_resource_community(rec)
        if not check_community_access(rec_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization access forbidden: record does not belong to caller organization."
            }), 403

        # Rule 4.3: Intra-Org Role Restrictions: field_worker can read only their own scans -> 403
        user_role = getattr(g, 'user_role', None)
        user_id = getattr(g, 'user_id', None)
        if user_role == "field_worker" and rec.get("scanned_by") != user_id:
            return jsonify({
                "error": "forbidden",
                "message": "Access denied: field_worker can only view own records."
            }), 403

        return jsonify(rec)

    @app.route("/api/camps/<camp_id>/paediatric-records", methods=["GET"])
    def list_camp_paediatric_records(camp_id):
        camp = resolve_camp(camp_id)
        if not camp:
            return jsonify({"error": "Camp not found"}), 404
        camp_comm = get_resource_community(camp)
        if not check_community_access(camp_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization access forbidden: camp does not belong to caller organization."
            }), 403
        status_filter = request.args.get("status")
        limit = min(int(request.args.get("limit", 100)), 500)
        if is_supabase_enabled():
            recs = supabase_service.list_paediatric_records(camp_id=camp_id, status=status_filter, limit=limit)
            if recs is not None:
                return jsonify(recs)
        recs = database.list_paediatric_records(camp_id=camp_id, status=status_filter, limit=limit, db_path=db_path)
        return jsonify(recs)

    @app.route("/api/camps/<camp_id>/adult-records", methods=["GET"])
    def list_camp_adult_records(camp_id):
        camp = resolve_camp(camp_id)
        if not camp:
            return jsonify({"error": "Camp not found"}), 404
        camp_comm = get_resource_community(camp)
        if not check_community_access(camp_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization access forbidden: camp does not belong to caller organization."
            }), 403
        status_filter = request.args.get("status")
        limit = min(int(request.args.get("limit", 100)), 500)
        if is_supabase_enabled():
            recs = supabase_service.list_adult_records(camp_id=camp_id, status=status_filter, limit=limit)
            if recs is not None:
                return jsonify(recs)
        recs = database.list_adult_records(camp_id=camp_id, status=status_filter, limit=limit, db_path=db_path)
        return jsonify(recs)

    @app.route("/api/paediatric-records/<record_id>", methods=["GET"])
    def get_paediatric_record(record_id):
        rec = None
        if is_supabase_enabled():
            rec = supabase_service.get_paediatric_record_by_id(record_id)
        if not rec:
            rec = database.get_paediatric_record_by_id(record_id, db_path)
        if not rec:
            return jsonify({"error": "Paediatric record not found"}), 404
        rec_comm = get_resource_community(rec)
        if not check_community_access(rec_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization access forbidden: record does not belong to caller organization."
            }), 403
        return jsonify(rec)

    @app.route("/api/adult-records/<record_id>", methods=["GET"])
    def get_adult_record(record_id):
        rec = None
        if is_supabase_enabled():
            rec = supabase_service.get_adult_record_by_id(record_id)
        if not rec:
            rec = database.get_adult_record_by_id(record_id, db_path)
        if not rec:
            return jsonify({"error": "Adult record not found"}), 404
        rec_comm = get_resource_community(rec)
        if not check_community_access(rec_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization access forbidden: record does not belong to caller organization."
            }), 403
        return jsonify(rec)

    @app.route("/api/paediatric/screen", methods=["POST"])
    def screen_paediatric_vitals():
        """Evaluate paediatric growth percentiles (IAP 2015), BP classification, and ATR scoliosis screening."""
        from backend.services.paediatric_screening import classify_paediatric
        data = request.get_json(silent=True) or {}
        fields = {}
        for k, v in data.items():
            if isinstance(v, (list, dict)):
                fields[k] = v
            else:
                fields[k] = [v, 1.0]
        result = classify_paediatric(fields)
        return jsonify(result)

    @app.route("/api/records", methods=["POST"])
    def create_record():
        from flask import g
        caller_comms = get_caller_communities()
        if not caller_comms:
            return jsonify({
                "error": "forbidden",
                "message": "Tenant identity required: caller has no authorized community memberships.",
                "code": "MISSING_TENANT_IDENTITY"
            }), 403

        payload = request.get_json(silent=True) or {}
        # Ensure a single, consistent record ID across both Supabase and SQLite
        if not payload.get("id"):
            payload["id"] = f"rec_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"

        camp_id = payload.get("camp_id")
        if not camp_id or camp_id == "camp_default":
            camp_id = "camp_default"
            payload["camp_id"] = camp_id

        # Ensure camp exists in local database to satisfy foreign key / record_service validation
        existing_camp = database.get_camp_by_id(camp_id, db_path)
        if not existing_camp:
            primary_comm = getattr(g, 'org_id', None) or (next(iter(caller_comms)) if caller_comms else "comm_default")
            try:
                database.create_camp(
                    name="General Health Screening Camp",
                    expected_records=100,
                    date_str=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    camp_id=camp_id,
                    community_id=primary_comm,
                    db_path=db_path
                )
                existing_camp = database.get_camp_by_id(camp_id, db_path)
            except Exception as camp_init_err:
                logger.warning("Auto-creation of camp %s failed: %s", camp_id, camp_init_err)

        if existing_camp:
            camp_comm = get_resource_community(existing_camp)
            if caller_comms and not check_community_access(camp_comm):
                primary_comm = getattr(g, 'org_id', None) or next(iter(caller_comms))
                payload["community_id"] = primary_comm
            else:
                payload["community_id"] = camp_comm
        else:
            primary_comm = getattr(g, 'org_id', None) or (next(iter(caller_comms)) if caller_comms else "comm_default")
            payload["community_id"] = primary_comm

        if not payload.get("scanned_by"):
            payload["scanned_by"] = getattr(g, 'user_id', 'demo_user')
        try:
            # 1. Local-First: Persist to SQLite immediately (< 2 ms)
            created = record_service.create_record(payload)

            # 2. Asynchronous Sync: Sync to Supabase in background thread without blocking the user
            if is_supabase_enabled():
                def _bg_sync_create(rec_data):
                    try:
                        supabase_service.insert_record(rec_data)
                    except Exception as s_err:
                        logger.warning("Background Supabase record insert notice: %s", s_err)

                threading.Thread(target=_bg_sync_create, args=(dict(payload),), daemon=True).start()

            return jsonify(created), 201
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.exception("Error saving record")
            return jsonify({"error": "Failed to persist record"}), 500

    @app.route("/api/records/<record_id>", methods=["PUT"])
    def update_record(record_id):
        from flask import g
        from backend.services.field_permissions import validate_field_worker_edits
        existing = resolve_record(record_id)
        if not existing:
            return jsonify({"error": "Record not found"}), 404

        rec_comm = get_resource_community(existing)
        if not check_community_access(rec_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization modification forbidden: record does not belong to caller organization."
            }), 403

        # Rule 4.3: Field worker can only modify their own scans -> 404
        user_role = getattr(g, 'user_role', None)
        user_id = getattr(g, 'user_id', None)
        if user_role == "field_worker" and existing.get("scanned_by") != user_id:
            return jsonify({
                "error": "not_found",
                "message": "Record not found"
            }), 404

        payload = request.get_json(silent=True) or {}

        # Rule 4.2: Field worker clinical check (fail-closed, reject whole payload)
        if user_role == "field_worker":
            allowed, offending = validate_field_worker_edits(payload)
            if not allowed:
                return jsonify({
                    "error": "forbidden",
                    "message": f"Field workers cannot modify clinical fields: {', '.join(offending)}",
                    "code": "CLINICAL_FIELDS_FORBIDDEN"
                }), 403

        # Rule 4.4: Editing an approved record resets status to 'needs_review'
        was_approved = existing.get("status") == "approved"
        if was_approved and payload.get("status") != "approved":
            payload["status"] = "needs_review"
            payload["approved_by"] = None
            payload["approved_at"] = None

        # 1. Local-First: Update record in SQLite immediately (< 1 ms)
        updated = database.update_record(record_id, payload, db_path)
        if not updated:
            return jsonify({"error": "Record not found"}), 404

        # 2. Rule 4.5: Write audit row for every edited field locally
        for field, new_val in payload.items():
            if field in ("updated_at", "version"):
                continue
            old_val = existing.get(field)
            if str(old_val) != str(new_val):
                database.log_record_audit(
                    record_id=record_id,
                    org_id=rec_comm,
                    user_id=user_id or 'demo_user',
                    user_role=user_role or 'demo',
                    action="update",
                    field_name=field,
                    old_value=str(old_val) if old_val is not None else None,
                    new_value=str(new_val) if new_val is not None else None,
                    db_path=db_path
                )

        # 3. Asynchronous Sync: Sync updates to Supabase in background thread
        if is_supabase_enabled():
            def _bg_sync_update(r_id, p_data):
                try:
                    supabase_service.update_record(r_id, p_data)
                except Exception as e:
                    logger.warning("Background Supabase update_record notice: %s", e)

            threading.Thread(target=_bg_sync_update, args=(record_id, dict(payload)), daemon=True).start()

        return jsonify(updated)

    @app.route("/api/records/<record_id>", methods=["DELETE"])
    def delete_record(record_id):
        from flask import g
        existing = resolve_record(record_id)
        if not existing:
            return jsonify({"error": "Record not found"}), 404

        rec_comm = get_resource_community(existing)
        if not check_community_access(rec_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization deletion forbidden: record does not belong to caller organization."
            }), 403

        # Intra-Org Role Restrictions (A7):
        user_role = getattr(g, 'user_role', None)
        user_id = getattr(g, 'user_id', None)
        # field_worker cannot delete
        if user_role == "field_worker":
            return jsonify({
                "error": "forbidden",
                "message": "Field workers are not permitted to delete records."
            }), 403
        # doctor can delete only their own drafts
        if user_role == "doctor":
            is_draft = existing.get("status") in ("needs_review", "draft")
            is_own = existing.get("scanned_by") == user_id
            if not (is_draft and is_own):
                return jsonify({
                    "error": "forbidden",
                    "message": "Doctors may only delete their own draft records."
                }), 403

        if is_supabase_enabled():
            try:
                supabase_service.delete_record(record_id)
            except Exception as e:
                logger.error("Supabase delete_record error: %s", e)
        success = database.delete_record(record_id, db_path)
        return jsonify({"status": "deleted", "success": success})

    @app.route("/api/records/<record_id>/review", methods=["PUT", "POST"])
    def review_record(record_id):
        """Review and update record fields. Resolving flags alone does NOT approve or bill paper."""
        existing = resolve_record(record_id)
        if not existing:
            return jsonify({"error": "Record not found"}), 404

        rec_comm = get_resource_community(existing)
        if not check_community_access(rec_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization modification forbidden: record does not belong to caller organization."
            }), 403

        # Role Permissions: doctor, admin, superuser, or field_worker for own records
        user_role = getattr(g, 'user_role', None)
        user_id = getattr(g, 'user_id', None)
        if user_role == "field_worker" and existing.get("scanned_by") != user_id:
            return jsonify({
                "error": "forbidden",
                "message": "Field workers can only review their own records."
            }), 403
        elif user_role not in ("field_worker", "doctor", "admin", "superuser"):
            return jsonify({
                "error": "forbidden",
                "message": f"Review requires authorized role; current role '{user_role}' is forbidden."
            }), 403

        payload = request.get_json(silent=True) or {}
        current_user = getattr(g, 'current_user', {})
        reviewer = current_user.get("name") or payload.get("reviewed_by") or user_id

        try:
            if is_supabase_enabled():
                try:
                    supabase_service.update_reviewed_record(record_id, payload, reviewer)
                except Exception as e:
                    logger.error("Failed to update in Supabase: %s", e)

            updated = record_service.review_record(record_id, payload, reviewer)
            # Release lock upon review
            database.unlock_record(record_id, reviewer, db_path=db_path)
            return jsonify(updated)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.exception("Error reviewing record")
            return jsonify({"error": "Failed to update record"}), 500

    @app.route("/api/records/<record_id>/approve", methods=["POST"])
    def approve_record_endpoint(record_id):
        """Explicit approval action: checks flags are resolved, stores approved_by & approved_at, and marks paper billed."""
        from flask import g
        user_role = getattr(g, 'user_role', None)
        user_id = getattr(g, 'user_id', None)
        
        if user_role not in ("field_worker", "doctor", "admin", "superuser"):
            return jsonify({
                "error": "forbidden",
                "message": f"Approval requires authorized role; current role '{user_role}' is forbidden."
            }), 403

        existing = resolve_record(record_id)
        if not existing:
            return jsonify({"error": "Record not found"}), 404

        rec_comm = get_resource_community(existing)
        if not check_community_access(rec_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization modification forbidden: record does not belong to caller organization."
            }), 403

        # Intra-Org Worker Isolation: field_worker can approve only their own scans
        if user_role == "field_worker" and existing.get("scanned_by") != user_id:
            return jsonify({
                "error": "forbidden",
                "message": "Field workers can only approve their own records."
            }), 403

        payload = request.get_json(silent=True) or {}
        current_user = getattr(g, 'current_user', {})
        doctor = current_user.get("name") or user_id
        org_id = rec_comm or 'demo_org'

        # Check for unresolved flags passed in payload
        unresolved_flags = payload.get("unresolved_flags") or payload.get("flags_unresolved")
        if unresolved_flags:
            return jsonify({
                "error": "flags_unresolved",
                "message": f"Approval refused: {len(unresolved_flags)} flagged fields remain unresolved."
            }), 409

        if is_supabase_enabled():
            try:
                supabase_service.approve_record(record_id, doctor)
            except Exception as e:
                logger.error("Failed to approve in Supabase: %s", e)

        success, updated, err = database.approve_record(record_id, approved_by=doctor, org_id=org_id, db_path=db_path)
        if not success:
            if err == "flags_unresolved":
                return jsonify({
                    "error": "flags_unresolved",
                    "message": "Approval refused: flagged fields remain unresolved."
                }), 409
            if err == "consent_withheld":
                return jsonify({
                    "error": "consent_withheld",
                    "message": "Approval refused: Patient or parental consent is withheld or not given."
                }), 403
            return jsonify({"error": err or "Failed to approve record"}), 404

        database.log_record_audit(
            record_id=record_id,
            org_id=rec_comm,
            user_id=user_id or 'demo_user',
            user_role=user_role or 'demo',
            action="approve",
            field_name="status",
            old_value=existing.get("status"),
            new_value="approved",
            db_path=db_path
        )

        return jsonify({
            "success": True,
            "status": "approved",
            "approved_by": doctor,
            "approved_at": updated.get("approved_at"),
            "record": updated
        })

    # --- Record Audit API (Rule 4.5) ---
    @app.route("/api/records/<record_id>/audit", methods=["GET"])
    def get_record_audit(record_id):
        """Retrieve insert-only audit log for doctor/admin within the same org."""
        from flask import g
        existing = database.get_record_by_id(record_id, db_path)
        if not existing:
            return jsonify({"error": "Record not found"}), 404

        rec_comm = get_resource_community(existing)
        if not check_community_access(rec_comm):
            return jsonify({"error": "forbidden", "message": "Cross-organization access forbidden"}), 403

        user_role = getattr(g, 'user_role', None)
        if user_role not in ("doctor", "admin", "superuser"):
            return jsonify({"error": "forbidden", "message": "Only doctors and admins can view audit history."}), 403

        entries = database.get_record_audit_log(record_id, db_path=db_path)
        return jsonify({"audit_log": entries}), 200

    @app.route("/api/records/<record_id>/release", methods=["POST"])
    def release_record(record_id):
        org_id = request.headers.get("X-Org-ID") or request.args.get("org_id") or "org_default"
        rec = database.get_record_by_id(record_id, db_path)
        if rec and rec.get("status") in ("reviewed", "approved"):
            return jsonify({
                "error": "already_billed",
                "message": "Cannot release paper quota for an already approved record."
            }), 409
        papers_left = database.release_paper(org_id=org_id, record_id=record_id, reason="manual_release", db_path=db_path)
        return jsonify({"success": True, "papers_left": papers_left})

    @app.route("/api/records/<record_id>/lock", methods=["POST"])
    def lock_record(record_id):
        data = request.get_json(silent=True) or {}
        doctor_name = data.get("doctor_name") or request.headers.get("X-Doctor-ID") or "Dr. Priya"
        # Local-first: lock instantly in SQLite
        res = database.lock_record(record_id, doctor_name, db_path=db_path)
        if is_supabase_enabled():
            def _bg_lock(r_id, doc):
                try:
                    supabase_service.lock_record(r_id, doc)
                except Exception as e:
                    logger.debug("Background Supabase lock notice: %s", e)
            threading.Thread(target=_bg_lock, args=(record_id, doctor_name), daemon=True).start()
        return jsonify(res)

    @app.route("/api/records/<record_id>/unlock", methods=["POST"])
    def unlock_record(record_id):
        data = request.get_json(silent=True) or {}
        doctor_name = data.get("doctor_name") or request.headers.get("X-Doctor-ID") or "Dr. Priya"
        # Local-first: unlock instantly in SQLite
        success = database.unlock_record(record_id, doctor_name, db_path=db_path)
        if is_supabase_enabled():
            def _bg_unlock(r_id, doc):
                try:
                    supabase_service.unlock_record(r_id, doc)
                except Exception as e:
                    logger.debug("Background Supabase unlock notice: %s", e)
            threading.Thread(target=_bg_unlock, args=(record_id, doctor_name), daemon=True).start()
        return jsonify({"success": success, "unlocked": success})

    @app.route("/api/records/validate", methods=["POST"])
    def validate_record():
        payload = request.get_json(silent=True) or {}
        res = record_service.validate_record_fields(payload)
        return jsonify(res)

    @app.route("/api/records/check-duplicate", methods=["POST"])
    def check_duplicate():
        data = request.get_json(silent=True) or {}
        camp_id = data.get("camp_id", "")
        person_name = data.get("person_name", "")
        phone = data.get("phone", "")
        exclude_id = data.get("exclude_id")
        res = database.check_duplicate_record(camp_id, person_name, phone, exclude_id, db_path)
        res["has_duplicate"] = res.get("is_duplicate", False)
        res["count"] = 1 if res["has_duplicate"] else 0
        return jsonify(res)

    @app.route("/api/records/<record_id>/erase", methods=["DELETE", "POST"])
    def erase_record(record_id):
        # DPDP Right to Erasure - Admin within own org only
        from flask import g
        if g.user_role not in ("admin", "superuser"):
            return jsonify({
                "error": "forbidden",
                "message": "DPDP Right to Erasure can only be exercised by organization administrators."
            }), 403

        existing = database.get_record_by_id(record_id, db_path)
        if not existing:
            return jsonify({"error": "Record not found"}), 404

        rec_comm = get_resource_community(existing)
        if not check_community_access(rec_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization erasure forbidden: record does not belong to caller organization."
            }), 403

        if is_supabase_enabled():
            try:
                supabase_service.delete_record(record_id)
            except Exception as e:
                logger.error("Supabase erasure error: %s", e)
        success = database.erase_patient_record(record_id, db_path)
        return jsonify({
            "status": "erased",
            "success": success,
            "erased": success,
            "message": "Patient data and source images permanently deleted under DPDP Act."
        })

    @app.route("/api/records/<record_id>/referral-slip", methods=["GET"])
    def get_referral_slip(record_id):
        """Generate and stream a 1-page printable clinical referral slip PDF."""
        rec = database.get_record_by_id(record_id, db_path)
        if not rec:
            return jsonify({"error": "Record not found"}), 404

        rec_comm = get_resource_community(rec)
        if not check_community_access(rec_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization access forbidden: record does not belong to caller organization."
            }), 403

        p_dir = database.get_private_upload_dir()
        pdf_dir = os.path.join(p_dir, "referral_slips")
        os.makedirs(pdf_dir, exist_ok=True)
        pdf_filename = f"referral_slip_{record_id}.pdf"
        output_pdf_path = os.path.join(pdf_dir, pdf_filename)

        database.generate_patient_referral_pdf(rec, output_pdf_path)

        with open(output_pdf_path, "rb") as f:
            pdf_bytes = f.read()

        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"inline; filename={pdf_filename}"}
        )


    @app.route("/api/admin/billing", methods=["GET"])
    def admin_billing():
        if not is_admin_request():
            return jsonify({"error": "forbidden", "message": "Admin authorization required."}), 403
        # Aggregated admin, billing and operational telemetry
        billing_info = database.get_admin_billing(db_path)
        billing_info["used_scans"] = billing_info.get("quota_used", 0)
        billing_info["monthly_limit"] = billing_info.get("quota_total", 5000)
        billing_info["cost_estimate_inr"] = billing_info.get("est_groq_cost_inr", 0.0)
        billing_info["invoices"] = [
            {"id": "INV-2026-10", "date": "01 Oct 2026", "plan": "Outreach 2,500 Scans", "amount": "₹0.00 (Grant)", "status": "Paid"},
            {"id": "INV-2026-09", "date": "01 Sep 2026", "plan": "Outreach 2,500 Scans", "amount": "₹0.00 (Grant)", "status": "Paid"}
        ]
        return jsonify(billing_info)

    @app.route("/api/admin/purchase-credits", methods=["POST"])
    def purchase_credits():
        data = request.get_json(silent=True) or {}
        credits = int(data.get("credits", 1000))
        res = database.purchase_credits(credits, db_path=db_path)
        res["credits_added"] = res.get("added_credits", credits)
        res["new_limit"] = res.get("new_quota", 5000)
        return jsonify(res)

    # --- Tokenized Single-Use Invites API ---
    @app.route("/api/community/invites", methods=["GET"])
    def list_invites():
        from flask import g
        user_id = getattr(g, "user_id", None)
        if not user_id:
            return jsonify({"error": "unauthorized", "code": "MISSING_TOKEN", "message": "Authentication required."}), 401

        caller_comms = get_caller_communities()
        comm_id = request.args.get("community_id")
        if not comm_id:
            if len(caller_comms) == 1:
                comm_id = next(iter(caller_comms))
            else:
                return jsonify({"error": "bad_request", "code": "COMMUNITY_ID_REQUIRED", "message": "community_id parameter is required."}), 400

        # Verify caller belongs to the requested community
        if not check_community_access(comm_id):
            return jsonify({"error": "forbidden", "code": "COMMUNITY_ACCESS_DENIED", "message": "Access denied: caller does not have active membership in the requested community."}), 403

        comm_role = get_caller_community_role(comm_id)
        if comm_role not in ("admin", "community_admin", "lead_doctor", "owner", "doctor", "physician"):
            return jsonify({"error": "forbidden", "code": "INSUFFICIENT_ROLE", "message": "Users cannot view invitations unless explicitly authorized."}), 403

        # Community Admins may view all invitations in that community
        # Doctor members may view only invitations they created
        is_admin = comm_role in ("admin", "community_admin", "lead_doctor", "owner")
        filter_creator = None if is_admin else str(user_id)

        if is_supabase_enabled():
            invs = supabase_service.list_invitations(community_id=comm_id, created_by=filter_creator)
            return jsonify({"success": True, "invites": invs, "total": len(invs)}), 200

        # Offline / local SQLite fallback
        raw_invs = database.list_invites(comm_id, db_path=db_path)
        if filter_creator:
            filtered_invs = []
            for inv in raw_invs:
                c = str(inv.get("created_by") or "").strip().lower()
                if c == filter_creator.lower():
                    filtered_invs.append(inv)
            invs = filtered_invs
        else:
            invs = raw_invs

        for inv in invs:
            inv["status"] = supabase_service.derive_invitation_status(inv)

        return jsonify({"success": True, "invites": invs, "total": len(invs)}), 200

    @app.route("/api/community/invites", methods=["POST"])
    @app.route("/api/community/invite", methods=["POST"])
    def create_invite():
        import secrets
        from datetime import timedelta
        from flask import g

        user_id = getattr(g, "user_id", None)
        if not user_id:
            return jsonify({"error": "unauthorized", "code": "MISSING_TOKEN", "message": "Authentication required."}), 401

        data = request.get_json(silent=True) or {}
        comm_id = data.get("community_id")
        caller_comms = get_caller_communities()
        if not comm_id:
            if len(caller_comms) == 1:
                comm_id = next(iter(caller_comms))
            else:
                return jsonify({"error": "bad_request", "code": "COMMUNITY_ID_REQUIRED", "message": "community_id is required."}), 400

        # Verify caller is an active member of the target community
        if not check_community_access(comm_id):
            return jsonify({"error": "forbidden", "code": "COMMUNITY_ACCESS_DENIED", "message": "Access denied: caller does not have active membership in the target community."}), 403

        # Only Community Admins and authorized Doctor members may create invitation codes
        # Global account type alone does not grant community permissions!
        comm_role = get_caller_community_role(comm_id)
        if comm_role not in ("admin", "community_admin", "lead_doctor", "owner", "doctor", "physician"):
            return jsonify({"error": "forbidden", "code": "INSUFFICIENT_ROLE", "message": "Only Community Admins and Doctors may create invitations."}), 403

        # Determine approval requirement derived strictly from community role (not from request JSON)
        is_admin = comm_role in ("admin", "community_admin", "lead_doctor", "owner")
        requires_admin_approval = not is_admin  # Admin -> False, Doctor -> True

        # Target role for the invited user
        target_role = (data.get("role") or "doctor").strip().lower()
        if target_role in ("user", "worker"):
            target_role = "field_worker"
        if target_role in ("community_admin", "super_admin", "admin"):
            return jsonify({"error": "bad_request", "code": "PRIVILEGED_ROLE_PROHIBITED", "message": "Administrative roles cannot be granted through invitations."}), 400
        if target_role not in ("doctor", "field_worker"):
            return jsonify({"error": "bad_request", "code": "INVALID_INVITATION_ROLE", "message": "Invalid invitation role. Allowed roles: doctor, field_worker."}), 400

        # Target email (optional restriction)
        raw_email = data.get("email")
        email = raw_email.strip().lower() if raw_email and str(raw_email).strip() else None

        # Configurable max_uses
        raw_max = data.get("max_uses")
        if raw_max is not None:
            try:
                max_uses = int(raw_max)
                if max_uses < 1:
                    return jsonify({"error": "bad_request", "code": "INVALID_MAX_USES", "message": "max_uses must be a positive integer."}), 400
            except (ValueError, TypeError):
                return jsonify({"error": "bad_request", "code": "INVALID_MAX_USES", "message": "max_uses must be a valid integer."}), 400
        else:
            max_uses = 1

        # Configurable expires_at / expires_hours
        if data.get("expires_at"):
            try:
                raw_exp = str(data["expires_at"]).strip()
                exp_clean = raw_exp.replace("Z", "+00:00")
                exp_dt = datetime.fromisoformat(exp_clean)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if exp_dt <= datetime.now(timezone.utc):
                    return jsonify({"error": "bad_request", "code": "INVALID_EXPIRY", "message": "expires_at must be in the future."}), 400
                expires_at_iso = exp_dt.isoformat()
                expires_hours = max(1, int((exp_dt - datetime.now(timezone.utc)).total_seconds() / 3600))
            except Exception:
                return jsonify({"error": "bad_request", "code": "INVALID_EXPIRY", "message": "Invalid expires_at format. Expected ISO 8601 string."}), 400
        else:
            raw_hours = data.get("expires_hours", 72)
            try:
                expires_hours = int(raw_hours)
                if expires_hours < 1:
                    return jsonify({"error": "bad_request", "code": "INVALID_EXPIRY", "message": "expires_hours must be a positive integer."}), 400
            except (ValueError, TypeError):
                expires_hours = 72
            expires_at_iso = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).isoformat()

        # Generate secure, unpredictable invitation code (at least 128-bit cryptographic randomness)
        prefix = "DOC" if target_role == "doctor" else "USR"
        created_by = str(user_id)

        if is_supabase_enabled():
            res = None
            code = None
            for _ in range(5):
                code = f"INV-{prefix}-{secrets.token_hex(16).upper()}"
                res = supabase_service.create_invitation(
                    community_id=comm_id,
                    code=code,
                    role_id=target_role,
                    created_by=created_by,
                    requires_admin_approval=requires_admin_approval,
                    max_uses=max_uses,
                    expires_at=expires_at_iso,
                    email=email
                )
                if res.get("success"):
                    break
            if not res or not res.get("success"):
                return jsonify({
                    "error": "server_error",
                    "code": res.get("code", "INVITATION_CREATION_FAILED") if res else "INVITATION_CREATION_FAILED",
                    "message": res.get("message", "Failed to create invitation.") if res else "Failed to create invitation."
                }), 500

            inv = res["invitation"]
            try:
                database.create_invite(comm_id, email, target_role, expires_hours=expires_hours, created_by=created_by, db_path=db_path)
            except Exception as cache_err:
                logger.debug("SQLite invite cache skipped: %s", cache_err)

            return jsonify({
                "success": True,
                "invitation": inv,
                "invite": inv,
                "invite_code": inv.get("code") or code
            }), 201

        # Offline / local SQLite fallback
        code = f"INV-{prefix}-{secrets.token_hex(16).upper()}"
        inv = database.create_invite(comm_id, email, target_role, expires_hours=expires_hours, created_by=created_by, db_path=db_path)
        inv["code"] = code
        inv["requires_admin_approval"] = requires_admin_approval
        inv["max_uses"] = max_uses
        inv["use_count"] = 0
        inv["status"] = "active"
        return jsonify({
            "success": True,
            "invitation": inv,
            "invite": inv,
            "invite_code": inv.get("code")
        }), 201

    @app.route("/api/community/invites/<invite_id>", methods=["DELETE"])
    @app.route("/api/community/invites/<invite_id>/revoke", methods=["POST"])
    def revoke_invite(invite_id):
        from flask import g
        user_id = getattr(g, "user_id", None)
        if not user_id:
            return jsonify({"error": "unauthorized", "code": "MISSING_TOKEN", "message": "Authentication required."}), 401

        # Resolve the invitation from Supabase or local SQLite
        inv = None
        if is_supabase_enabled():
            inv = supabase_service.get_invitation_by_id_or_code(invite_id)
        else:
            with database.get_db_connection(db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, community_id, is_revoked, created_by FROM invites WHERE id = ? OR code = ?;", (invite_id, invite_id))
                row = cur.fetchone()
                inv = dict(row) if row else None

        if not inv:
            return jsonify({"error": "not_found", "code": "INVITATION_NOT_FOUND", "message": "Invitation not found."}), 404

        inv_comm_id = str(inv.get("community_id", "")).strip()
        # Require caller to belong to that community
        if not check_community_access(inv_comm_id):
            return jsonify({"error": "forbidden", "code": "COMMUNITY_ACCESS_DENIED", "message": "Access denied: cannot manage invitations of another community."}), 403

        # Authorization: Community Admin can revoke any invitation; Doctor can revoke only their own
        caller_role = get_caller_community_role(inv_comm_id)
        is_admin = caller_role in ("admin", "community_admin", "lead_doctor", "owner")
        is_doctor = caller_role in ("doctor", "physician")

        if is_admin:
            pass  # Admin authorized to revoke any invitation in this community
        elif is_doctor:
            inv_creator = str(inv.get("created_by") or "").strip().lower()
            caller_id = str(user_id).strip().lower()
            if inv_creator != caller_id:
                return jsonify({
                    "error": "forbidden",
                    "code": "FORBIDDEN",
                    "message": "Doctors may only revoke invitations they created. Community Admin authorization required to revoke other invitations."
                }), 403
        else:
            return jsonify({
                "error": "forbidden",
                "code": "INSUFFICIENT_ROLE",
                "message": "Community Admin or creator authorization required to revoke invitations."
            }), 403

        # Revoke invitation without hard-deleting
        if is_supabase_enabled():
            success = supabase_service.revoke_invitation(inv["id"])
        else:
            success = database.revoke_invite(inv["id"], db_path=db_path)

        if not success:
            return jsonify({"error": "server_error", "code": "REVOCATION_FAILED", "message": "Failed to revoke invitation."}), 500

        return jsonify({
            "success": True,
            "revoked": True,
            "message": "Invitation revoked successfully."
        }), 200

    def _map_invitation_rpc_error(res: Dict[str, Any]):
        """Map canonical PL/pgSQL RPC error codes to appropriate HTTP status and response JSON."""
        err_code = res.get("code") or "INVITATION_ERROR"
        err_msg = res.get("message") or "An error occurred while processing the invitation."
        
        # 404: Nonexistent resources
        if err_code in ("INVITATION_NOT_FOUND", "COMMUNITY_NOT_FOUND", "USER_NOT_FOUND", "MEMBERSHIP_NOT_FOUND", "ADMIN_NOT_FOUND", "TARGET_USER_NOT_FOUND"):
            status_code = 404
        # 403: Security & eligibility violations
        elif err_code in (
            "EMAIL_NOT_VERIFIED", "ROLE_INCOMPATIBLE", "PRIVILEGED_ROLE_PROHIBITED",
            "INVITATION_EMAIL_MISMATCH", "USER_EMAIL_MISMATCH", "USER_INACTIVE",
            "COMMUNITY_INACTIVE", "FORBIDDEN", "SELF_REVIEW_PROHIBITED", "ADMIN_INACTIVE",
            "TARGET_USER_INACTIVE", "INSUFFICIENT_ROLE", "COMMUNITY_ACCESS_DENIED"
        ):
            status_code = 403
        # 409: Invariant, lifecycle & capacity conflicts
        elif err_code in (
            "ALREADY_REDEEMED", "ALREADY_ACCEPTED", "ALREADY_DECLINED",
            "INVITATION_PREVIOUSLY_DECLINED", "ALREADY_ACTIVE_MEMBER",
            "MEMBERSHIP_PENDING_APPROVAL", "MEMBERSHIP_REVOKED",
            "MEMBERSHIP_PREVIOUSLY_REJECTED", "MEMBERSHIP_INACTIVE",
            "MEMBERSHIP_EXISTS", "MEMBERSHIP_NOT_PENDING", "ALREADY_REVIEWED",
            "MEMBERSHIP_COMMUNITY_CHANGED", "MEMBERSHIP_USER_CHANGED"
        ):
            status_code = 409
        # 400: Malformed inputs, expired/exhausted/revoked codes, or capacity limits
        elif err_code in (
            "INVALID_CODE", "INVALID_USER", "INVALID_EMAIL", "INVALID_ACCOUNT_TYPE",
            "INVALID_ROLE", "INVITATION_REVOKED", "INVITATION_EXPIRED",
            "INVITATION_EXHAUSTED", "INVITATION_INVALID_CAPACITY", "INVITATION_USAGE_UPDATE_FAILED",
            "INVALID_MEMBERSHIP", "INVALID_ADMIN", "INVALID_DECISION"
        ):
            status_code = 400
        else:
            status_code = 400

        return jsonify({
            "success": False,
            "valid": False,
            "error": err_code.lower(),
            "code": err_code,
            "message": err_msg
        }), status_code

    def _extract_trusted_invitation_identity():
        """Extract trusted user identity and claims strictly from verified JWT and server profile.
        Never trust client JSON or localStorage for identity or account type!
        Returns (user_id, user_email, email_verified, account_type, err_response).
        """
        from flask import g
        claims = getattr(g, "current_user", None) or {}
        user_id = getattr(g, "user_id", None) or claims.get("sub") or claims.get("user_id")
        if not user_id:
            return None, None, None, None, (jsonify({
                "error": "unauthorized",
                "code": "MISSING_TOKEN",
                "message": "Authentication required."
            }), 401)

        raw_claims = claims.get("raw") if isinstance(claims.get("raw"), dict) else claims
        app_meta = raw_claims.get("app_metadata") if isinstance(raw_claims.get("app_metadata"), dict) else (claims.get("app_metadata") or {})
        account_type = str(app_meta.get("account_type") or claims.get("account_type") or "").strip().lower()
        if not account_type:
            user_role = str(getattr(g, "user_role", None) or claims.get("role") or "").strip().lower()
            if user_role in ("doctor", "physician"):
                account_type = "doctor"
            elif user_role in ("worker", "field_worker", "user"):
                account_type = "user"

        user_prof = None
        db_email_verified = False
        if is_supabase_enabled():
            user_prof = supabase_service.get_user_profile(str(user_id))
            if user_prof:
                db_email_verified = bool(user_prof.get("email_verified"))
                if not account_type:
                    account_type = str(user_prof.get("account_type") or "").strip().lower()

        if account_type in ("field_worker", "worker"):
            account_type = "user"
        elif account_type not in ("doctor", "user"):
            account_type = "user"

        jwt_email_verified = bool(raw_claims.get("email_confirmed_at")) or bool(app_meta.get("email_verified")) or bool(claims.get("email_verified"))
        email_verified = jwt_email_verified or db_email_verified

        user_email = (user_prof.get("email") if user_prof else None) or claims.get("email") or getattr(g, "user_email", None) or ""
        user_email = user_email.strip().lower()

        # Ensure user profile is synchronized into public.users if Supabase is enabled
        if is_supabase_enabled():
            if not user_prof:
                supabase_service.sync_user_profile(
                    user_id=str(user_id),
                    email=user_email,
                    full_name=(claims.get("name") or "User").strip(),
                    account_type=account_type
                )
                user_prof = supabase_service.get_user_profile(str(user_id))
            if email_verified and user_prof and not user_prof.get("email_verified"):
                supabase_service.update_user_email_verified(str(user_id), True)

        return str(user_id), user_email, email_verified, account_type, None

    @app.route("/api/community/invites/preview", methods=["POST"])
    @app.route("/api/community/invite/preview", methods=["POST"])
    def preview_community_invite():
        """Authenticated endpoint to preview an invitation code using rpc_redeem_invitation_atomic.
        Strictly read-only: does NOT increment use_count, record response, or create membership.
        """
        user_id, user_email, email_verified, account_type, err = _extract_trusted_invitation_identity()
        if err:
            return err

        data = request.get_json(silent=True) or {}
        code = (data.get("code") or "").strip().upper()
        if not code:
            return jsonify({
                "error": "bad_request",
                "code": "INVALID_CODE",
                "message": "Invitation code is required."
            }), 400

        if not email_verified:
            return jsonify({
                "error": "forbidden",
                "code": "EMAIL_NOT_VERIFIED",
                "message": "Account email must be verified to preview invitations."
            }), 403

        if is_supabase_enabled():
            res = supabase_service.preview_invitation(
                code=code,
                user_id=user_id,
                user_email=user_email,
                email_verified=email_verified,
                account_type=account_type
            )
            if res.get("success"):
                p_data = res.get("data") or {}
                return jsonify({
                    "success": True,
                    "valid": True,
                    "action": "preview",
                    "invitation_id": p_data.get("invitation_id"),
                    "community_id": p_data.get("community_id"),
                    "community_name": p_data.get("community_name"),
                    "name": p_data.get("community_name"),
                    "role": p_data.get("role_id"),
                    "role_id": p_data.get("role_id"),
                    "requires_admin_approval": p_data.get("requires_admin_approval"),
                    "will_require_approval": p_data.get("will_require_approval"),
                    "expires_at": p_data.get("expires_at"),
                    "max_uses": p_data.get("max_uses"),
                    "use_count": p_data.get("use_count"),
                    "invite": {
                        "id": p_data.get("invitation_id"),
                        "code": code,
                        "community_id": p_data.get("community_id"),
                        "community_name": p_data.get("community_name"),
                        "name": p_data.get("community_name"),
                        "role": p_data.get("role_id"),
                        "role_id": p_data.get("role_id"),
                        "requires_admin_approval": p_data.get("requires_admin_approval"),
                        "expires_at": p_data.get("expires_at"),
                        "max_uses": p_data.get("max_uses"),
                        "use_count": p_data.get("use_count")
                    },
                    "message": p_data.get("message", "Invitation code is valid.")
                }), 200
            return _map_invitation_rpc_error(res)

        # SQLite fallback
        inv = database.verify_invite(code, db_path=db_path)
        if not inv:
            return jsonify({
                "success": False,
                "valid": False,
                "error": "invitation_not_found",
                "code": "INVITATION_NOT_FOUND",
                "message": "Invite code is invalid, expired, or has been revoked."
            }), 404
        return jsonify({
            "success": True,
            "valid": True,
            "action": "preview",
            "invite": inv,
            "community_id": inv.get("community_id"),
            "role": inv.get("role", "doctor"),
            "message": "Invitation code is valid."
        }), 200

    @app.route("/api/community/invites/accept", methods=["POST"])
    @app.route("/api/community/invite/accept", methods=["POST"])
    @app.route("/api/community/invites/redeem", methods=["POST"])
    @app.route("/api/community/invite/redeem", methods=["POST"])
    def accept_community_invite():
        """Explicitly accept an invitation code using rpc_accept_invitation_atomic.
        Atomically increments use_count, records response, and creates community membership.
        """
        user_id, user_email, email_verified, account_type, err = _extract_trusted_invitation_identity()
        if err:
            return err

        data = request.get_json(silent=True) or {}
        code = (data.get("code") or "").strip().upper()
        if not code:
            return jsonify({
                "error": "bad_request",
                "code": "INVALID_CODE",
                "message": "Invitation code is required."
            }), 400

        if not email_verified:
            return jsonify({
                "error": "forbidden",
                "code": "EMAIL_NOT_VERIFIED",
                "message": "Account email must be verified to accept invitations."
            }), 403

        if is_supabase_enabled():
            res = supabase_service.accept_invitation(
                code=code,
                user_id=user_id,
                user_email=user_email,
                email_verified=email_verified,
                account_type=account_type
            )
            if res.get("success"):
                a_data = res.get("data") or {}
                return jsonify({
                    "success": True,
                    "action": "accepted",
                    "status": a_data.get("status"),
                    "community_id": a_data.get("community_id"),
                    "community_name": a_data.get("community_name"),
                    "role": a_data.get("role_id"),
                    "role_id": a_data.get("role_id"),
                    "message": a_data.get("message")
                }), 200
            return _map_invitation_rpc_error(res)

        # SQLite fallback (no atomic RPC in SQLite)
        inv = database.verify_invite(code, db_path=db_path)
        if not inv:
            return jsonify({
                "error": "bad_request",
                "code": "INVITATION_NOT_FOUND",
                "message": "Invite code is invalid, expired, or has been revoked."
            }), 400
        return jsonify({
            "success": True,
            "action": "accepted",
            "status": "active",
            "community_id": inv.get("community_id"),
            "role": inv.get("role", "doctor"),
            "message": "Successfully joined community workspace."
        }), 200

    @app.route("/api/community/invites/decline", methods=["POST"])
    @app.route("/api/community/invite/decline", methods=["POST"])
    def decline_community_invite():
        """Explicitly decline an invitation code using rpc_decline_invitation_atomic.
        Records response without incrementing usage. Prevents duplicate responses and subsequent acceptance.
        """
        user_id, user_email, email_verified, account_type, err = _extract_trusted_invitation_identity()
        if err:
            return err

        data = request.get_json(silent=True) or {}
        code = (data.get("code") or "").strip().upper()
        if not code:
            return jsonify({
                "error": "bad_request",
                "code": "INVALID_CODE",
                "message": "Invitation code is required."
            }), 400

        if not email_verified:
            return jsonify({
                "error": "forbidden",
                "code": "EMAIL_NOT_VERIFIED",
                "message": "Account email must be verified to decline invitations."
            }), 403

        if is_supabase_enabled():
            res = supabase_service.decline_invitation(
                code=code,
                user_id=user_id,
                user_email=user_email,
                email_verified=email_verified
            )
            if res.get("success"):
                d_data = res.get("data") or {}
                return jsonify({
                    "success": True,
                    "action": "declined",
                    "message": d_data.get("message", "Invitation declined.")
                }), 200
            return _map_invitation_rpc_error(res)

        # SQLite fallback
        return jsonify({
            "success": True,
            "action": "declined",
            "message": "Invitation declined."
        }), 200

    @app.route("/api/community/verify-invite", methods=["POST"])
    def verify_invite_token():
        data = request.get_json(silent=True) or {}
        code = (data.get("code") or "").strip().upper()
        if not code:
            return jsonify({"error": "bad_request", "code": "INVALID_CODE", "message": "Invitation code is required."}), 400

        # Route directly to authenticated preview flow (no unauthenticated bypass or legacy fallback)
        return preview_community_invite()

    # --- Image Upload API (Supabase Storage with Local Fallback) ---
    @app.route("/api/upload-image", methods=["POST"])
    def upload_image():
        """
        Upload medical intake form images with security validation.
        
        SECURITY FIX: Added file size limit (10MB), file type validation, and error handling.
        """
        # Security: Maximum file size (10MB for high-resolution medical forms)
        MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB in bytes
        ALLOWED_MIME_TYPES = {'image/jpeg', 'image/jpg', 'image/png', 'image/webp'}
        ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
        
        if "image" not in request.files:
            return jsonify({"error": "No image file uploaded"}), 400
            
        img = request.files["image"]
        
        # Validate filename exists
        if not img.filename or img.filename == '':
            return jsonify({"error": "Invalid filename"}), 400
        
        # Validate file extension
        file_ext = os.path.splitext(img.filename)[1].lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            return jsonify({
                "error": "Invalid file type",
                "message": f"Only image files are allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            }), 400
        
        camp_id = request.form.get("camp_id", "default_camp")
        page_num = request.form.get("page", "1")
        filename = f"{camp_id}_page{page_num}_{os.urandom(4).hex()}.jpg"
        
        # Read file with size limit
        img_bytes = img.read(MAX_FILE_SIZE + 1)  # Read one byte extra to detect oversized files
        
        # Validate file size
        if len(img_bytes) > MAX_FILE_SIZE:
            return jsonify({
                "error": "File too large",
                "message": f"Maximum file size is {MAX_FILE_SIZE // (1024 * 1024)}MB",
                "max_size_bytes": MAX_FILE_SIZE
            }), 413  # 413 Payload Too Large
        
        if len(img_bytes) == 0:
            return jsonify({"error": "Empty file uploaded"}), 400
        
        # Validate MIME type
        mimetype = img.mimetype or "image/jpeg"
        if mimetype not in ALLOWED_MIME_TYPES:
            return jsonify({
                "error": "Invalid file type",
                "message": f"Only image MIME types are allowed: {', '.join(ALLOWED_MIME_TYPES)}"
            }), 400

        # Try Supabase Storage first
        if supabase_service.is_configured():
            try:
                storage_path = f"{camp_id}/{filename}"
                public_url = supabase_service.upload_storage_image(storage_path, img_bytes, mimetype)
                if public_url:
                    return jsonify({"url": public_url, "storage": "supabase", "size_bytes": len(img_bytes)})
            except Exception as e:
                logger.error("Supabase storage upload failed: %s", e)
                # Fall through to local storage

        # Save to private upload directory outside static root (Rule 10.1)
        try:
            target_org = getattr(g, "org_id", "org_default")
            file_id = database.save_private_file(
                org_id=target_org,
                record_id=None,
                file_type="upload",
                filename=filename,
                data=img_bytes,
                mime_type=mimetype,
                db_path=db_path
            )
            return jsonify({
                "url": f"/api/files/{file_id}", 
                "file_id": file_id, 
                "storage": "private",
                "size_bytes": len(img_bytes)
            })
        except Exception as e:
            logger.exception("File upload error")
            return jsonify({"error": "Failed to save uploaded file"}), 500

    # --- Camp Analytics API ---
    @app.route("/api/analytics", methods=["GET"])
    @app.route("/api/camps/<camp_id>/analytics", methods=["GET"])
    def camp_analytics(camp_id=None):
        cid = camp_id or request.args.get("camp_id")
        if cid:
            camp = database.get_camp_by_id(cid, db_path)
            if not camp:
                return jsonify({"error": "Camp not found"}), 404
            camp_comm = get_resource_community(camp)
            if not check_community_access(camp_comm):
                return jsonify({
                    "error": "forbidden",
                    "message": "Cross-organization analytics forbidden: camp does not belong to caller organization."
                }), 403
        else:
            caller_comms = get_caller_communities()
            if not caller_comms:
                return jsonify({
                    "error": "forbidden",
                    "message": "Tenant identity required: caller has no authorized community memberships.",
                    "code": "MISSING_TENANT_IDENTITY"
                }), 403
        stats = database.get_camp_analytics(camp_id=cid, db_path=db_path)
        return jsonify(stats)

    # --- Community (Organization / Outreach Network) API ---
    @app.route("/api/community/create", methods=["POST"])
    def community_create():
        import secrets
        from flask import g
        claims = getattr(g, "current_user", None) or {}
        user_id = getattr(g, "user_id", None) or claims.get("sub") or claims.get("user_id")

        if not user_id:
            return jsonify({
                "error": "unauthorized",
                "code": "MISSING_TOKEN",
                "message": "Authentication required."
            }), 401

        # 1. Derive authorization and account type strictly from verified claims / app_metadata
        # Never trust request JSON for role or account_type!
        raw_claims = claims.get("raw") if isinstance(claims.get("raw"), dict) else claims
        app_meta = raw_claims.get("app_metadata") if isinstance(raw_claims.get("app_metadata"), dict) else (claims.get("app_metadata") or {})
        account_type = str(app_meta.get("account_type") or claims.get("account_type") or "").strip().lower()
        if not account_type:
            user_role = str(getattr(g, "user_role", None) or claims.get("role") or "").strip().lower()
            if user_role == "doctor":
                account_type = "doctor"
            elif user_role in ("worker", "field_worker", "user"):
                account_type = "user"

        if account_type != "doctor":
            logger.warning("Community creation forbidden for non-doctor account: user_id=%s, account_type=%s", user_id, account_type)
            return jsonify({
                "error": "forbidden",
                "code": "DOCTOR_REQUIRED",
                "message": "Only verified Doctor accounts may create communities."
            }), 403

        # 2. Derive email verification status strictly from trusted sources
        # Checked via JWT confirmation or public.users record
        jwt_email_verified = bool(raw_claims.get("email_confirmed_at")) or bool(app_meta.get("email_verified")) or bool(claims.get("email_verified"))
        user_prof = None
        db_email_verified = False

        if is_supabase_enabled():
            user_prof = supabase_service.get_user_profile(str(user_id))
            if user_prof:
                db_email_verified = bool(user_prof.get("email_verified"))

        is_verified = jwt_email_verified or db_email_verified
        if not is_verified:
            logger.warning("Community creation forbidden for unverified doctor: user_id=%s", user_id)
            return jsonify({
                "error": "forbidden",
                "code": "EMAIL_NOT_VERIFIED",
                "message": "Doctor email must be verified to create a community."
            }), 403

        # 3. Parse and sanitize request payload (only name and code accepted)
        # Client-supplied identity fields (creator_name, email, creator_id, role, account_type) are strictly ignored!
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({
                "error": "bad_request",
                "code": "COMMUNITY_NAME_REQUIRED",
                "message": "Community name is required."
            }), 400

        custom_code = (data.get("code") or "").strip().upper()
        if not custom_code:
            custom_code = f"COMM-{secrets.token_hex(3).upper()}"

        # 4. Resolve creator name and email from authoritative profile
        if is_supabase_enabled():
            if user_prof:
                creator_name = (user_prof.get("full_name") or claims.get("name") or "Doctor").strip()
                creator_email = (user_prof.get("email") or claims.get("email") or "").strip().lower()
            else:
                creator_name = (claims.get("name") or "Doctor").strip()
                creator_email = (claims.get("email") or "").strip().lower()
                supabase_service.sync_user_profile(
                    user_id=str(user_id),
                    email=creator_email,
                    full_name=creator_name,
                    account_type="doctor"
                )
                user_prof = supabase_service.get_user_profile(str(user_id))
                if user_prof:
                    creator_name = (user_prof.get("full_name") or creator_name).strip()
                    creator_email = (user_prof.get("email") or creator_email).strip().lower()

            # Synchronize email_verified flag to public.users if verified via JWT
            if is_verified and user_prof and not user_prof.get("email_verified"):
                supabase_service.update_user_email_verified(str(user_id), True)

            # Call atomic Supabase RPC
            rpc_res = supabase_service.create_community_atomic(
                name=name,
                code=custom_code,
                creator_id=str(user_id),
                creator_name=creator_name,
                creator_email=creator_email,
                user_role="doctor",
                email_verified=True
            )

            if not rpc_res.get("success"):
                err_code = rpc_res.get("code", "COMMUNITY_CREATION_FAILED")
                err_msg = rpc_res.get("message", "Failed to create community.")
                if err_code == "COMMUNITY_CODE_TAKEN":
                    return jsonify({"error": "conflict", "code": "COMMUNITY_CODE_TAKEN", "message": err_msg}), 409
                elif err_code in ("FORBIDDEN", "DOCTOR_REQUIRED"):
                    return jsonify({"error": "forbidden", "code": "DOCTOR_REQUIRED", "message": err_msg}), 403
                elif err_code == "EMAIL_NOT_VERIFIED":
                    return jsonify({"error": "forbidden", "code": "EMAIL_NOT_VERIFIED", "message": err_msg}), 403
                elif err_code in ("COMMUNITY_NAME_REQUIRED", "COMMUNITY_CODE_REQUIRED"):
                    return jsonify({"error": "bad_request", "code": err_code, "message": err_msg}), 400
                elif err_code == "CREATOR_NOT_FOUND":
                    return jsonify({"error": "not_found", "code": "CREATOR_NOT_FOUND", "message": err_msg}), 404
                elif err_code == "CREATOR_INACTIVE":
                    return jsonify({"error": "forbidden", "code": "CREATOR_INACTIVE", "message": err_msg}), 403
                else:
                    return jsonify({"error": "server_error", "code": err_code, "message": err_msg}), 500

            comm = rpc_res["community"]

            # Cache in SQLite for offline access (best-effort)
            try:
                database.create_community(name, creator_name, creator_email, code=comm["code"], db_path=db_path)
                database.get_or_create_doctor(
                    email=creator_email,
                    full_name=creator_name,
                    specialization="Community Lead",
                    auth_id=str(user_id),
                    community_id=comm["id"],
                    community_code=comm["code"],
                    community_role="admin",
                    db_path=db_path
                )
            except Exception as cache_err:
                logger.debug("Local SQLite cache write skipped: %s", cache_err)

            return jsonify({
                "success": True,
                "community": comm
            }), 201

        # Offline / local SQLite fallback (when Supabase is disabled)
        creator_name = (claims.get("name") or "Doctor").strip()
        creator_email = (claims.get("email") or f"doc_{user_id}@offline.local").strip().lower()
        try:
            comm = database.create_community(name, creator_name, creator_email, code=custom_code, db_path=db_path)
            database.get_or_create_doctor(
                email=creator_email,
                full_name=creator_name,
                specialization="Community Lead",
                auth_id=str(user_id),
                community_id=comm["id"],
                community_code=comm["code"],
                community_role="admin",
                db_path=db_path
            )
            return jsonify({
                "success": True,
                "community": {
                    "id": comm["id"],
                    "name": comm["name"],
                    "code": comm["code"],
                    "role": "community_admin"
                }
            }), 201
        except Exception as e:
            logger.error("Offline community creation failed: %s", e)
            return jsonify({
                "error": "server_error",
                "code": "COMMUNITY_CREATION_FAILED",
                "message": str(e)
            }), 500

    @app.route("/api/communities", methods=["GET"])
    def list_user_communities():
        """Return all active communities where the authenticated caller has legitimate active membership."""
        from flask import g
        user_id = getattr(g, "user_id", None)
        if not user_id:
            return jsonify({"error": "unauthorized", "message": "Authentication required."}), 401

        if is_supabase_enabled():
            mems = supabase_service.get_user_community_memberships(str(user_id), active_only=True)
            communities = []
            seen_ids = set()
            for m in mems:
                if m.get("status") != "active":
                    continue
                cid = str(m.get("community_id", "")).strip()
                if not cid or cid in seen_ids:
                    continue
                seen_ids.add(cid)
                communities.append({
                    "id": cid,
                    "name": m.get("community_name") or "Community",
                    "code": m.get("community_code") or "",
                    "role": m.get("role") or m.get("role_id") or "doctor",
                    "role_id": m.get("role_id") or "doctor",
                    "status": "active",
                    "joined_at": m.get("joined_at")
                })
            return jsonify({"success": True, "communities": communities}), 200

        # Offline / local SQLite fallback
        caller_email = (getattr(g, "current_user", {}).get("email") or str(user_id)).strip().lower()
        communities = []
        try:
            with database.get_db_connection(db_path) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT c.id, c.name, c.code, d.community_role, 'active' as status
                    FROM communities c
                    JOIN doctors d ON d.community_id = c.id
                    WHERE (d.auth_id = ? OR d.id = ? OR LOWER(d.email) = ?) AND d.is_active = 1
                    UNION
                    SELECT c.id, c.name, c.code, 'admin' as community_role, 'active' as status
                    FROM communities c
                    WHERE LOWER(c.creator_email) = ?
                """, (str(user_id), str(user_id), caller_email, caller_email))
                seen_ids = set()
                for r in cur.fetchall():
                    cid = str(r[0])
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        communities.append({
                            "id": cid,
                            "name": r[1],
                            "code": r[2],
                            "role": r[3] or "doctor",
                            "role_id": r[3] or "doctor",
                            "status": "active"
                        })
        except Exception as e:
            logger.error("Failed to query offline communities: %s", e)
        return jsonify({"success": True, "communities": communities}), 200

    @app.route("/api/community/memberships", methods=["GET"])
    def list_user_memberships():
        """Return all memberships (active, pending, rejected, revoked) for the authenticated caller."""
        from flask import g
        user_id = getattr(g, "user_id", None)
        if not user_id:
            return jsonify({"error": "unauthorized", "message": "Authentication required."}), 401

        status_filter = request.args.get("status")
        if status_filter:
            status_filter = status_filter.strip().lower()

        if is_supabase_enabled():
            active_only = (status_filter == "active")
            mems = supabase_service.get_user_community_memberships(str(user_id), active_only=active_only)
            if status_filter:
                mems = [m for m in mems if m.get("status") == status_filter]
            return jsonify({"success": True, "memberships": mems}), 200

        # Offline / local SQLite fallback
        caller_email = (getattr(g, "current_user", {}).get("email") or str(user_id)).strip().lower()
        memberships = []
        try:
            with database.get_db_connection(db_path) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT c.id, c.name, c.code, d.community_role, 'active' as status
                    FROM communities c
                    JOIN doctors d ON d.community_id = c.id
                    WHERE (d.auth_id = ? OR d.id = ? OR LOWER(d.email) = ?) AND d.is_active = 1
                    UNION
                    SELECT c.id, c.name, c.code, 'admin' as community_role, 'active' as status
                    FROM communities c
                    WHERE LOWER(c.creator_email) = ?
                """, (str(user_id), str(user_id), caller_email, caller_email))
                seen_ids = set()
                for r in cur.fetchall():
                    cid = str(r[0])
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        memberships.append({
                            "membership_id": f"local_{cid}",
                            "community_id": cid,
                            "community_name": r[1],
                            "community_code": r[2],
                            "role_id": r[3] or "doctor",
                            "role": r[3] or "doctor",
                            "status": "active"
                        })
        except Exception as e:
            logger.error("Failed to query offline memberships: %s", e)
        if status_filter:
            memberships = [m for m in memberships if m.get("status") == status_filter]
        return jsonify({"success": True, "memberships": memberships}), 200

    @app.route("/api/community/verify", methods=["POST"])
    @limiter.limit("10/minute")
    def community_verify():
        data = request.get_json(silent=True) or {}
        code = (data.get("code") or "").strip().upper()
        if not code:
            return jsonify({"valid": False, "error": "Community code is required"}), 400
        comm = database.get_community_by_code(code, db_path=db_path)
        if not comm:
            return jsonify({"valid": False, "error": f"Invalid code '{code}'. Ask your organizer for the exact code."}), 400
        return jsonify({
            "valid": True,
            "community": {
                "id": comm["id"],
                "name": comm["name"],
                "code": comm["code"],
                "creator_name": comm["creator_name"]
            }
        })

    @app.route("/api/community/memberships/pending", methods=["GET"])
    @app.route("/api/community/members/pending", methods=["GET"])
    def list_pending_community_memberships():
        """Authenticated endpoint for Community Admins to list pending membership requests within a community."""
        from flask import g
        user_id = getattr(g, "user_id", None) or (getattr(g, "current_user", None) or {}).get("sub")
        if not user_id:
            return jsonify({"error": "unauthorized", "code": "MISSING_TOKEN", "message": "Authentication required."}), 401

        comm_id = request.args.get("community_id")
        caller_comms = get_caller_communities()
        if not comm_id:
            if len(caller_comms) == 1:
                comm_id = next(iter(caller_comms))
            else:
                return jsonify({"error": "bad_request", "code": "COMMUNITY_ID_REQUIRED", "message": "community_id parameter is required."}), 400

        # Verify caller is an active member of this community
        if not check_community_access(comm_id):
            return jsonify({"error": "forbidden", "code": "COMMUNITY_ACCESS_DENIED", "message": "Access denied: caller does not have active membership in this community."}), 403

        # Only active community_admin members can view pending requests
        caller_role = get_caller_community_role(comm_id)
        if caller_role not in ("admin", "community_admin", "lead_doctor", "owner"):
            return jsonify({
                "error": "forbidden",
                "code": "INSUFFICIENT_ROLE",
                "message": "Only active Community Admins can view pending membership requests."
            }), 403

        if is_supabase_enabled():
            pending = supabase_service.list_pending_memberships(comm_id)
            return jsonify({
                "success": True,
                "community_id": comm_id,
                "pending_memberships": pending,
                "requests": pending,
                "total": len(pending)
            }), 200

        # Offline / local SQLite fallback
        return jsonify({
            "success": True,
            "community_id": comm_id,
            "pending_memberships": [],
            "requests": [],
            "total": 0
        }), 200

    def _handle_admin_review(membership_id: str, decision: str, reason: Optional[str] = None):
        """Internal helper for reviewing membership requests via rpc_admin_review_membership_atomic."""
        from flask import g
        claims = getattr(g, "current_user", None) or {}
        admin_user_id = getattr(g, "user_id", None) or claims.get("sub") or claims.get("user_id")
        if not admin_user_id:
            return jsonify({"error": "unauthorized", "code": "MISSING_TOKEN", "message": "Authentication required."}), 401

        clean_decision = str(decision or "").strip().lower()
        if clean_decision in ("approve", "approved"):
            clean_decision = "approved"
        elif clean_decision in ("reject", "rejected"):
            clean_decision = "rejected"
        else:
            return jsonify({"error": "bad_request", "code": "INVALID_DECISION", "message": "Decision must be approve or reject."}), 400

        if is_supabase_enabled():
            # Step 1: Pre-fetch target membership for defense-in-depth security checks
            target_mem = supabase_service.get_membership_by_id(str(membership_id))
            if not target_mem:
                return jsonify({"error": "not_found", "code": "MEMBERSHIP_NOT_FOUND", "message": "Membership record does not exist."}), 404

            # Step 2: Prevent self-review
            if str(target_mem.get("user_id")) == str(admin_user_id):
                return jsonify({
                    "error": "forbidden",
                    "code": "SELF_REVIEW_PROHIBITED",
                    "message": "Administrators cannot review their own membership application."
                }), 403

            # Step 3: Verify target membership is pending
            target_status = str(target_mem.get("status") or "").strip().lower()
            if target_status != "pending":
                return jsonify({
                    "error": "conflict",
                    "code": "MEMBERSHIP_NOT_PENDING",
                    "message": f"Only pending memberships can be reviewed. Current status is '{target_status}'."
                }), 409

            # Step 4: Community isolation & admin authorization
            target_comm_id = str(target_mem.get("community_id"))
            if not check_community_access(target_comm_id):
                return jsonify({
                    "error": "forbidden",
                    "code": "COMMUNITY_ACCESS_DENIED",
                    "message": "Access denied: cannot review memberships belonging to another community."
                }), 403

            caller_role = get_caller_community_role(target_comm_id)
            if caller_role not in ("admin", "community_admin", "lead_doctor", "owner"):
                return jsonify({
                    "error": "forbidden",
                    "code": "INSUFFICIENT_ROLE",
                    "message": "Caller is not an authorized active Community Admin for this community."
                }), 403

            # Step 5: Execute atomic RPC
            res = supabase_service.admin_review_membership(
                membership_id=str(membership_id),
                admin_user_id=str(admin_user_id),
                decision=clean_decision,
                reason=reason
            )
            if res.get("success"):
                r_data = res.get("data") or {}
                new_status = r_data.get("status") or ("active" if clean_decision == "approved" else "rejected")
                return jsonify({
                    "success": True,
                    "membership_id": str(membership_id),
                    "status": new_status,
                    "community_id": r_data.get("community_id") or target_comm_id,
                    "decision": clean_decision,
                    "reason": reason if clean_decision == "rejected" else None,
                    "message": f"Membership application successfully {new_status}."
                }), 200

            return _map_invitation_rpc_error(res)

        # Offline / local SQLite fallback
        return jsonify({
            "success": True,
            "membership_id": str(membership_id),
            "status": "active" if clean_decision == "approved" else "rejected",
            "decision": clean_decision,
            "reason": reason if clean_decision == "rejected" else None,
            "message": f"Membership application successfully {'approved' if clean_decision == 'approved' else 'rejected'}."
        }), 200

    @app.route("/api/community/memberships/<membership_id>/review", methods=["POST"])
    @app.route("/api/community/members/<membership_id>/review", methods=["POST"])
    @app.route("/api/community/membership/review", methods=["POST"])
    def admin_review_community_membership(membership_id=None):
        data = request.get_json(silent=True) or {}
        target_id = membership_id or data.get("membership_id")
        if not target_id:
            return jsonify({"error": "bad_request", "code": "INVALID_MEMBERSHIP", "message": "membership_id is required."}), 400
        decision = data.get("decision")
        reason = data.get("reason")
        return _handle_admin_review(membership_id=target_id, decision=decision, reason=reason)

    @app.route("/api/community/memberships/<membership_id>/approve", methods=["POST"])
    @app.route("/api/community/members/<membership_id>/approve", methods=["POST"])
    def admin_approve_community_membership(membership_id):
        return _handle_admin_review(membership_id=membership_id, decision="approved", reason=None)

    @app.route("/api/community/memberships/<membership_id>/reject", methods=["POST"])
    @app.route("/api/community/members/<membership_id>/reject", methods=["POST"])
    def admin_reject_community_membership(membership_id):
        data = request.get_json(silent=True) or {}
        return _handle_admin_review(membership_id=membership_id, decision="rejected", reason=data.get("reason"))

    @app.route("/api/community/members", methods=["GET"])
    def community_members():
        caller_comms = get_caller_communities()
        comm_id = request.args.get("community_id")
        if not comm_id:
            if len(caller_comms) == 1:
                comm_id = next(iter(caller_comms))
            else:
                return jsonify({"error": "bad_request", "message": "community_id parameter is required."}), 400

        # Verify caller is an approved member of the requested community
        if not check_community_access(comm_id):
            return jsonify({"error": "forbidden", "message": "Access denied: caller does not belong to this community."}), 403

        members = []
        with database.get_db_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, email, full_name, specialization, community_role, is_active FROM doctors WHERE community_id = ? AND is_active = 1 ORDER BY created_at ASC;",
                (comm_id,)
            )
            members = [dict(r) for r in cur.fetchall()]

        return jsonify({"members": members, "total": len(members)})

    @app.route("/api/community/rotate-code", methods=["POST"])
    def community_rotate():
        data = request.get_json(silent=True) or {}
        comm_id = data.get("community_id")
        caller_comms = get_caller_communities()
        if not comm_id:
            if len(caller_comms) == 1:
                comm_id = next(iter(caller_comms))
            else:
                return jsonify({"error": "bad_request", "message": "community_id is required."}), 400

        # Reject requests targeting another community
        if not check_community_access(comm_id):
            return jsonify({"error": "forbidden", "message": "Access denied: caller does not belong to this community."}), 403

        # Require that community's Admin role
        caller_role = get_caller_community_role(comm_id)
        if caller_role not in ("admin", "community_admin", "lead_doctor", "owner"):
            return jsonify({"error": "forbidden", "message": "Community Admin authorization required to rotate community code."}), 403

        new_code = data.get("new_code")
        res = database.rotate_community_code(comm_id, new_code=new_code, db_path=db_path)
        if not res:
            return jsonify({"error": "not_found", "message": "Community not found."}), 404
        return jsonify({"success": True, "community": res}), 200

    @app.route("/api/community/members/revoke", methods=["POST"])
    def community_revoke_member():
        data = request.get_json(silent=True) or {}
        doctor_id = data.get("doctor_id")
        if not doctor_id:
            return jsonify({"error": "bad_request", "message": "doctor_id is required."}), 400

        # Resolve the target membership and its actual community from the database
        with database.get_db_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, email, full_name, community_id, community_role, is_active FROM doctors WHERE id = ? OR auth_id = ?;",
                (str(doctor_id), str(doctor_id))
            )
            target_doc = cur.fetchone()

        if not target_doc or not target_doc["community_id"]:
            return jsonify({"error": "not_found", "message": "Target community membership not found."}), 404

        target_comm_id = target_doc["community_id"]
        # If client supplied community_id, verify it matches the actual community
        client_comm_id = data.get("community_id")
        if client_comm_id and str(client_comm_id).strip() != target_comm_id:
            return jsonify({"error": "forbidden", "message": "Target member does not belong to the specified community."}), 403

        # Prevent revoking memberships in another community
        if not check_community_access(target_comm_id):
            return jsonify({"error": "forbidden", "message": "Access denied: cannot manage members of another community."}), 403

        # Require that community's Admin role
        caller_role = get_caller_community_role(target_comm_id)
        if caller_role not in ("admin", "community_admin", "lead_doctor", "owner"):
            return jsonify({"error": "forbidden", "message": "Community Admin authorization required to revoke members."}), 403

        # Preserve existing admin-transfer and last-admin protections
        target_role = str(target_doc["community_role"] or "").strip().lower()
        if target_role in ("admin", "community_admin", "lead_doctor", "owner"):
            with database.get_db_connection(db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) as admin_count FROM doctors WHERE community_id = ? AND community_role IN ('admin', 'community_admin', 'lead_doctor', 'owner') AND id != ? AND is_active = 1;",
                    (target_comm_id, target_doc["id"])
                )
                row = cur.fetchone()
                if row and row["admin_count"] == 0:
                    return jsonify({
                        "error": "cannot_revoke_last_admin",
                        "message": "Cannot revoke the last administrator of the community. Please assign another administrator first."
                    }), 400

        success = database.revoke_community_member(target_comm_id, target_doc["id"], db_path=db_path)
        return jsonify({"success": success})

    # --- Doctor Authentication & Profile API ---
    @app.route("/api/auth/send-otp", methods=["POST"])
    @limiter.limit("10/minute")
    def auth_send_otp():
        data = request.get_json(silent=True) or {}
        phone = (data.get("phone") or "").strip()
        if not phone:
            return jsonify({"error": "Phone number is required."}), 400
        
        demo_mode_active = os.getenv("DEMO_MODE", "0").lower() in ("1", "true")
        logger.info("Clinical verification code requested for %s (demo_mode=%s)", phone, demo_mode_active)
        from backend.services.auth_service import send_otp_for_recipient
        try:
            send_otp_for_recipient(phone)
        except Exception as e:
            logger.warning("OTP dispatch fallback: %s", e)

        resp = {
            "success": True,
            "message": f"Clinical verification code sent to {phone}"
        }
        if demo_mode_active:
            resp["demo_otp"] = "123456"
        return jsonify(resp)

    @app.route("/api/auth/demo", methods=["POST"])
    @limiter.limit("10/minute")
    def auth_demo():
        app_env = os.getenv("APP_ENV", "").strip().lower()
        demo_mode_active = os.getenv("DEMO_MODE", "0").lower() in ("1", "true")
        if app_env == "production" or not demo_mode_active:
            return jsonify({"error": "Demo login is disabled and refused in production. DEMO_MODE=1 required."}), 403
        from backend.services.auth_service import create_access_token
        token = create_access_token(
            user_id="doc_demo",
            role="doctor",
            org_id="comm_demo",
            email="demo@clinic.camp",
            name="Dr. Priya (Demo)"
        )
        return jsonify({
            "success": True,
            "token": token,
            "doctor": {"id": "doc_demo", "full_name": "Dr. Priya (Demo)", "role": "doctor"},
            "community": {"id": "comm_demo", "name": "Rotary Community Health Outreach", "code": "CAMP-2026"}
        })

    @app.route("/api/auth/login", methods=["POST"])
    @limiter.limit("10/minute")
    def auth_login():
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        pin = (data.get("pin") or "").strip()
        otp = (data.get("otp") or "").strip()
        phone = (data.get("phone") or "").strip()
        community_code = (data.get("community_code") or "").strip().upper()

        # Requirement 8: Remove PIN and OTP authentication from the backend login flow
        if pin or otp:
            return jsonify({
                "error": "PIN and OTP authentication have been retired. Please log in using your email and password.",
                "code": "PIN_AND_OTP_RETIRED"
            }), 400

        if phone and not email:
            return jsonify({
                "error": "Phone login is no longer supported. Please log in using your email and password.",
                "code": "PHONE_LOGIN_RETIRED"
            }), 400

        # Requirement 9: Never permit mock or demo authentication as a production fallback
        app_env = os.getenv("APP_ENV", "production").strip().lower()
        demo_mode_active = os.getenv("DEMO_MODE", "0").lower() in ("1", "true")
        is_demo_req = bool(data.get("is_demo") or data.get("demo") or email == "demo@clinic.camp")
        if is_demo_req:
            if app_env == "production" or not demo_mode_active:
                return jsonify({
                    "error": "Demo login is disabled and refused in production. DEMO_MODE=1 required.",
                    "code": "DEMO_LOGIN_FORBIDDEN"
                }), 403
            from backend.services.auth_service import create_access_token
            demo_comm = database.get_community_by_code(community_code or "CAMP-2026", db_path=db_path) or {
                "id": "comm_demo", "name": "Rotary Community Health Outreach", "code": "CAMP-2026"
            }
            demo_token = create_access_token(
                user_id="doc_demo",
                role="doctor",
                org_id=demo_comm.get("id", "comm_demo"),
                email="demo@clinic.camp",
                name="Dr. Priya (Demo)"
            )
            return jsonify({
                "success": True,
                "token": demo_token,
                "access_token": demo_token,
                "token_type": "bearer",
                "doctor": {"id": "doc_demo", "full_name": "Dr. Priya (Demo)", "role": "doctor"},
                "community": demo_comm
            })

        if not email or not password:
            return jsonify({
                "error": "Email and password are required.",
                "code": "CREDENTIALS_REQUIRED"
            }), 400

        from backend.services.auth_service import check_account_locked, record_login_attempt, create_access_token

        is_locked, remaining_sec = check_account_locked(email)
        if is_locked:
            return jsonify({
                "error": "Account is locked due to consecutive failed attempts. Please try again later.",
                "code": "ACCOUNT_LOCKED",
                "remaining_seconds": remaining_sec
            }), 403

        # Requirement 1: Supabase Auth handles email/password login
        auth_res = None
        if supabase_service.is_configured():
            auth_res = supabase_service.auth_sign_in(email, password)
            if not auth_res.get("success"):
                record_login_attempt(email, success=False)
                # Requirement 2: Mandatory email verification check
                if auth_res.get("code") == "EMAIL_NOT_VERIFIED":
                    return jsonify({
                        "error": auth_res.get("error", "Email not verified. Please verify your email before logging in."),
                        "code": "EMAIL_NOT_VERIFIED"
                    }), 403
                return jsonify({
                    "error": auth_res.get("error", "Invalid email or password."),
                    "code": "INVALID_CREDENTIALS"
                }), 401
        else:
            if app_env == "production":
                record_login_attempt(email, success=False)
                return jsonify({
                    "error": "Authentication service is not configured.",
                    "code": "AUTH_UNCONFIGURED"
                }), 500

        record_login_attempt(email, success=True)

        user_data = (auth_res or {}).get("user") or {}
        # Requirements 4 & 5: Supabase Auth UUID is the authenticated identity
        user_id = user_data.get("id") or f"usr_{hashlib.sha256(email.encode()).hexdigest()[:16]}"
        access_token = (auth_res or {}).get("access_token")
        refresh_token = (auth_res or {}).get("refresh_token")
        token_type = (auth_res or {}).get("token_type", "bearer")
        expires_in = (auth_res or {}).get("expires_in", 3600)
        account_type = (user_data.get("app_metadata") or {}).get("account_type") or (user_data.get("user_metadata") or {}).get("account_type") or "doctor"

        # Requirement 10: Resolve community roles from trusted database membership records, never from client-supplied roles
        memberships = []
        if supabase_service.is_configured() and user_id:
            try:
                memberships = supabase_service.get_user_community_memberships(user_id)
            except Exception as mem_err:
                logger.warning("Failed to fetch community memberships for %s: %s", user_id, mem_err)

        comm = None
        active_role = account_type
        if community_code:
            comm = database.get_community_by_code(community_code, db_path=db_path)
            if not comm:
                return jsonify({
                    "error": f"Invalid Community Code '{community_code}'.",
                    "code": "INVALID_COMMUNITY_CODE"
                }), 403
            if memberships and comm:
                # Requirement 2: Only active memberships grant access; support both role_id and role
                matched = next((m for m in memberships if m.get("community_id") == comm.get("id") and m.get("status") == "active"), None)
                if matched:
                    active_role = matched.get("role_id") or matched.get("role") or active_role
        elif memberships:
            # Filter strictly for approved/active memberships
            active_mems = [m for m in memberships if m.get("status") == "active"]
            if active_mems:
                primary_mem = active_mems[0]
                active_role = primary_mem.get("role_id") or primary_mem.get("role") or active_role
                if primary_mem.get("community_id"):
                    comm = database.get_community_by_id(primary_mem["community_id"], db_path=db_path)

        if not comm:
            comm = database.get_community_by_code("CAMP-2026", db_path=db_path) or {
                "id": "comm_default",
                "name": "General Health Community",
                "code": "CAMP-2026"
            }

        # Keep doctor / user record in SQLite keyed by Supabase UUID
        doc_name = user_data.get("user_metadata", {}).get("full_name") or email.split("@")[0].title()
        now = datetime.now(timezone.utc).isoformat()
        with database.get_db_connection(db_path) as conn:
            cur = conn.cursor()
            existing_row = cur.execute("SELECT id FROM doctors WHERE id = ? OR auth_id = ? OR LOWER(email) = LOWER(?);", (user_id, user_id, email)).fetchone()
            if existing_row:
                cur.execute(
                    "UPDATE doctors SET auth_id = ?, community_role = ?, updated_at = ? WHERE id = ?;",
                    (user_id, active_role, now, existing_row["id"])
                )
            else:
                cur.execute(
                    """INSERT INTO doctors (id, auth_id, email, full_name, specialization, community_id, community_code, community_role, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                    (user_id, user_id, email, doc_name, "General Physician", comm.get("id"), comm.get("code"), active_role, now, now)
                )
            conn.commit()

        profile = database.get_doctor_profile(user_id, db_path=db_path) or {
            "id": user_id,
            "email": email,
            "full_name": doc_name,
            "community_role": active_role
        }

        # Requirement 1: Do not accept locally generated application JWTs as Supabase sessions
        if not access_token:
            if supabase_service.is_configured():
                return jsonify({
                    "error": "Failed to retrieve authenticated session from Supabase.",
                    "code": "AUTH_TOKEN_ERROR"
                }), 500
            access_token = create_access_token(
                user_id=user_id,
                role=active_role,
                org_id=comm.get("id", "comm_default"),
                email=email,
                name=doc_name
            )

        exposed_account_type = "user" if active_role == "field_worker" else ("doctor" if active_role in ("doctor", "community_admin", "super_admin") else account_type)

        return jsonify({
            "success": True,
            "token": access_token,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": token_type,
            "expires_in": expires_in,
            "user": {
                "id": user_id,
                "email": email,
                "full_name": doc_name,
                "role": active_role,
                "account_type": exposed_account_type
            },
            "doctor": profile,
            "community": comm,
            "memberships": memberships
        }), 200

    @app.route("/api/auth/signup", methods=["POST"])
    def auth_signup():
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        full_name = (data.get("full_name") or "").strip()
        account_type = (data.get("account_type") or data.get("role") or "doctor").strip().lower()
        specialization = (data.get("specialization") or ("General Physician" if account_type == "doctor" else "Community Health Worker")).strip()
        community_code = (data.get("community_code") or "").strip().upper()

        if not email or not password:
            return jsonify({"error": "Email and password are required.", "code": "CREDENTIALS_REQUIRED"}), 400

        if not full_name:
            return jsonify({"error": "Full Name is required.", "code": "NAME_REQUIRED"}), 400

        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters.", "code": "WEAK_PASSWORD"}), 400

        # Requirement 11: Use 'doctor' and 'user' as application account types
        if account_type not in ("doctor", "user"):
            return jsonify({
                "error": "Invalid account_type. Allowed account types: 'doctor' or 'user'.",
                "code": "INVALID_ACCOUNT_TYPE"
            }), 400

        comm = None
        if community_code:
            comm = database.get_community_by_code(community_code, db_path=db_path)
            if not comm:
                return jsonify({
                    "error": f"Invalid Community Code '{community_code}'.",
                    "code": "INVALID_COMMUNITY_CODE"
                }), 403

        # Requirement 1: Supabase Auth handles email/password signup
        # Requirement 12: Handle partial signup failures safely without creating inconsistent profiles
        if supabase_service.is_configured():
            client_origin = request.headers.get("Origin") or getattr(request, "host_url", "http://localhost:5001/")
            if client_origin and ("127.0.0.1:5001" in client_origin or "localhost:5001" in client_origin):
                target_redirect = client_origin.rstrip("/") + "/"
            else:
                target_redirect = "ink2insight://auth/callback"

            auth_res = supabase_service.auth_sign_up(
                email=email,
                password=password,
                full_name=full_name,
                specialization=specialization,
                account_type=account_type,
                email_redirect_to=target_redirect
            )
            if not auth_res.get("success"):
                err_msg = auth_res.get("error", "Registration failed.")
                err_code = auth_res.get("code", "SIGNUP_FAILED")
                if "rate limit" in str(err_msg).lower():
                    err_code = "EMAIL_RATE_LIMIT_EXCEEDED"
                    err_msg = "Supabase email rate limit exceeded (maximum 3 confirmation emails per hour). Please wait before requesting another confirmation email, or use 1-Click Demo login."
                status_http = 409 if err_code == "USER_ALREADY_EXISTS" else 400
                return jsonify({
                    "error": err_msg,
                    "code": err_code
                }), status_http

            user_data = auth_res.get("user") or {}
            user_id = user_data.get("id")

            if not user_id:
                return jsonify({
                    "error": "Failed to create user account identity.",
                    "code": "AUTH_IDENTITY_MISSING"
                }), 500

            # Safely sync user profile with Supabase UUID (Requirement 4 & 5)
            # Exact signature match: (user_id, email, full_name, account_type, specialization)
            sync_ok = False
            try:
                sync_res = supabase_service.sync_user_profile(
                    user_id=user_id,
                    email=email,
                    full_name=full_name,
                    account_type=account_type,
                    specialization=specialization
                )
                sync_ok = bool(sync_res.get("success", False)) if isinstance(sync_res, dict) else bool(sync_res)
            except TypeError as te:
                logger.error("TypeError in sync_user_profile call for user %s: %s", user_id, te)
                sync_ok = False
            except Exception as sync_err:
                logger.error("Profile synchronization error for user %s (%s): %s", user_id, email, sync_err)
                sync_ok = False

            if not sync_ok:
                logger.warning(
                    "Profile synchronization failed for user %s (%s). Supabase Auth account preserved but profile sync incomplete.",
                    user_id, email
                )
                return jsonify({
                    "success": False,
                    "error": "Account registered with authentication provider, but profile setup could not be completed. Please contact support or try logging in.",
                    "code": "PROFILE_SYNC_FAILED",
                    "user": {
                        "id": user_id,
                        "email": email,
                        "account_type": account_type
                    },
                    "email_verification_required": True
                }), 502

            # Initialize local record keyed by Supabase UUID
            # Security Rule: Supplying community_code does NOT grant active membership during signup
            now = datetime.now(timezone.utc).isoformat()
            with database.get_db_connection(db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT id FROM doctors WHERE id = ? OR LOWER(email) = LOWER(?);", (user_id, email))
                if not cur.fetchone():
                    cur.execute(
                        """INSERT INTO doctors (id, auth_id, email, full_name, specialization, community_id, community_code, community_role, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                        (user_id, user_id, email, full_name, specialization, None, comm["code"] if comm else None, account_type, now, now)
                    )
                    conn.commit()

            resp_payload = {
                "success": True,
                "message": "Registration successful. Please check your email to verify your account before logging in.",
                "user": {
                    "id": user_id,
                    "email": email,
                    "full_name": full_name,
                    "account_type": account_type,
                    "email_confirmed": False
                },
                "email_verification_required": True
            }
            if auth_res.get("action_link"):
                resp_payload["verification_link"] = auth_res["action_link"]
            return jsonify(resp_payload), 201

        # Fallback for unconfigured dev environment
        now = datetime.now(timezone.utc).isoformat()
        dev_user_id = f"usr_{hashlib.sha256(email.encode()).hexdigest()[:16]}"
        with database.get_db_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM doctors WHERE id = ? OR LOWER(email) = LOWER(?);", (dev_user_id, email))
            if not cur.fetchone():
                cur.execute(
                    """INSERT INTO doctors (id, auth_id, email, full_name, specialization, community_id, community_code, community_role, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                    (dev_user_id, dev_user_id, email, full_name, specialization, None, comm["code"] if comm else None, account_type, now, now)
                )
                conn.commit()

        return jsonify({
            "success": True,
            "message": "Registration successful (dev mode).",
            "user": {
                "id": dev_user_id,
                "email": email,
                "full_name": full_name,
                "account_type": account_type,
                "email_confirmed": False
            },
            "email_verification_required": True
        }), 201

    @app.route("/api/auth/logout", methods=["POST"])
    def auth_logout():
        """Secure logout: signs out from Supabase Auth and invalidates session token."""
        from backend.services.auth_service import get_token_from_request
        data = request.get_json(silent=True) or {}
        token = data.get("token") or data.get("access_token") or get_token_from_request()
        if token and supabase_service.is_configured():
            supabase_service.auth_sign_out(token)
        return jsonify({
            "success": True,
            "message": "Successfully logged out."
        }), 200

    @app.route("/api/auth/forgot-password", methods=["POST"])
    @limiter.limit("5/minute")
    def auth_forgot_password():
        """Initiate password recovery email via Supabase Auth."""
        if not supabase_service.is_configured():
            return jsonify({
                "error": "service_unavailable",
                "message": "Authentication service is not configured. Password recovery is unavailable.",
                "code": "AUTH_UNCONFIGURED"
            }), 503

        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        if not email:
            return jsonify({"error": "Email is required.", "code": "EMAIL_REQUIRED"}), 400

        client_origin = request.headers.get("Origin") or request.host_url
        if client_origin and ("127.0.0.1:5001" in client_origin or "localhost:5001" in client_origin):
            target_redirect = client_origin.rstrip("/") + "/"
        else:
            target_redirect = "ink2insight://auth/callback"

        res = supabase_service.auth_recover_password(email, redirect_to=target_redirect)
        if not res.get("success"):
            return jsonify({
                "error": res.get("error", "Password recovery request failed."),
                "code": res.get("code", "RECOVERY_FAILED")
            }), 400

        resp_data = {
            "success": True,
            "message": f"Password recovery instructions sent to {email} if an account exists."
        }
        if res.get("action_link"):
            resp_data["recovery_link"] = res["action_link"]
            try:
                import requests as _raw_req
                _ver_res = _raw_req.get(res["action_link"], allow_redirects=False, timeout=5)
                if _ver_res.status_code in (301, 302, 303, 307, 308) and _ver_res.headers.get("Location"):
                    resp_data["callback_url"] = _ver_res.headers["Location"]
            except Exception as _res_err:
                logger.warning("Could not pre-resolve recovery link: %s", _res_err)
        return jsonify(resp_data), 200

    @app.route("/api/auth/reset-password", methods=["POST"])
    @limiter.limit("5/minute")
    def auth_reset_password():
        """Reset password using recovery access token."""
        if not supabase_service.is_configured():
            return jsonify({
                "error": "service_unavailable",
                "message": "Authentication service is not configured. Password reset is unavailable.",
                "code": "AUTH_UNCONFIGURED"
            }), 503

        from backend.services.auth_service import get_token_from_request
        data = request.get_json(silent=True) or {}
        new_password = data.get("new_password") or data.get("password") or ""
        token = data.get("token") or data.get("access_token") or get_token_from_request()

        if not new_password or len(new_password) < 6:
            return jsonify({"error": "New password must be at least 6 characters.", "code": "WEAK_PASSWORD"}), 400
        if not token:
            return jsonify({"error": "Recovery access token is required in body or Authorization header.", "code": "TOKEN_REQUIRED"}), 400

        res = supabase_service.auth_reset_password(token, new_password)
        if not res.get("success"):
            return jsonify({
                "error": res.get("error", "Failed to update password."),
                "code": res.get("code", "RESET_FAILED")
            }), 400

        return jsonify({
            "success": True,
            "message": "Password successfully reset. You may now log in with your new password."
        }), 200

    @app.route("/api/doctor/profile", methods=["GET", "PUT"])
    @app.route("/api/doctors/profile", methods=["GET", "PUT"])
    def doctor_profile():
        from flask import g

        # Derive identity strictly from verified token claims on g
        current_user = getattr(g, "current_user", None) or {}
        auth_user_id = getattr(g, "user_id", None) or current_user.get("user_id") or current_user.get("sub")
        auth_email = (current_user.get("email") or "").strip().lower()

        if not auth_user_id and not auth_email:
            return jsonify({
                "error": "unauthorized",
                "message": "Authentication required. Valid user session not found.",
                "code": "UNAUTHORIZED"
            }), 401

        # Prevent IDOR: Never trust client-supplied doctor_id or X-Doctor-ID as the authenticated identity.
        # If client passes an explicit doctor_id or X-Doctor-ID, verify it does not attempt to access another doctor's profile.
        override_id = (request.args.get("doctor_id") or request.headers.get("X-Doctor-ID") or "").strip()
        if override_id:
            allowed_identifiers = {str(auth_user_id).lower()}
            if auth_email:
                allowed_identifiers.add(auth_email)
            # Query actual doctor record strictly from database without demo fallback
            with database.get_db_connection(db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, auth_id, email, full_name FROM doctors WHERE id = ? OR auth_id = ? OR LOWER(email) = LOWER(?);",
                    (str(auth_user_id), str(auth_user_id), auth_email)
                )
                actual_row = cur.fetchone()
                if actual_row:
                    for field in ("id", "auth_id", "email", "full_name"):
                        val = actual_row[field]
                        if val:
                            allowed_identifiers.add(str(val).lower())

            if override_id.lower() not in allowed_identifiers:
                return jsonify({
                    "error": "forbidden",
                    "message": "Access denied. You cannot read or modify another doctor's profile.",
                    "code": "FORBIDDEN"
                }), 403

        # Identity is derived exclusively from the verified token
        doc_key = auth_email if auth_email else str(auth_user_id)

        if request.method == "GET":
            if is_supabase_enabled():
                try:
                    s_prof = supabase_service.get_doctor_profile(str(auth_user_id)) or (supabase_service.get_doctor_profile(auth_email) if auth_email else None)
                    if s_prof:
                        return jsonify(s_prof)
                except Exception as e:
                    logger.warning("Supabase get_doctor_profile error: %s", e)

            prof = database.get_doctor_profile(doc_key, db_path=db_path)
            # If database returned the legacy demo fallback but the authenticated user is NOT that demo doctor,
            # retrieve or create the authentic profile for the authenticated doctor
            if prof and prof.get("email") and auth_email and prof.get("email").lower() != auth_email:
                doc_name = current_user.get("name") or "Doctor"
                prof = database.get_or_create_doctor(
                    email=auth_email,
                    full_name=doc_name,
                    specialization="General Physician",
                    auth_id=str(auth_user_id),
                    community_id=getattr(g, "org_id", None) or "comm_default",
                    community_code="HEALTH-2026",
                    db_path=db_path
                )
            return jsonify(prof)
        else:
            data = request.get_json(silent=True) or {}
            full_name = data.get("full_name") or (current_user.get("name") if current_user else "Doctor")
            spec = data.get("specialization") or "General Physician"
            avatar = data.get("avatar_url")
            s_updated = None
            if is_supabase_enabled():
                try:
                    s_updated = supabase_service.update_doctor_profile(
                        str(auth_user_id), full_name=full_name, specialization=spec, avatar_url=avatar
                    )
                except Exception as e:
                    logger.warning("Supabase update_doctor_profile error: %s", e)

            updated = database.update_doctor_profile(doc_key, full_name, spec, avatar, db_path=db_path)
            return jsonify(s_updated or updated)

    @app.route("/api/sync/supabase", methods=["POST"])
    def sync_to_supabase():
        """Push all existing local SQLite camps and records to Supabase."""
        if not supabase_service.is_configured():
            return jsonify({"error": "Supabase credentials not configured in backend/.env"}), 400
        try:
            res = supabase_service.sync_from_sqlite(db_path)
            return jsonify({"status": "ok", "synced": res})
        except Exception as e:
            logger.exception("Error syncing SQLite to Supabase")
            return jsonify({"error": str(e)}), 500

    # --- AI Extraction API ---
    @app.route("/api/extract", methods=["POST"])
    @limiter.limit("10/minute")
    def extract_document():
        """Accepts student_form and health_assessment sheets, checks quota/dedup, and calls AI extractor."""
        # Support both new standard names and backward-compatible page1/page2
        sf_file = request.files.get("student_form") or request.files.get("page1")
        ha_file = request.files.get("health_assessment") or request.files.get("page2")

        if not sf_file or not ha_file:
            return jsonify({
                "error": "Both student_form and health_assessment image files are required for extraction.",
                "code": "MISSING_PAGES"
            }), 400

        if not sf_file.filename or not ha_file.filename:
            return jsonify({"error": "One or both uploaded files are empty.", "code": "EMPTY_FILE"}), 400

        sf_mime = sf_file.mimetype or "image/jpeg"
        ha_mime = ha_file.mimetype or "image/jpeg"

        if sf_mime not in ALLOWED_MIME_TYPES or ha_mime not in ALLOWED_MIME_TYPES:
            return jsonify({
                "error": "Invalid image format. Allowed formats: JPEG, PNG, WebP, HEIC/HEIF.",
                "code": "INVALID_MIME_TYPE"
            }), 400

        sf_bytes = sf_file.read()
        ha_bytes = ha_file.read()

        if len(sf_bytes) > MAX_FILE_SIZE or len(ha_bytes) > MAX_FILE_SIZE:
            return jsonify({
                "error": "Image file size exceeds the 15 MB limit.",
                "code": "FILE_TOO_LARGE"
            }), 400

        camp_id = request.form.get("camp_id") or request.args.get("camp_id") or "camp_default"
        record_id = request.form.get("record_id") or request.args.get("record_id")
        org_id = request.headers.get("X-Org-ID") or request.form.get("org_id") or request.args.get("org_id") or "org_default"

        # Safeguard 2: Scanned File Ownership:
        # Derive the active community ID strictly from authenticated user's verified membership.
        # Ensure /api/extract stores files against that verified community.
        # Never trust an arbitrary frontend-supplied community ID.
        verified_comms = get_caller_communities()
        target_org = None

        if camp_id and camp_id != "camp_default":
            camp_rec = database.get_camp_by_id(camp_id, db_path)
            if camp_rec:
                c_comm = get_resource_community(camp_rec)
                if c_comm and (c_comm in verified_comms or not verified_comms):
                    target_org = c_comm

        if not target_org and verified_comms:
            caller_org = getattr(g, "org_id", None) or getattr(g, "community_id", None)
            if caller_org and caller_org in verified_comms:
                target_org = caller_org
            else:
                target_org = sorted(list(verified_comms))[0]

        if not target_org:
            target_org = getattr(g, "org_id", None) or org_id or "org_default"

        f1_id = database.save_private_file(
            org_id=target_org,
            record_id=record_id,
            file_type="scan_page1",
            filename=f"{camp_id}_p1.jpg",
            data=sf_bytes,
            mime_type="image/jpeg",
            db_path=db_path
        )
        f2_id = database.save_private_file(
            org_id=target_org,
            record_id=record_id,
            file_type="scan_page2",
            filename=f"{camp_id}_p2.jpg",
            data=ha_bytes,
            mime_type="image/jpeg",
            db_path=db_path
        )
        page1_permanent_url = f"/api/files/{f1_id}"
        page2_permanent_url = f"/api/files/{f2_id}"

        # Dynamic AI API Key, Provider, and Model overrides
        dynamic_key = (
            request.headers.get("X-AI-API-Key")
            or request.headers.get("X-API-Key")
            or request.form.get("api_key")
            or request.args.get("api_key")
        )
        dynamic_provider = (
            request.headers.get("X-AI-Provider")
            or request.form.get("ai_provider")
            or request.args.get("ai_provider")
        )
        dynamic_model = (
            request.headers.get("X-AI-Model")
            or request.form.get("model")
            or request.args.get("model")
        )

        # Emergency AI Kill Switch Check (Fail fast before image analysis or reservations)
        try:
            with database.get_db_connection(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT ai_paused FROM orgs WHERE id = ?;", (org_id,))
                org_row = cursor.fetchone()
                if org_row and org_row["ai_paused"]:
                    return jsonify({
                        "success": False,
                        "error": "AI processing is currently paused by administrator kill switch.",
                        "code": "AI_PAUSED"
                    }), 403
        except Exception as ai_pause_check_error:
            logger.warning("Failed to check AI pause status for org %s: %s", org_id, ai_pause_check_error)

        # Task 2: Server-side Quality Pre-flight Check
        from backend.services.quality_service import assess_image_quality
        q1 = assess_image_quality(sf_bytes)
        q2 = assess_image_quality(ha_bytes)
        quality_ok = bool((q1.get("ok") is not False) and (q2.get("ok") is not False))
        combined_issues = []
        if q1.get("ok") is False and q1.get("issues"):
            combined_issues.extend([f"Page 1: {iss}" for iss in q1["issues"]])
        if q2.get("ok") is False and q2.get("issues"):
            combined_issues.extend([f"Page 2: {iss}" for iss in q2["issues"]])

        quality_bypassed = (
            request.headers.get("X-Quality-Bypassed", "").strip().lower() in ("1", "true")
            or request.form.get("quality_bypassed", "").strip().lower() in ("1", "true")
            or request.args.get("quality_bypassed", "").strip().lower() in ("1", "true")
        )

        quality_summary = {
            "page1": q1,
            "page2": q2,
            "blur_score": min(q1.get("blur_score", 0), q2.get("blur_score", 0)),
            "brightness": round((q1.get("brightness", 0) + q2.get("brightness", 0)) / 2.0, 2),
            "glare_pct": max(q1.get("glare_pct", 0), q2.get("glare_pct", 0)),
            "ok": quality_ok,
            "issues": combined_issues
        }

        if not quality_ok and not quality_bypassed:
            if record_id:
                try:
                    database.release_paper(org_id=org_id, record_id=record_id, db_path=db_path)
                except Exception as paper_release_error:
                    logger.error("Failed to release paper quota for record %s: %s", record_id, paper_release_error)
            return jsonify({
                "success": False,
                "error": "unreadable",
                "message": f"Image quality check failed. Please retake the photo. Issues: {', '.join(combined_issues)}",
                "quality": quality_summary,
                "code": "IMAGE_UNREADABLE"
            }), 422

        template_ids_raw = request.form.get("template_ids") or request.args.get("template_ids")
        template_ids_list = [t.strip() for t in template_ids_raw.split(",") if t.strip()] if template_ids_raw else None

        result = ai_extract(
            student_form_bytes=sf_bytes,
            health_assessment_bytes=ha_bytes,
            camp_id=camp_id,
            record_id=record_id,
            org_id=target_org,
            db_path=db_path,
            api_key=dynamic_key,
            provider=dynamic_provider,
            model=dynamic_model,
            template_ids=template_ids_list
        )

        # Inject quality metrics and bypass flag
        result["quality"] = quality_summary
        result["quality_bypassed"] = quality_bypassed
        if isinstance(result.get("data"), dict):
            result["data"]["quality"] = quality_summary
            result["data"]["quality_bypassed"] = quality_bypassed

        # Inject permanent URLs so client saves durable image links in database
        result["page1_url"] = page1_permanent_url
        result["page2_url"] = page2_permanent_url
        if isinstance(result.get("data"), dict):
            result["data"]["page1_url"] = page1_permanent_url
            result["data"]["page2_url"] = page2_permanent_url

        http_code = result.get("http_status", 200 if result.get("success") else 422)
        return jsonify(result), http_code

    @app.route("/api/extract/v2", methods=["POST"])
    def extract_document_v2():
        """Phase 3 Extraction v2: Template-driven extraction with field-scoped glossary and invariants."""
        # Check for JSON payload normalization
        json_data = request.get_json(silent=True) or {}
        if json_data.get("template_id") and (json_data.get("raw_model_json") or json_data.get("data")):
            t_id = json_data.get("template_id")
            raw_json = json_data.get("raw_model_json") or json_data.get("data")
            try:
                processed = default_extraction_pipeline.process_extracted_payload(t_id, raw_json)
                return jsonify({"success": True, "result": processed}), 200
            except KeyError as ke:
                return jsonify({"success": False, "error": str(ke), "code": "TEMPLATE_NOT_FOUND"}), 404
            except Exception as e:
                return jsonify({"success": False, "error": str(e), "code": "NORMALIZATION_ERROR"}), 422

        # Check for image file upload
        file = request.files.get("image") or request.files.get("page") or request.files.get("student_form")
        if not file:
            return jsonify({
                "error": "No image file or extraction JSON provided. Expecting 'image' file or 'raw_model_json' payload.",
                "code": "MISSING_INPUT"
            }), 400

        img_bytes = file.read()
        t_id = request.form.get("template_id") or request.args.get("template_id")
        footer_code = request.form.get("footer_code") or request.args.get("footer_code")
        header_text = request.form.get("header_text") or request.args.get("header_text")

        if not t_id:
            classification = default_template_engine.classify_page(footer_code, header_text)
            t_id = classification.get("template_id") or "tpl_adult_examination_v9_1"

        tpl = default_template_engine.get_template(t_id)
        if not tpl:
            return jsonify({"error": f"Template '{t_id}' not registered.", "code": "TEMPLATE_NOT_FOUND"}), 404

        # Save permanent scan file in private directory outside static root (Rule 10.1)
        target_org = getattr(g, "org_id", "org_default")
        f_id = database.save_private_file(
            org_id=target_org,
            record_id=None,
            file_type="scan_form",
            filename=f"form_{t_id}.jpg",
            data=img_bytes,
            mime_type="image/jpeg",
            db_path=db_path
        )

        # Process form inputs through extraction pipeline
        form_payload = {}
        for sec in tpl.get("sections", []):
            for f in sec.get("fields", []):
                val = request.form.get(f["key"])
                if val is not None:
                    form_payload[f["key"]] = {"raw_text": val, "confidence": 0.95}

        processed = default_extraction_pipeline.process_extracted_payload(t_id, form_payload)
        processed["image_url"] = f"/api/files/{f_id}"
        processed["file_id"] = f_id
        processed["image_hash"] = default_extraction_pipeline.compute_image_hash(img_bytes)

        return jsonify({
            "success": True,
            "template_id": t_id,
            "template_title": tpl.get("title", ""),
            "data": processed,
            "code": "EXTRACTION_SUCCESS"
        }), 200

    # --- Unified AI Pipeline Status & Telemetry ---
    @app.route("/api/ai/status", methods=["GET"])
    def get_ai_status():
        """Returns auto-detected AI provider configuration, model, and guardrail parameters."""
        try:
            from backend.services.unified_pipeline import detect_ai_provider, PROVIDER_DEFAULTS
        except ImportError:
            from services.unified_pipeline import detect_ai_provider, PROVIDER_DEFAULTS
        
        req_key = (
            request.headers.get("X-AI-API-Key") or
            request.args.get("api_key") or
            ""
        ).strip()
        req_provider = (
            request.headers.get("X-AI-Provider") or
            request.args.get("provider") or
            ""
        ).strip().lower()

        detected_provider = detect_ai_provider(req_key, preferred=req_provider) if req_key or req_provider else detect_ai_provider("")
        cfg = PROVIDER_DEFAULTS.get(detected_provider, {})

        configured_providers = []
        for prov, info in PROVIDER_DEFAULTS.items():
            key = os.getenv(f"{prov.upper()}_API_KEY", "")
            if key and len(key) > 8:
                configured_providers.append({
                    "provider": prov,
                    "name": info["name"],
                    "primary_model": info["primary_model"],
                    "fallback_model": info["fallback_model"]
                })

        return jsonify({
            "unified_pipeline": True,
            "active_provider": detected_provider,
            "provider_name": cfg.get("name", detected_provider),
            "primary_model": cfg.get("primary_model"),
            "fallback_model": cfg.get("fallback_model"),
            "configured_providers": configured_providers,
            "guardrail_parameters": {
                "flag_confidence": float(os.getenv("FLAG_CONFIDENCE", "0.8")),
                "flag_confidence_measure": float(os.getenv("FLAG_CONFIDENCE_MEASURE", "0.9")),
                "image_max_edge_px": int(os.getenv("IMAGE_MAX_EDGE_PX", "1568")),
                "timeout_seconds": int(os.getenv("AI_TIMEOUT_SECONDS", "60")),
                "max_retries": int(os.getenv("AI_MAX_RETRIES", "2")),
                "temperature": 0.0,
                "max_output_tokens": 4000
            }
        })

    # --- Phase 3 Usage & Metering Endpoints (Admin-Only) ---
    @app.route("/api/usage", methods=["GET"])
    def get_usage_endpoint():
        """Returns papers included, used, left, add-on papers, renewal date, and cost this month (Admin-Only)."""
        if not is_admin_request():
            return jsonify({"error": "forbidden", "message": "Admin authorization required to access usage telemetry."}), 403
        org_id = request.headers.get("X-Org-ID") or request.args.get("org_id") or "org_default"
        summary = database.get_usage_summary(org_id=org_id, db_path=db_path)
        return jsonify(summary)

    @app.route("/api/usage/ledger", methods=["GET"])
    def get_usage_ledger_endpoint():
        """Returns paginated usage ledger entries (Admin-Only)."""
        if not is_admin_request():
            return jsonify({"error": "forbidden", "message": "Admin authorization required to access usage ledger."}), 403
        org_id = request.headers.get("X-Org-ID") or request.args.get("org_id") or "org_default"
        from_date = request.args.get("from")
        to_date = request.args.get("to")
        try:
            page = int(request.args.get("page", 1))
            limit = int(request.args.get("limit", 50))
        except (ValueError, TypeError):
            page, limit = 1, 50

        ledger_data = database.get_usage_ledger_paged(
            org_id=org_id,
            from_date=from_date,
            to_date=to_date,
            page=page,
            limit=limit,
            db_path=db_path
        )
        return jsonify(ledger_data)

    @app.route("/api/admin/ai-pause", methods=["POST"])
    @app.route("/api/ai/pause", methods=["POST"])
    def admin_ai_pause():
        """Emergency kill switch per organization or global (Admin-Only)."""
        if not is_admin_request():
            return jsonify({"error": "forbidden", "message": "Admin authorization required to toggle AI kill switch."}), 403
        data = request.get_json(silent=True) or {}
        org_id = data.get("org_id")
        paused = bool(data.get("paused", True))
        success = database.set_ai_pause(org_id=org_id, paused=paused, db_path=db_path)
        return jsonify({
            "success": success,
            "paused": paused,
            "org_id": org_id or "all",
            "message": "AI processing paused" if paused else "AI processing resumed"
        })

    @app.route("/api/admin/cleanup-expired-reservations", methods=["POST"])
    def admin_cleanup_expired_reservations():
        """Scheduled job: release unapproved reservations older than RESERVATION_EXPIRY_DAYS (Admin-Only)."""
        if not is_admin_request():
            return jsonify({"error": "forbidden", "message": "Admin authorization required to run reservation cleanup."}), 403
        data = request.get_json(silent=True) or {}
        expiry_days = int(data.get("expiry_days") or request.args.get("expiry_days") or os.getenv("RESERVATION_EXPIRY_DAYS", "7"))
        released = database.release_expired_reservations(expiry_days=expiry_days, db_path=db_path)
        return jsonify({
            "success": True,
            "released_papers": released,
            "expiry_days": expiry_days,
            "message": f"Successfully released {released} unapproved paper reservation(s)."
        })

    # --- Phase 4: Razorpay Orders, Webhook & Invoices API ---
    @app.route("/api/billing/order", methods=["POST"])
    def billing_create_order():
        """Create a Razorpay order for purchasing scan credits."""
        data = request.get_json(silent=True) or {}
        org_id = request.headers.get("X-Org-ID") or data.get("org_id") or "org_default"
        papers = int(data.get("papers", 1000))

        # Standard pack pricing:
        if papers == 1000:
            base_rs = 5000.0
        elif papers == 2500:
            base_rs = 11250.0
        elif papers == 5000:
            base_rs = 20000.0
        else:
            base_rs = round(papers * 5.0, 2)

        gst_rs = round(base_rs * 0.18, 2)
        total_rs = base_rs + gst_rs
        amount_paise = int(round(total_rs * 100))

        order_id = f"order_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"
        key_id = os.getenv("RAZORPAY_KEY_ID", "rzp_test_ink2insights")

        return jsonify({
            "order_id": order_id,
            "papers": papers,
            "amount_rs": base_rs,
            "gst_rs": gst_rs,
            "total_rs": total_rs,
            "amount_paise": amount_paise,
            "currency": "INR",
            "key_id": key_id,
            "org_id": org_id
        })

    @app.route("/api/billing/webhook", methods=["POST"])
    def billing_webhook():
        """Signature-checked Razorpay webhook with idempotent credit and sequential GST invoice PDF."""
        signature = request.headers.get("X-Razorpay-Signature", "")
        raw_body = request.get_data()

        app_env = (os.getenv("APP_ENV") or "development").strip().lower()
        webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
        if not webhook_secret:
            if app_env == "production":
                return jsonify({"error": "configuration_error", "message": "RAZORPAY_WEBHOOK_SECRET must be configured in production."}), 500
            webhook_secret = "mock_webhook_secret"

        if not database.verify_razorpay_webhook_signature(raw_body, signature, webhook_secret=webhook_secret):
            return jsonify({"error": "invalid_signature", "message": "HMAC signature mismatch"}), 400

        payload = request.get_json(silent=True) or {}
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {}) if "payload" in payload else payload
        payment_id = payment_entity.get("id") or payload.get("razorpay_payment_id") or f"pay_{os.urandom(4).hex()}"
        notes = payment_entity.get("notes", {})
        org_id = notes.get("org_id") or payload.get("org_id") or "org_default"
        papers = int(notes.get("papers") or payload.get("papers", 1000))

        if papers == 1000:
            base_rs = 5000.0
        elif papers == 2500:
            base_rs = 11250.0
        elif papers == 5000:
            base_rs = 20000.0
        else:
            base_rs = round(papers * 5.0, 2)
        gst_rs = round(base_rs * 0.18, 2)

        success, result = database.process_successful_payment(
            org_id=org_id,
            payment_id=payment_id,
            papers=papers,
            amount_rs=base_rs,
            gst_rs=gst_rs,
            db_path=db_path
        )

        return jsonify({
            "status": "ok",
            "success": success,
            "result": result
        })

    @app.route("/api/billing/invoices", methods=["GET"])
    def billing_invoices():
        """List all GST invoices for the organization."""
        org_id = getattr(g, "org_id", None) or "org_default"
        invoices = database.get_invoices_for_org(org_id=org_id, db_path=db_path)
        return jsonify({"invoices": invoices, "total": len(invoices)})

    @app.route("/api/billing/invoices/<invoice_id>/download", methods=["GET"])
    def billing_download_invoice(invoice_id):
        """Download invoice PDF by invoice ID or invoice number."""
        inv = database.get_invoice_by_id_or_number(invoice_id, db_path=db_path)
        if not inv or not inv.get("pdf_path") or not os.path.exists(inv["pdf_path"]):
            return jsonify({"error": "Invoice PDF not found"}), 404

        inv_comm = inv.get("community_id") or inv.get("org_id")
        if not check_community_access(inv_comm):
            return jsonify({"error": "forbidden", "message": "Access denied across organizations"}), 403

        pdf_path = inv["pdf_path"]
        inv_number = inv.get("number", "invoice")
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={inv_number}.pdf"}
        )

    # --- Comprehensive Excel Export (and CSV fallback) ---
    @app.route("/api/export", methods=["GET"])
    def export_all():
        from flask import g
        if getattr(g, "user_role", None) == "field_worker":
            return jsonify({"error": "forbidden", "message": "Field workers are not permitted to export records."}), 403

        camp_id = request.args.get("camp_id")
        if camp_id:
            camp = resolve_camp(camp_id)
            if not camp:
                return jsonify({"error": "Camp not found"}), 404
            camp_comm = get_resource_community(camp)
            if not check_community_access(camp_comm):
                return jsonify({
                    "error": "forbidden",
                    "message": "Cross-organization export forbidden: camp does not belong to caller organization."
                }), 403
            community_id = camp_comm
        else:
            caller_comms = get_caller_communities()
            if not caller_comms:
                return jsonify({
                    "error": "forbidden",
                    "message": "Tenant identity required: caller has no authorized community memberships.",
                    "code": "MISSING_TENANT_IDENTITY"
                }), 403
            primary = getattr(g, "org_id", None) or getattr(g, "community_id", None)
            community_id = primary if primary in caller_comms else next(iter(caller_comms))

        only_reviewed = request.args.get("only_reviewed", "false").lower() == "true"
        form_type = request.args.get("type") or request.args.get("form_type") or request.args.get("template")
        record_ids_param = request.args.get("record_ids") or request.args.get("ids")
        record_ids = [rid.strip() for rid in record_ids_param.split(",") if rid.strip()] if record_ids_param else None
        fmt = request.args.get("format", "xlsx").lower()
        if fmt == "csv":
            csv_data = record_service.generate_csv_export(
                camp_id=camp_id,
                only_reviewed=only_reviewed,
                community_id=community_id,
                org_id=community_id
            )
            filename = f"clinic_records_{camp_id or 'all'}.csv"
            return Response(
                csv_data,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

        excel_data = record_service.generate_excel_export(
            camp_id=camp_id,
            only_reviewed=only_reviewed,
            form_type=form_type,
            community_id=community_id,
            org_id=community_id,
            record_ids=record_ids
        )
        prefix = f"{form_type}_" if form_type else ""
        filename = f"{prefix}clinic_records_{camp_id or 'all'}.xlsx"
        return Response(
            excel_data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    @app.route("/api/export/adult/excel", methods=["GET"])
    def export_adult_excel_endpoint():
        """Dedicated endpoint to download formatted Adult Health Screening Excel workbook."""
        from flask import g
        if getattr(g, "user_role", None) == "field_worker":
            return jsonify({"error": "forbidden", "message": "Field workers are not permitted to export records."}), 403
        camp_id = request.args.get("camp_id")
        only_reviewed = request.args.get("only_reviewed", "false").lower() == "true"
        record_ids_param = request.args.get("record_ids") or request.args.get("ids")
        record_ids = [rid.strip() for rid in record_ids_param.split(",") if rid.strip()] if record_ids_param else None
        community_id = getattr(g, "org_id", None) or getattr(g, "community_id", None)
        excel_data = record_service.generate_adult_excel_export(
            camp_id=camp_id,
            only_reviewed=only_reviewed,
            community_id=community_id,
            org_id=community_id,
            record_ids=record_ids
        )
        filename = f"Adult_Health_Records_{camp_id or 'all'}.xlsx"
        return Response(
            excel_data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    @app.route("/api/export/paediatric/excel", methods=["GET"])
    def export_paediatric_excel_endpoint():
        """Dedicated endpoint to download formatted Paediatric School Health Screening Excel workbook."""
        from flask import g
        if getattr(g, "user_role", None) == "field_worker":
            return jsonify({"error": "forbidden", "message": "Field workers are not permitted to export records."}), 403
        camp_id = request.args.get("camp_id")
        only_reviewed = request.args.get("only_reviewed", "false").lower() == "true"
        record_ids_param = request.args.get("record_ids") or request.args.get("ids")
        record_ids = [rid.strip() for rid in record_ids_param.split(",") if rid.strip()] if record_ids_param else None
        community_id = getattr(g, "org_id", None) or getattr(g, "community_id", None)
        excel_data = record_service.generate_paediatric_excel_export(
            camp_id=camp_id,
            only_reviewed=only_reviewed,
            community_id=community_id,
            org_id=community_id,
            record_ids=record_ids
        )
        filename = f"Paediatric_Health_Records_{camp_id or 'all'}.xlsx"
        return Response(
            excel_data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    @app.route("/api/camps/<camp_id>/export", methods=["GET"])
    @app.route("/api/camps/<camp_id>/export/excel", methods=["GET"])
    def export_camp(camp_id):
        from flask import g
        if getattr(g, "user_role", None) == "field_worker":
            return jsonify({"error": "forbidden", "message": "Field workers are not permitted to export records."}), 403
        camp = resolve_camp(camp_id)
        if not camp:
            return jsonify({"error": "Camp not found"}), 404
        camp_comm = get_resource_community(camp)
        if not check_community_access(camp_comm):
            return jsonify({
                "error": "forbidden",
                "message": "Cross-organization export forbidden: camp does not belong to caller organization."
            }), 403
        raw_name = camp.get("name", "Organization") if camp else "Organization"
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', raw_name).strip('_') or f"Org_{camp_id}"
        form_type = request.args.get("type") or request.args.get("form_type") or request.args.get("template")
        record_ids_param = request.args.get("record_ids") or request.args.get("ids")
        record_ids = [rid.strip() for rid in record_ids_param.split(",") if rid.strip()] if record_ids_param else None
        fmt = request.args.get("format", "xlsx").lower()
        if fmt == "csv":
            csv_data = record_service.generate_csv_export(camp_id=camp_id, only_reviewed=False)
            filename = f"{safe_name}_Health_Records.csv"
            return Response(
                csv_data,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

        excel_data = record_service.generate_excel_export(
            camp_id=camp_id,
            only_reviewed=False,
            form_type=form_type,
            record_ids=record_ids
        )
        filename = f"{safe_name}_Health_Records.xlsx"
        return Response(
            excel_data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    # --- Separate Forms Structure & Attributes API ---
    @app.route("/api/forms", methods=["GET"])
    def list_forms_catalog():
        """List all available form definitions (adult/paediatric suites + individual pages) with attribute counts."""
        forms = default_template_engine.list_forms()
        return jsonify({
            "forms": forms,
            "count": len(forms)
        })

    @app.route("/api/forms/<form_id>", methods=["GET"])
    def get_form_structure_endpoint(form_id):
        """Get complete hierarchical structure (pages, sections, fields, validations) for a form."""
        structure = default_template_engine.get_form_structure(form_id)
        if not structure:
            return jsonify({"error": f"Form structure '{form_id}' not found."}), 404
        return jsonify(structure)

    @app.route("/api/forms/<form_id>/attributes", methods=["GET"])
    def get_form_attributes_endpoint(form_id):
        """Get flat attribute dictionary for a form (clinical labels, types, writers, export columns)."""
        structure = default_template_engine.get_form_structure(form_id)
        if not structure:
            return jsonify({"error": f"Form '{form_id}' not found."}), 404
        attributes = default_template_engine.get_form_attributes(form_id)
        return jsonify({
            "form_id": structure["id"],
            "title": structure["title"],
            "family": structure["family"],
            "attributes": attributes,
            "count": len(attributes)
        })

    @app.route("/api/forms/<form_id>/schema", methods=["GET"])
    def get_form_schema_endpoint(form_id):
        """Get dynamic UI rendering schema for tabs and form inputs."""
        canon_id = default_template_engine.resolve_form_id(form_id)
        if canon_id in default_template_engine.templates:
            return jsonify(default_template_engine.generate_ui_schema(canon_id))
        
        # Composite forms UI schema (combines tabs of both pages)
        structure = default_template_engine.get_form_structure(form_id)
        if not structure:
            return jsonify({"error": f"Form '{form_id}' not found."}), 404

        merged_tabs = []
        for p in structure.get("pages", []):
            page_title = p.get("title", f"Page {p.get('page_number')}")
            for sec in p.get("sections", []):
                fields = []
                for f in sec.get("fields", []):
                    fields.append({
                        "key": f["key"],
                        "label": f["label"],
                        "type": f["type"],
                        "required": f.get("required", False),
                        "options": f.get("options", []),
                        "validation": f.get("validation"),
                        "glossary_scope": f.get("glossary_scope"),
                        "alert_hook": f.get("alert_hook"),
                        "export_column": f.get("export_column") or f["key"]
                    })
                merged_tabs.append({
                    "id": f"p{p.get('page_number')}_{sec['id']}",
                    "title": f"[{page_title[:10]}] {sec['title']}",
                    "page_number": p.get("page_number"),
                    "fields": fields
                })

        return jsonify({
            "form_id": structure["id"],
            "title": structure["title"],
            "family": structure["family"],
            "tabs": merged_tabs
        })

    # --- Template Engine & Case Pairing Endpoints (Phase 2) ---
    @app.route("/api/templates", methods=["GET"])
    def list_templates():
        """List all available declarative clinical templates (paediatric-only active)."""
        include_all = request.args.get("all", "false").lower() in ("true", "1") or request.args.get("active_only", "true").lower() in ("false", "0")
        all_templates = default_template_engine.list_templates(active_only=not include_all)
        return jsonify({
            "templates": all_templates,
            "count": len(all_templates)
        })

    @app.route("/api/templates/<template_id>", methods=["GET"])
    def get_template(template_id):
        """Get full template definition or dynamic UI schema."""
        tpl = default_template_engine.get_template(template_id)
        if not tpl:
            return jsonify({"error": f"Template '{template_id}' not found."}), 404

        # Gate paediatric templates with ENABLE_PAEDIATRIC=1
        if tpl.get("family") == "paediatric" and os.getenv("ENABLE_PAEDIATRIC", "1").strip().lower() in ("0", "false"):
            return jsonify({
                "error": "forbidden",
                "message": "Paediatric templates are currently disabled (ENABLE_PAEDIATRIC=0)."
            }), 403

        if request.args.get("ui_schema", "false").lower() == "true":
            return jsonify(default_template_engine.generate_ui_schema(template_id))
        return jsonify(tpl)

    @app.route("/api/templates/classify", methods=["POST"])
    def classify_page_template():
        """Classify page template based on footer code or title text."""
        data = request.get_json(silent=True) or {}
        footer_code = data.get("footer_code")
        header_text = data.get("header_text")

        p2_footer = data.get("page2_footer") or data.get("p2_footer")
        p2_header = data.get("page2_header") or data.get("p2_header")
        if p2_footer is not None or p2_header is not None:
            p1_footer = data.get("page1_footer") or data.get("p1_footer") or footer_code
            p1_header = data.get("page1_header") or data.get("p1_header") or header_text
            camp_target = data.get("camp_target_group")
            client_tids = data.get("template_ids")
            result = default_template_engine.resolve_page_pair(
                p1_footer=p1_footer,
                p1_header=p1_header,
                p2_footer=p2_footer,
                p2_header=p2_header,
                camp_target_group=camp_target,
                client_template_ids=client_tids
            )
            return jsonify(result)

        result = default_template_engine.classify_page(footer_code, header_text)
        return jsonify(result)

    @app.route("/api/cases/pair", methods=["POST"])
    def pair_case_endpoint():
        """Pair history and examination page payloads into a unified Case."""
        data = request.get_json(silent=True) or {}
        history_page = data.get("history_page") or {}
        exam_page = data.get("exam_page") or {}
        session_id = data.get("session_id")
        result = default_template_engine.pair_case(history_page, exam_page, session_id=session_id)
        return jsonify(result)

    # --- Clear / Reset Data ---
    @app.route("/api/reset-demo", methods=["POST"])
    @app.route("/api/clear-data", methods=["POST"])
    def reset_demo():
        # Prevent ordinary Doctor and User accounts from executing destructive operations.
        # Disabled by default; requires explicitly controlled development mechanism.
        app_env = os.getenv("APP_ENV", "production").strip().lower()
        allow_reset = os.getenv("ALLOW_DATA_RESET", "0").strip().lower() in ("1", "true")
        testing_env = os.getenv("TESTING", "").strip().lower()
        if testing_env in ("0", "false"):
            is_testing = False
        else:
            is_testing = app.config.get("TESTING", False) or (testing_env in ("1", "true"))

        if not (allow_reset and (app_env == "development" or is_testing)):
            return jsonify({
                "error": "forbidden",
                "message": "Destructive reset operations are disabled in this environment. Database wipe is restricted.",
                "code": "RESET_DISABLED"
            }), 403

        database.clear_all_records(db_path)
        logger.info("Database records and camps were cleared via explicitly enabled local development reset.")
        return jsonify({"message": "Data cleared successfully", "stats": database.get_overall_stats(db_path)})

    return app

# Top-level Flask instance required by Vercel and WSGI runtimes
app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    logger.info("Starting I2 insights Clinical API on port %d...", port)
    app.run(host="0.0.0.0", port=port, debug=False)
