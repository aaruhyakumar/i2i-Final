"""Unified AI Backend Pipeline for Clinical Intake Document OCR.

A single unified pipeline for all vision AI providers:
- Auto-detects provider from API key format (NVIDIA NIM, Groq, Gemini, OpenAI, Anthropic, OpenRouter)
- Allows dynamic API key, model, and provider injection via request, headers, or environment
- Pre-flight image optimization (orientation correction, max-edge resizing, compression, stitching)
- Comprehensive clinical parameter checking (8 clinical sections, 95+ fields, [value, confidence])
- Physiological plausibility bounds (BP systolic/diastolic, pulse, weight, height, RBS, auto-BMI)
- Medical abbreviation expansion & ambiguity detection
- Automated multi-model escalation and cross-provider failover
- Quota ledger reservation, cost calculation, and 24h cryptographic deduplication

Stage 2B image-quality improvements (2026-11-17):
- Conservative YCbCr luminance contrast stretch (1st–99th percentile) preserves RGB colour.
- Unnecessary JPEG recompression is skipped when no resize or enhancement is applied.
- Configurable max_edge (default 1568 px) and quality (default 82) via env or call-site.
- MIME type declaration always matches the encoded format sent to Claude.
"""

import os
import io
import re
import time
import json
import base64
import logging
from typing import Dict, Any, Optional, Tuple, List, Union
from PIL import Image, ImageOps
import numpy as np
import requests

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

from .ai_service import (
    AIService,
    APPENDIX_C_PROMPT,
    SYSTEM_EXTRACTION_PROMPT,
    validate_and_sanitize_extraction,
    apply_server_side_flags,
    should_escalate_record,
    FLAG_CONFIDENCE,
    FLAG_CONFIDENCE_MEASURE,
    CRITICAL_MEASUREMENT_KEYS,
    MANDATORY_FIELD_KEYS
)

logger = logging.getLogger("unified_ai_pipeline")

# Default Pricing in USD per 1M tokens (Anthropic Claude Vision)
PROVIDER_DEFAULTS = {
    "anthropic": {
        "name": "Anthropic Claude",
        "url": "https://api.anthropic.com/v1/messages",
        "primary_model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        "fallback_model": os.getenv("ANTHROPIC_FALLBACK_MODEL", "claude-sonnet-5-5"),
        "price_in": 3.00,
        "price_out": 15.00,
        "fallback_price_in": 3.00,
        "fallback_price_out": 15.00,
        "needs_stitch": False
    },
    "claude": {
        "name": "Anthropic Claude",
        "url": "https://api.anthropic.com/v1/messages",
        "primary_model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        "fallback_model": os.getenv("ANTHROPIC_FALLBACK_MODEL", "claude-sonnet-5-5"),
        "price_in": 3.00,
        "price_out": 15.00,
        "fallback_price_in": 3.00,
        "fallback_price_out": 15.00,
        "needs_stitch": False
    }
}


def get_configured_ai_provider() -> str:
    """Return the currently configured AI provider, strictly defaulting to 'claude'."""
    return "claude"


def detect_ai_provider(
    api_key: Optional[str] = None,
    fallback: Optional[str] = None,
    preferred: Optional[str] = None
) -> str:
    """Auto-detect AI provider: always resolves to claude in Claude-only architecture."""
    return "claude"


def resolve_api_key_for_provider(provider: str = "claude", explicit_key: Optional[str] = None) -> str:
    """Resolve Anthropic API key, checking explicit key then environment."""
    if explicit_key and len(explicit_key.strip()) > 8:
        return explicit_key.strip()
    return os.getenv("ANTHROPIC_API_KEY", "").strip()
    if prov == "openai":
        return os.getenv("OPENAI_API_KEY", "").strip()
    if prov == "openrouter":
        return os.getenv("OPENROUTER_API_KEY", "").strip()
    if prov in ("anthropic", "claude"):
        return os.getenv("ANTHROPIC_API_KEY", "").strip()
    
    return ""


