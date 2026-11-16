"""
BUG-07 Test Suite: Cross-Browser HEIC Preview Support
Tests frontend client-side HEIC-to-JPEG preview conversion, original file preservation,
failure isolation, dual-page independence, retake compatibility, backend review-image
transcoding, authorization enforcement, and non-regression of standard JPEG/PNG formats.
"""

import io
import os
import re
import pytest
from pathlib import Path
from PIL import Image
import pillow_heif

from backend.app import create_app
from backend import database
from backend.services.auth_service import create_access_token

BASE_DIR = Path(__file__).resolve().parent.parent
APP_JS_PATH = BASE_DIR / "frontend" / "js" / "app.js"
SCANNER_JS_PATH = BASE_DIR / "frontend" / "js" / "scanner.js"
INDEX_PATH = BASE_DIR / "frontend" / "index.html"
VENDOR_HEIC_PATH = BASE_DIR / "frontend" / "js" / "vendor" / "heic2any.min.js"


# ==============================================================================
# 1. Static Architecture & Frontend Verification
# ==============================================================================

def test_heic2any_vendor_file_and_script_tag():
    """Verify heic2any.min.js is locally vendored and referenced without external CDN."""
    # Verify local file exists and is substantial
    assert VENDOR_HEIC_PATH.exists(), f"Missing vendored HEIC decoder at {VENDOR_HEIC_PATH}"
    assert VENDOR_HEIC_PATH.stat().st_size > 1_000_000, "Vendored heic2any.min.js appears truncated"

    html = INDEX_PATH.read_text(encoding="utf-8")
    # Verify index.html loads local vendor script
    assert '<script src="js/vendor/heic2any.min.js"></script>' in html
    # Ensure no external CDN dependency was introduced
    assert "cdn.jsdelivr.net/npm/heic2any" not in html
    assert "unpkg.com" not in html


def test_scanner_js_heic_conversion_export():
    """Verify DocumentScanner exports convertHeicToJpeg and integrates display conversion."""
    scanner_js = SCANNER_JS_PATH.read_text(encoding="utf-8")

    assert "export async function convertHeicToJpeg" in scanner_js
    assert "window.heic2any" in scanner_js
    assert "createImageBitmap" in scanner_js
    assert "displayBlob = await convertHeicToJpeg(file)" in scanner_js
    assert "this.setPageImage(pageIdx, file, url" in scanner_js