# ---------------------------------------------------------------------------
# Image Preprocessing & Normalization (Stage 2B)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Image quality configuration (env-overridable, safe production defaults)
# ---------------------------------------------------------------------------
# IMAGE_MAX_EDGE_PX   – longest edge cap (px) sent to Claude Vision.
#                       Default 1568 (original production value).
#                       Set to 2048 to enable the high-resolution path once the
#                       Stage 2 benchmark confirms benefit.
#                       Must be an integer in [512, 4096]; invalid values fall
#                       back to 1568.
# IMAGE_JPEG_QUALITY  – JPEG re-encode quality (0-95).
#                       Default 82 (original production value).
#                       Set to 92 to accompany the 2048-px path.
#                       Must be an integer in [50, 95]; invalid values fall
#                       back to 82.
# IMAGE_CONTRAST_ENHANCE – set to "1" to enable YCbCr luminance stretch.
#                       Default "0" (disabled) until the Stage 2 benchmark
#                       demonstrates that enhancement improves extraction
#                       accuracy.  Set to "1" to activate.
def _safe_int_env(var: str, default: int, lo: int, hi: int) -> int:
    """Read an integer env-var and clamp to [lo, hi]; fall back to default on error."""
    raw = os.getenv(var, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        if lo <= v <= hi:
            return v
        logger.warning("Env %s=%r is outside [%d, %d]; using default %d", var, raw, lo, hi, default)
        return default
    except ValueError:
        logger.warning("Env %s=%r is not a valid integer; using default %d", var, raw, default)
        return default

_ENV_MAX_EDGE    = _safe_int_env("IMAGE_MAX_EDGE_PX",  1568, 512, 4096)
_ENV_QUALITY     = _safe_int_env("IMAGE_JPEG_QUALITY", 82,   50,  95)
# Contrast enhancement is DISABLED by default until the Stage 2 benchmark
# confirms benefit.  Set IMAGE_CONTRAST_ENHANCE=1 to enable.
_ENV_CONTRAST_EN = os.getenv("IMAGE_CONTRAST_ENHANCE", "0").strip() in ("1", "true", "yes")

# Claude Vision accepts: image/jpeg, image/png, image/webp
_CLAUDE_MIME_WHITELIST = {"image/jpeg", "image/png", "image/webp"}
_PIL_FORMAT_TO_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class ImagePreprocessingError(ValueError):
    """Raised when clinical image preprocessing fails due to malformed, unreadable, or unsupported data."""
    pass


def sniff_image_mime(img_bytes: bytes) -> Optional[str]:
    """Identify image format strictly from magic bytes; returns standard MIME string or None if unsupported."""
    if not img_bytes or len(img_bytes) < 4:
        return None
    # JPEG starts with \xFF\xD8\xFF
    if img_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    # PNG starts with \x89PNG\r\n\x1a\n
    if img_bytes[:8] == b"\x89PNG\r\n\x1a\n" or img_bytes[:4] == b"\x89PNG":
        return "image/png"
    # WebP starts with RIFF....WEBP
    if len(img_bytes) >= 12 and img_bytes[:4] == b"RIFF" and img_bytes[8:12] == b"WEBP":
        return "image/webp"
    return None


def _apply_contrast_enhancement(im: "Image.Image") -> "Image.Image":
    """Conservative luminance-only contrast stretch using YCbCr colour space.

    Stretches the Y (luminance) channel from the 1st to the 99th percentile to
    white-balance underexposed mobile scans while keeping chrominance (Cb/Cr)
    channels – and therefore pen-ink colour – completely intact.

    Safety guardrails:
    - Only applied when the luminance range is wide enough (> 20 levels) to
      avoid amplifying noise in already-high-contrast images.
    - Maximum gain is capped at 6× to prevent border shadows turning into
      apparent handwriting.
    - Falls back silently to the original image on any error.
    - Preserves RGB mode on the returned image.

    This function is controlled by the IMAGE_CONTRAST_ENHANCE environment
    variable (set to "0" to disable).
    """
    try:
        import numpy as np
        ycbcr = im.convert("YCbCr")
        y_ch, cb_ch, cr_ch = ycbcr.split()
        y_arr = np.array(y_ch, dtype=np.float32)

        p_low  = float(np.percentile(y_arr, 1.0))
        p_high = float(np.percentile(y_arr, 99.0))
        span   = p_high - p_low

        # Skip stretch when image already has good dynamic range or is too flat
        if span < 20.0:
            return im

        # Cap gain at 6× to guard against noise amplification
        scale = min(255.0 / span, 6.0)
        y_stretched = np.clip((y_arr - p_low) * scale, 0, 255).astype(np.uint8)
        y_new = Image.fromarray(y_stretched, mode="L")
        return Image.merge("YCbCr", (y_new, cb_ch, cr_ch)).convert("RGB")
    except Exception as ce:
        logger.debug("Contrast enhancement skipped (error): %s", ce)
        return im


def preprocess_clinical_image(
    img_bytes: bytes,
    max_edge: int = _ENV_MAX_EDGE,
    quality: int = _ENV_QUALITY,
    enhance_contrast: Optional[bool] = None,
) -> Tuple[bytes, str]:
    """Preprocess a clinical form image for Claude Vision.

    Stage 2B behaviour
    ------------------
    1. EXIF orientation is always corrected.
    2. Unsupported modes (RGBA, P) are converted to RGB.
    3. Images are NOT upscaled – only images whose longest edge exceeds
       ``max_edge`` are downsampled (LANCZOS).
    4. Conservative YCbCr luminance contrast stretch is applied (see
       ``_apply_contrast_enhancement``).  Controlled by the
       IMAGE_CONTRAST_ENHANCE env-var or the ``enhance_contrast`` argument.
    5. Recompression is skipped when no transformation was needed AND the
       source format is already JPEG or PNG – the original encoded bytes are
       returned unchanged to avoid generation loss.
       IMPORTANT: the pass-through is only allowed when EXIF orientation did
       NOT physically rotate the image.  If rotation was required the image is
       always re-encoded so that Claude receives correctly oriented pixels.
    6. When recompression is required the output is always JPEG so that the
       content-block MIME type declaration is unambiguous.

    Returns
    -------
    (processed_bytes, mime_type)
        ``processed_bytes`` – bytes ready for base64 encoding in a Claude
        content block.
        ``mime_type``       – the matching MIME string ("image/jpeg" or
        "image/png") to pass in the ``media_type`` field.

    Errors
    ------
    Any PIL exception falls back to returning the original bytes with a
    best-effort MIME guess, so a failure here never silently drops an image.
    """
    if not img_bytes:
        raise ImagePreprocessingError("Cannot preprocess empty image bytes.")

    # Explicitly reject GIF format
    if img_bytes[:6] in (b"GIF87a", b"GIF89a") or img_bytes[:3] == b"GIF":
        raise ImagePreprocessingError(
            "Unsupported image format: GIF images are not accepted for clinical document extraction. "
            "Please upload a JPEG, PNG, or WebP document scan."
        )

    # Decide whether contrast enhancement is requested
    do_enhance = _ENV_CONTRAST_EN if enhance_contrast is None else bool(enhance_contrast)

    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            source_format = (im.format or "").upper()  # e.g. "JPEG", "PNG", "WEBP"
            if source_format == "GIF":
                raise ImagePreprocessingError(
                    "Unsupported image format: GIF images are not accepted for clinical document extraction. "
                    "Please upload a JPEG, PNG, or WebP document scan."
                )

            # --- Step 1: EXIF orientation (always verified) ---
            # If EXIF inspection fails (e.g. corrupted EXIF block), do not silently
            # assume the image is orientation-safe. Fail safely with a clear error.
            exif_changed = False
            try:
                exif = im.getexif()
                orientation = exif.get(0x0112) if exif else None
                if orientation is not None and orientation not in (0, 1):
                    im = ImageOps.exif_transpose(im)
                    exif_changed = True
            except Exception as exif_err:
                logger.error("EXIF orientation inspection failed: %s", exif_err)
                raise ImagePreprocessingError(
                    f"Unable to reliably establish image orientation: EXIF metadata is corrupted or unreadable ({exif_err})"
                ) from exif_err

            # --- Step 2: Mode normalisation ---
            needs_mode_convert = im.mode not in ("RGB", "L")
            if needs_mode_convert:
                im = im.convert("RGB")
            elif im.mode == "L":
                # Greyscale source: keep as-is; Claude accepts it.
                pass

            w, h = im.size

            # --- Step 3: Resize only when image exceeds the cap ---
            needs_resize = max(w, h) > max_edge
            if needs_resize:
                if w >= h:
                    new_w, new_h = max_edge, max(1, int(h * max_edge / float(w)))
                else:
                    new_h, new_w = max_edge, max(1, int(w * max_edge / float(h)))
                im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # --- Step 4: Conservative contrast enhancement ---
            if do_enhance and im.mode == "RGB":
                im = _apply_contrast_enhancement(im)
                needs_reencode = True
            else:
                needs_reencode = needs_resize or needs_mode_convert or exif_changed

            # --- Step 5: Skip recompression when nothing changed ---
            # Return original bytes ONLY when:
            #   • no transformation occurred (no resize, no mode convert,
            #     no contrast enhance, no EXIF rotation), AND
            #   • the source format is already in Claude's whitelist and matches sniffed magic bytes.
            # If EXIF rotation was needed, we must re-encode the rotated pixels.
            source_mime = _PIL_FORMAT_TO_MIME.get(source_format, "")
            sniffed_mime = sniff_image_mime(img_bytes)
            if not needs_reencode and source_mime in _CLAUDE_MIME_WHITELIST and source_mime == sniffed_mime:
                return img_bytes, source_mime

            # --- Step 6: Re-encode as JPEG ---
            # Ensure RGB before saving as JPEG.
            if im.mode not in ("RGB",):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue(), "image/jpeg"

    except ImagePreprocessingError:
        raise
    except Exception as e:
        logger.error("Clinical image preprocessing failed: %s", e)
        # Check actual file signature: never assume JPEG or fabricate an inaccurate MIME type
        detected_mime = sniff_image_mime(img_bytes)
        if not detected_mime or detected_mime not in _CLAUDE_MIME_WHITELIST:
            sig = img_bytes[:12] if len(img_bytes) >= 12 else img_bytes
            raise ImagePreprocessingError(
                f"Unsupported or malformed image format: cannot establish valid Claude Vision MIME type. "
                f"Byte signature: {sig!r}. Error: {e}"
            ) from e
        # Even if a signature is recognized, if PIL cannot open the payload it is unreadable/corrupted
        raise ImagePreprocessingError(
            f"Image payload could not be parsed as valid {detected_mime}: {e}"
        ) from e


def stitch_pages_side_by_side(p1_bytes: bytes, p2_bytes: bytes, target_h: int = 1200, quality: int = 82) -> bytes:
    """Stitch 2 document pages side-by-side into 1 image for single-image vision endpoints (e.g. NVIDIA NIM)."""
    try:
        im1 = Image.open(io.BytesIO(p1_bytes))
        im2 = Image.open(io.BytesIO(p2_bytes))
        if im1.mode in ("RGBA", "P"): im1 = im1.convert("RGB")
        if im2.mode in ("RGBA", "P"): im2 = im2.convert("RGB")

        w1 = max(1, int(im1.width * (target_h / float(max(1, im1.height)))))
        w2 = max(1, int(im2.width * (target_h / float(max(1, im2.height)))))
        im1_r = im1.resize((w1, target_h), Image.Resampling.LANCZOS)
        im2_r = im2.resize((w2, target_h), Image.Resampling.LANCZOS)

        stitched = Image.new("RGB", (w1 + w2, target_h), (255, 255, 255))
        stitched.paste(im1_r, (0, 0))
        stitched.paste(im2_r, (w1, 0))

        buf = io.BytesIO()
        stitched.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception as e:
        logger.warning("Could not stitch images: %s", e)
        return p1_bytes


# ---------------------------------------------------------------------------
# Single Unified AI Pipeline Class
# ---------------------------------------------------------------------------

class UnifiedAIPipeline(AIService):
    """The Single AI Pipeline for Vision Document Transcription.
    
    Accepts any vision AI API key and manages:
    - Provider detection & payload translation
    - Temperature (0.0 for strict medical accuracy) & token limits
    - Retries with exponential backoff
    - Server-side physiological bounds and abbreviation expansion
    - Automatic escalation to fallback reasoning model
    - Quota metering & cost computation in USD and INR
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        timeout_seconds: int = 60,
        max_retries: int = 2
    ):
        self.explicit_key = (api_key or "").strip()
        self.provider = detect_ai_provider(self.explicit_key, fallback=provider)
        self.api_key = resolve_api_key_for_provider(self.provider, self.explicit_key)
        
        cfg = PROVIDER_DEFAULTS.get(self.provider, PROVIDER_DEFAULTS["claude"])
        self.model = (model or cfg["primary_model"]).strip()
        self.fallback_model = (fallback_model or cfg["fallback_model"]).strip()
        
        self.timeout_seconds = int(os.getenv("AI_TIMEOUT_SECONDS", str(timeout_seconds)))
        self.max_retries = int(os.getenv("AI_MAX_RETRIES", str(max_retries)))
        self.usd_inr = float(os.getenv("USD_INR", "97.0"))

    def is_configured(self) -> bool:
        """Check whether the resolved API key is valid."""
        return bool(self.api_key and len(self.api_key) > 8)

    def provider_name(self) -> str:
        cfg = PROVIDER_DEFAULTS.get(self.provider, {"name": self.provider.title()})
        return f"{cfg['name']} ({self.model})"

    def calculate_cost_rs(
        self,
        tokens_in: int,
        tokens_out: int,
        tokens_think: int = 0,
        is_fallback: bool = False
    ) -> float:
        """Calculate total call cost in INR based on token metrics."""
        cfg = PROVIDER_DEFAULTS.get(self.provider, PROVIDER_DEFAULTS["claude"])
        p_in = cfg["fallback_price_in"] if is_fallback else cfg["price_in"]
        p_out = cfg["fallback_price_out"] if is_fallback else cfg["price_out"]

        effective_out = tokens_out + tokens_think
        cost_usd = ((tokens_in * p_in) + (effective_out * p_out)) / 1_000_000.0
        return round(cost_usd * self.usd_inr, 4)

    def _dispatch_request(
        self,
        model: str,
        prompt: str,
        p1_bytes: bytes,
        p2_bytes: bytes,
        is_fallback: bool = False,
        p1_mime: str = "image/jpeg",
        p2_mime: str = "image/jpeg",
    ) -> Dict[str, Any]:
        """Dispatch HTTP request to the selected vision provider with retries.

        p1_mime / p2_mime must match the actual encoding of p1_bytes / p2_bytes
        and must be a member of _CLAUDE_MIME_WHITELIST.  preprocess_clinical_image()
        guarantees this by returning the MIME alongside the bytes.
        """
        cfg = PROVIDER_DEFAULTS.get(self.provider, PROVIDER_DEFAULTS["claude"])
        t0 = time.time()

        # Prepare request for Anthropic Claude Messages API
        b64_1 = base64.b64encode(p1_bytes).decode("utf-8")
        b64_2 = base64.b64encode(p2_bytes).decode("utf-8")

        endpoint_url = cfg.get("url", "https://api.anthropic.com/v1/messages")
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        content_blocks = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": p1_mime, "data": b64_1}
            },
            {
                "type": "image",
                "source": {"type": "base64", "media_type": p2_mime, "data": b64_2}
            },
            {
                "type": "text",
                "text": prompt
            }
        ]
        payload = {
            "model": model,
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": content_blocks}]
        }

        # Execute HTTP POST with exponential backoff retries
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(endpoint_url, json=payload, headers=headers, timeout=self.timeout_seconds)
                elapsed = time.time() - t0

                if resp.status_code == 200:
                    res_json = resp.json()
                    raw_text, tokens_in, tokens_out, tokens_think = self._parse_provider_response(res_json)
                    cost_rs = self.calculate_cost_rs(tokens_in, tokens_out, tokens_think, is_fallback=is_fallback)

                    return {
                        "success": True,
                        "raw_text": raw_text,
                        "model": model,
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "tokens_think": tokens_think,
                        "seconds": round(elapsed, 2),
                        "cost_rs": cost_rs,
                        "provider": self.provider
                    }

                if resp.status_code == 401:
                    return {
                        "success": False,
                        "error": f"Authentication failed with {cfg['name']} (HTTP 401). Please check your API key.",
                        "code": "AUTH_ERROR",
                        "manual_entry_allowed": True,
                        "status_code": 401
                    }

                if resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning("Attempt %d: %s returned HTTP %d: %s", attempt + 1, cfg["name"], resp.status_code, resp.text[:200])
                    time.sleep(1.5 * (2 ** attempt))
                    continue

                return {
                    "success": False,
                    "error": f"{cfg['name']} API returned HTTP {resp.status_code}: {resp.text[:300]}",
                    "code": "API_ERROR",
                    "status_code": resp.status_code
                }

            except requests.exceptions.Timeout:
                last_err = "Request timed out"
                logger.warning("Attempt %d: Request timed out after %ds", attempt + 1, self.timeout_seconds)
                time.sleep(1.0)
            except Exception as e:
                last_err = str(e)
                logger.warning("Attempt %d request error: %s", attempt + 1, e)
                time.sleep(1.0)

        return {
            "success": False,
            "error": f"Failed after {self.max_retries + 1} attempts: {last_err}",
            "code": "REQUEST_FAILED"
        }

    def _parse_provider_response(self, res_json: Dict[str, Any]) -> Tuple[str, int, int, int]:
        """Extract text content and token usage from Anthropic Claude Messages response."""
        content_list = res_json.get("content", [])
        raw_text = "".join(c.get("text", "") for c in content_list if isinstance(c, dict) and c.get("type") == "text")
        usage = res_json.get("usage", {})
        tokens_in = usage.get("input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        tokens_think = 0
        return raw_text, tokens_in, tokens_out, tokens_think

    def _call_model(
        self,
        model: str,
        prompt: str,
        p1_bytes: bytes,
        p2_bytes: bytes,
        is_fallback: bool = False,
        p1_mime: str = "image/jpeg",
        p2_mime: str = "image/jpeg",
    ) -> Dict[str, Any]:
        """Dispatch a single model vision request."""
        return self._dispatch_request(
            model=model,
            prompt=prompt,
            p1_bytes=p1_bytes,
            p2_bytes=p2_bytes,
            is_fallback=is_fallback,
            p1_mime=p1_mime,
            p2_mime=p2_mime,
        )


    def _should_escalate(self, sanitized_data: Dict[str, Any]) -> Tuple[bool, str]:
        """Determine if a record should be escalated to the fallback model.
        Uses exact rule: count ONLY low-confidence fields (< FLAG_CONFIDENCE),
        never blank fields with confidence 1. Escalate if 3+ low-confidence,
        or any critical measurement is low-confidence or implausible, or invalid schema.
        """
        schema_valid = sanitized_data.get("schema_valid", True)
        fields = sanitized_data.get("fields")
        flag_details = sanitized_data.get("flag_details")

        if not flag_details:
            if fields:
                flag_details = apply_server_side_flags(fields)
            else:
                temp_fields = {}
                for k, v in sanitized_data.items():
                    if k in ("uncertain_fields", "missing_fields", "schema_valid", "flag_details", "flags"):
                        continue
                    temp_fields[k] = [v, 1.0]
                for uf in sanitized_data.get("uncertain_fields", []):
                    temp_fields[uf] = [temp_fields.get(uf, [None, 0.40])[0], 0.40]
                    if uf in ("blood_pressure", "blood_pressure_mmhg"):
                        temp_fields["bp"] = [temp_fields.get("bp", [temp_fields[uf][0], 0.40])[0], 0.40]
                    elif uf in ("pulse_bpm", "pulse"):
                        temp_fields["pulse"] = [temp_fields.get("pulse", [temp_fields[uf][0], 0.40])[0], 0.40]
                    elif uf in ("weight", "weight_kg"):
                        temp_fields["weight_kg"] = [temp_fields.get("weight_kg", [temp_fields[uf][0], 0.40])[0], 0.40]
                    elif uf in ("height", "height_cm"):
                        temp_fields["height_cm"] = [temp_fields.get("height_cm", [temp_fields[uf][0], 0.40])[0], 0.40]
                    elif uf in ("rbs", "random_blood_sugar_mgdl"):
                        temp_fields["rbs"] = [temp_fields.get("rbs", [temp_fields[uf][0], 0.40])[0], 0.40]
                fields = temp_fields
                flag_details = apply_server_side_flags(temp_fields)

        return should_escalate_record(schema_valid, fields or {}, flag_details or {})

    def extract_from_images(
        self,
        student_form_bytes: bytes,
        student_form_mime: str,
        health_assessment_bytes: bytes,
        health_assessment_mime: str,
        # Backward-compatible parameter aliases
        page1_bytes: Optional[bytes] = None,
        page1_mime: Optional[str] = None,
        page2_bytes: Optional[bytes] = None,
        page2_mime: Optional[str] = None,
        prompt: Optional[str] = None,
        target_group: Optional[str] = "adult",
        **kwargs
    ) -> Dict[str, Any]:
        """Execute the end-to-end extraction pipeline with multi-model escalation."""
        sf_bytes = page1_bytes if page1_bytes is not None else student_form_bytes
        ha_bytes = page2_bytes if page2_bytes is not None else health_assessment_bytes

        prompt_text = prompt
        if not prompt_text:
            if target_group and target_group.strip().lower() == "paediatric":
                try:
                    from .ai_service import PAEDIATRIC_EXTRACTION_PROMPT
                    prompt_text = PAEDIATRIC_EXTRACTION_PROMPT
                except ImportError:
                    from ai_service import PAEDIATRIC_EXTRACTION_PROMPT
                    prompt_text = PAEDIATRIC_EXTRACTION_PROMPT
            else:
                prompt_text = APPENDIX_C_PROMPT

        if not self.is_configured():
            cfg = PROVIDER_DEFAULTS.get(self.provider, {"name": self.provider})
            return {
                "success": False,
                "error": f"{cfg['name']} API key is not configured.",
                "code": "AI_NOT_CONFIGURED",
                "manual_entry_allowed": True
            }

        # 1. Preprocess both pages
        # preprocess_clinical_image() returns (bytes, mime_type) so that the
        # Claude content-block declaration always matches the encoded format.
        try:
            p1_opt, p1_mime = preprocess_clinical_image(sf_bytes)
            p2_opt, p2_mime = preprocess_clinical_image(ha_bytes)
        except Exception as prep_err:
            logger.error("Clinical image preprocessing failed: %s", prep_err)
            return {
                "success": False,
                "error": f"Image preprocessing failed: {prep_err}",
                "code": "IMAGE_PREPROCESSING_FAILED",
                "manual_entry_allowed": True
            }

        calls = []

        # 2. Primary Model Call
        primary_res = self._call_model(
            model=self.model,
            prompt=prompt_text,
            p1_bytes=p1_opt,
            p2_bytes=p2_opt,
            is_fallback=False,
            p1_mime=p1_mime,
            p2_mime=p2_mime,
        )

        calls.append(primary_res)

        if not primary_res.get("success"):
            return {
                "success": False,
                "error": primary_res.get("error", "Primary model call failed"),
                "code": primary_res.get("code", "PRIMARY_CALL_FAILED"),
                "manual_entry_allowed": True,
                "calls": calls
            }

        # 3. Parse JSON & Validate Schema
        raw_text = primary_res.get("raw_text", "")
        clean_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.MULTILINE)

        parsed_data = {}
        try:
            parsed_data = json.loads(clean_text)
            if isinstance(parsed_data, list) and len(parsed_data) > 0 and isinstance(parsed_data[0], dict):
                parsed_data = parsed_data[0]
        except Exception as parse_err:
            logger.warning("Primary model output is not valid JSON: %s", parse_err)
            parsed_data = {}

        sanitized = validate_and_sanitize_extraction(parsed_data)

        # 4. Evaluate Server-side Escalation Rules
        should_escalate, esc_reason = self._should_escalate(sanitized)

        # 5. Fallback Model Escalation if required
        if should_escalate and self.fallback_model and self.fallback_model != self.model:
            logger.info("Escalation triggered (%s). Re-running on fallback model: %s", esc_reason, self.fallback_model)
            fb_res = self._call_model(
                model=self.fallback_model,
                prompt=prompt_text,
                p1_bytes=p1_opt,
                p2_bytes=p2_opt,
                is_fallback=True,
                p1_mime=p1_mime,
                p2_mime=p2_mime,
            )
            calls.append(fb_res)

            if fb_res.get("success"):
                fb_text = fb_res.get("raw_text", "")
                fb_clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", fb_text.strip(), flags=re.MULTILINE)
                try:
                    fb_data = json.loads(fb_clean)
                    if isinstance(fb_data, list) and len(fb_data) > 0 and isinstance(fb_data[0], dict):
                        fb_data = fb_data[0]
                    sanitized = validate_and_sanitize_extraction(fb_data)
                    sanitized["escalated"] = True
                    sanitized["escalation_reason"] = esc_reason
                except Exception as fb_parse_err:
                    logger.warning("Fallback model output could not be parsed: %s", fb_parse_err)

        total_tokens_in = sum(c.get("tokens_in", 0) for c in calls if c.get("success"))
        total_tokens_out = sum(c.get("tokens_out", 0) for c in calls if c.get("success"))
        total_tokens_think = sum(c.get("tokens_think", 0) for c in calls if c.get("success"))
        total_seconds = round(sum(c.get("seconds", 0.0) for c in calls if c.get("success")), 2)
        total_cost_rs = round(sum(c.get("cost_rs", 0.0) for c in calls if c.get("success")), 4)

        return {
            "success": True,
            "data": sanitized,
            **sanitized,
            "calls": calls,
            "provider": self.provider_name(),
            "usage": {
                "tokens_in": total_tokens_in,
                "tokens_out": total_tokens_out,
                "tokens_think": total_tokens_think,
                "seconds": total_seconds,
                "cost_rs": total_cost_rs,
                "model": calls[-1].get("model", self.model),
                "provider": self.provider
            }
        }