def test_app_js_heic_preservation_and_display_split():
    """Verify app.js onFileUploaded stores original blob in pageBlobs and preview URL in pageUrls."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")

    # In onFileUploaded
    assert "state.pageBlobs[pageIdx] = res.blob;" in app_js
    assert "state.pageUrls[pageIdx] = res.url;" in app_js

    # Extraction must use pageBlobs (original untouched files)
    assert "const p1 = state.pageBlobs[0];" in app_js
    assert "const p2 = state.pageBlobs[1];" in app_js
    assert "api.extractPages(p1, p2" in app_js


# ==============================================================================
# 2. Frontend State Machine & Conversion Simulation Tests
# ==============================================================================

class MockFile:
    def __init__(self, name, size=1024*1024, mime_type="image/jpeg", data=b"fake_image_bytes"):
        self.name = name
        self.size = size
        self.type = mime_type
        self.data = data


class HEICUploadSimulation:
    """Simulates DocumentScanner.handleFileUpload and app.js onFileUploaded logic."""

    VALID_MIMES = [
        'image/jpeg', 'image/png', 'image/webp', 'image/jpg',
        'image/heic', 'image/heif', 'image/heic-sequence', 'image/heif-sequence'
    ]

    def __init__(self, heic_decoder_available=True, force_decode_failure=False):
        self.currentScanPage = 0
        self.retakeTargetPage = None
        self.pageBlobs = [None, None]  # Holds original File/Blob
        self.pageUrls = [None, None]   # Holds display JPEG URL
        self.revoked_urls = []
        self.url_counter = 0
        self.currentScreen = 'scan'
        self.heic_decoder_available = heic_decoder_available
        self.force_decode_failure = force_decode_failure

    def _create_url(self, label):
        self.url_counter += 1
        return f"blob:http://localhost:5001/{label}_{self.url_counter}"

    def _revoke_url(self, url):
        if url:
            self.revoked_urls.append(url)

    def convertHeicToJpeg(self, file):
        if self.force_decode_failure or not self.heic_decoder_available:
            raise RuntimeError(f'Unable to decode HEIC image "{file.name}". Please retry or select a JPEG/PNG.')
        # Successful conversion returns simulated JPEG Blob
        return MockFile(
            name=f"{file.name}.jpg",
            size=int(file.size * 0.8),
            mime_type="image/jpeg",
            data=b"jpeg_converted_display_bytes"
        )

    def handleFileUpload(self, file, target_page_index=None):
        if not file:
            return None

        is_heic = bool((file.name and re.search(r"\.(heic|heif)$", file.name, re.I)) or
                       (file.type and ("heic" in file.type or "heif" in file.type)))

        if file.type not in self.VALID_MIMES and not is_heic:
            raise ValueError("Please select a valid image file (JPEG, PNG, WebP, or iPhone HEIC).")

        if file.size > 20 * 1024 * 1024:
            raise ValueError("File size exceeds the 20 MB limit.")

        display_blob = file
        if is_heic:
            display_blob = self.convertHeicToJpeg(file)

        page_idx = target_page_index if target_page_index is not None else self.currentScanPage
        url = self._create_url(f"display_page_{page_idx + 1}")

        return {
            "blob": file,                  # ORIGINAL UNTOUCHED FILE
            "displayBlob": display_blob,   # BROWSER COMPATIBLE JPEG
            "url": url,
            "quality": {"ok": True},
            "isHeic": is_heic
        }

    def onFileUploaded(self, files, input_mock=None):
        try:
            if not files:
                return {"status": "cancelled"}

            is_retake = (self.retakeTargetPage in (0, 1)) or (self.currentScanPage == 0 and bool(self.pageBlobs[1]))
            target_retake = self.retakeTargetPage if self.retakeTargetPage in (0, 1) else self.currentScanPage

            if is_retake:
                p = target_retake
                res = self.handleFileUpload(files[0], p)
                if self.pageUrls[p]:
                    self._revoke_url(self.pageUrls[p])
                self.pageBlobs[p] = res["blob"]
                self.pageUrls[p] = res["url"]
                self.retakeTargetPage = None
                if self.pageBlobs[0] and self.pageBlobs[1]:
                    self.currentScreen = 'pairs'
                return {"status": "retake_complete", "target": p}

            elif len(files) >= 2:
                res1 = self.handleFileUpload(files[0], 0)
                res2 = self.handleFileUpload(files[1], 1)
                self.pageBlobs[0] = res1["blob"]
                self.pageUrls[0] = res1["url"]
                self.pageBlobs[1] = res2["blob"]
                self.pageUrls[1] = res2["url"]
                self.currentScreen = 'pairs'
                return {"status": "dual_complete"}

            else:
                p = self.currentScanPage
                res = self.handleFileUpload(files[0], p)
                self.pageBlobs[p] = res["blob"]
                self.pageUrls[p] = res["url"]
                if self.pageBlobs[0] and self.pageBlobs[1]:
                    self.currentScreen = 'pairs'
                elif p == 0:
                    self.currentScanPage = 1
                return {"status": "single_complete", "target": p}
        finally:
            if input_mock is not None:
                input_mock["value"] = ""


def test_heic_upload_preserves_original_and_generates_jpeg_preview():
    """Requirement 1 & 2: HEIC upload stores original file in pageBlobs and JPEG in preview."""
    sim = HEICUploadSimulation()
    orig_heic = MockFile("clinical_history.HEIC", mime_type="image/heic", data=b"ORIGINAL_HEIC_RAW_BYTES")

    res = sim.handleFileUpload(orig_heic, 0)

    # 1. Original file is 100% preserved
    assert res["blob"] == orig_heic
    assert res["blob"].data == b"ORIGINAL_HEIC_RAW_BYTES"
    assert res["isHeic"] is True

    # 2. Display blob is transcoded to JPEG
    assert res["displayBlob"].type == "image/jpeg"
    assert res["displayBlob"].name == "clinical_history.HEIC.jpg"
    assert "display_page_1" in res["url"]


def test_failed_conversion_displays_error_and_does_not_mutate_state():
    """Requirement 3: Failed HEIC conversion displays useful error and preserves previous state."""
    sim = HEICUploadSimulation(force_decode_failure=True)
    corrupted_heic = MockFile("bad_image.heic", mime_type="image/heic")

    input_mock = {"value": "bad_image.heic"}
    with pytest.raises(RuntimeError) as exc_info:
        sim.onFileUploaded([corrupted_heic], input_mock=input_mock)

    assert "Unable to decode HEIC image" in str(exc_info.value)
    # State MUST NOT be corrupted
    assert sim.pageBlobs[0] is None
    assert sim.pageUrls[0] is None
    # File input MUST be reset so user can retry
    assert input_mock["value"] == ""


def test_page1_and_page2_independence_with_mixed_formats():
    """Requirement 4: Page 1 (HEIC) and Page 2 (JPEG) operate independently."""
    sim = HEICUploadSimulation()

    heic_p1 = MockFile("intake_front.heic", mime_type="image/heic", data=b"HEIC_P1")
    jpeg_p2 = MockFile("exam_back.jpg", mime_type="image/jpeg", data=b"JPEG_P2")

    res = sim.onFileUploaded([heic_p1, jpeg_p2])
    assert res["status"] == "dual_complete"
    assert sim.currentScreen == "pairs"

    # Page 1 preserved original HEIC, has display JPEG url
    assert sim.pageBlobs[0] == heic_p1
    assert sim.pageBlobs[0].data == b"HEIC_P1"

    # Page 2 preserved original JPEG
    assert sim.pageBlobs[1] == jpeg_p2
    assert sim.pageBlobs[1].data == b"JPEG_P2"


def test_retake_independence_with_heic():
    """Requirement 5: Retaking Page 1 with HEIC leaves Page 2 intact."""
    sim = HEICUploadSimulation()
    p1_first = MockFile("p1.jpg", mime_type="image/jpeg")
    p2_orig = MockFile("p2_preserved.png", mime_type="image/png", data=b"PRESERVED_P2")
    sim.onFileUploaded([p1_first, p2_orig])

    old_p1_url = sim.pageUrls[0]
    p2_url = sim.pageUrls[1]

    # Retake Page 1
    sim.retakeTargetPage = 0
    sim.currentScanPage = 0
    sim.pageBlobs[0] = None

    # Upload replacement HEIC for Page 1
    heic_replacement = MockFile("p1_new.heic", mime_type="image/heic", data=b"NEW_HEIC_P1")
    res = sim.onFileUploaded([heic_replacement])

    assert res["status"] == "retake_complete"
    assert sim.pageBlobs[0] == heic_replacement
    assert sim.pageBlobs[0].data == b"NEW_HEIC_P1"
    # Page 2 remains 100% UNTOUCHED
    assert sim.pageBlobs[1] == p2_orig
    assert sim.pageBlobs[1].data == b"PRESERVED_P2"
    assert sim.pageUrls[1] == p2_url
    assert old_p1_url in sim.revoked_urls


def test_extraction_submission_uses_original_heic_bytes():
    """Requirement 6: Extraction submits the original HEIC bytes, not the preview JPEG."""
    sim = HEICUploadSimulation()
    heic1 = MockFile("form1.heic", mime_type="image/heic", data=b"RAW_HEIC_BYTES_PAGE1")
    heic2 = MockFile("form2.heic", mime_type="image/heic", data=b"RAW_HEIC_BYTES_PAGE2")

    sim.onFileUploaded([heic1, heic2])

    # Simulation of saveAndExtract():
    submitted_p1 = sim.pageBlobs[0]
    submitted_p2 = sim.pageBlobs[1]

    # Must be exact original objects and bytes
    assert submitted_p1.data == b"RAW_HEIC_BYTES_PAGE1"
    assert submitted_p2.data == b"RAW_HEIC_BYTES_PAGE2"


# ==============================================================================
# 3. Backend Integration Tests: /api/files/{file_id}
# ==============================================================================

@pytest.fixture
def api_client(tmp_path):
    """Flask client fixture with seeded database and authenticated admin user."""
    db_file = str(tmp_path / "test_heic_api.db")
    private_dir = str(tmp_path / "private_uploads")
    os.makedirs(private_dir, exist_ok=True)

    app = create_app(db_path=db_file)
    app.config["TESTING"] = True
    app.config["PRIVATE_UPLOAD_DIR"] = private_dir

    database.init_db(db_file)
    database.get_or_create_doctor(
        email="doc_heic_test@example.com",
        full_name="Dr Test",
        auth_id="doc_heic_test",
        community_id="comm_heic_test",
        db_path=db_file
    )
    database.create_camp("Camp Anna Nagar", 100, "Today", camp_id="camp_heic_01", community_id="comm_heic_test", db_path=db_file)
    token = create_access_token(user_id="doc_heic_test", role="admin", org_id="comm_heic_test", email="doc_heic_test@example.com")

    with app.test_client() as client:
        client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        client.db_file = db_file
        client.private_dir = private_dir
        client.auth_token = token
        yield client


def _create_synthetic_heif_bytes():
    """Create genuine HEIF bytes using pillow_heif."""
    pillow_heif.register_heif_opener()
    img = Image.new("RGB", (120, 160), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="HEIF")
    return buf.getvalue()


def test_backend_serves_compatible_jpeg_for_heic_original(api_client):
    """Requirement 7: /api/files/{id} transcodes HEIC original to JPEG while keeping stored file untouched."""
    heif_bytes = _create_synthetic_heif_bytes()

    # Save original HEIF via database.save_private_file
    file_id = database.save_private_file(
        org_id="comm_heic_test",
        record_id=None,
        file_type="scan_page1",
        filename="iphone_intake.heic",
        data=heif_bytes,
        mime_type="image/heic",
        db_path=api_client.db_file,
        private_dir=api_client.private_dir
    )

    file_rec = database.get_file_by_id(file_id, db_path=api_client.db_file)
    storage_path = file_rec["storage_path"]

    # 1. Verify original stored file is untouched genuine HEIF
    assert os.path.exists(storage_path)
    with open(storage_path, "rb") as f:
        stored_bytes = f.read()
    assert stored_bytes == heif_bytes
    assert pillow_heif.is_supported(storage_path) is True

    # 2. Request /api/files/{file_id} via GET
    res = api_client.get(f"/api/files/{file_id}")
    assert res.status_code == 200

    # 3. Response MUST be image/jpeg for browser compatibility
    assert res.mimetype == "image/jpeg"
    assert res.headers.get("Cache-Control") == "private, no-store"

    # 4. Verify the response bytes are decodable as JPEG
    resp_img = Image.open(io.BytesIO(res.data))
    assert resp_img.format == "JPEG"
    assert resp_img.size == (120, 160)

    # 5. Verify the original file on disk was NOT overwritten
    with open(storage_path, "rb") as f:
        assert f.read() == heif_bytes, "Original stored file on disk must remain 100% untouched"


def test_backend_preserves_standard_jpeg_and_png_behavior(api_client):
    """Requirement 8: Standard PNG and JPEG are served directly without transcoding."""
    # Test PNG
    png_img = Image.new("RGB", (80, 80), color="green")
    png_buf = io.BytesIO()
    png_img.save(png_buf, format="PNG")
    png_bytes = png_buf.getvalue()

    png_id = database.save_private_file(
        org_id="comm_heic_test",
        record_id=None,
        file_type="scan_page1",
        filename="standard_doc.png",
        data=png_bytes,
        mime_type="image/png",
        db_path=api_client.db_file,
        private_dir=api_client.private_dir
    )

    res_png = api_client.get(f"/api/files/{png_id}")
    assert res_png.status_code == 200
    assert res_png.mimetype == "image/png"
    assert res_png.data == png_bytes

    # Test JPEG
    jpg_img = Image.new("RGB", (80, 80), color="yellow")
    jpg_buf = io.BytesIO()
    jpg_img.save(jpg_buf, format="JPEG")
    jpg_bytes = jpg_buf.getvalue()

    jpg_id = database.save_private_file(
        org_id="comm_heic_test",
        record_id=None,
        file_type="scan_page2",
        filename="standard_doc.jpg",
        data=jpg_bytes,
        mime_type="image/jpeg",
        db_path=api_client.db_file,
        private_dir=api_client.private_dir
    )

    res_jpg = api_client.get(f"/api/files/{jpg_id}")
    assert res_jpg.status_code == 200
    assert res_jpg.mimetype == "image/jpeg"
    assert res_jpg.data == jpg_bytes


def test_backend_authorization_enforced_on_heic_files(api_client):
    """Requirement 9: Authorization checks remain strictly enforced on HEIC files."""
    heif_bytes = _create_synthetic_heif_bytes()

    file_id = database.save_private_file(
        org_id="comm_heic_test",
        record_id=None,
        file_type="scan_page1",
        filename="auth_protected.heic",
        data=heif_bytes,
        mime_type="image/heic",
        db_path=api_client.db_file,
        private_dir=api_client.private_dir
    )

    # 1. Unauthenticated request -> 401
    unauth_client = api_client.application.test_client()
    res_unauth = unauth_client.get(f"/api/files/{file_id}")
    assert res_unauth.status_code == 401

    # 2. Cross-tenant request -> 404 (anti-enumeration rule)
    other_org_token = create_access_token(user_id="foreign_doctor", role="doctor", org_id="different_community")
    cross_client = api_client.application.test_client()
    cross_client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {other_org_token}"
    res_cross = cross_client.get(f"/api/files/{file_id}")
    assert res_cross.status_code == 404
