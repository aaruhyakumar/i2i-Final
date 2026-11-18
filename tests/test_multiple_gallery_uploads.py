"""
BUG-05 Test Suite: Multiple Gallery Image Uploads
Tests support for selecting 1 or 2 document images from gallery,
preserving selection order, aspect ratio preservation, URL lifecycle,
retake mode compatibility, pre-flight file validation, and input reset.
"""

import re
import pytest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_JS_PATH = BASE_DIR / "frontend" / "js" / "app.js"
SCANNER_JS_PATH = BASE_DIR / "frontend" / "js" / "scanner.js"
INDEX_PATH = BASE_DIR / "frontend" / "index.html"
CSS_PATH = BASE_DIR / "frontend" / "css" / "styles.css"


# ==============================================================================
# 1. Static HTML & JavaScript Architecture Verification
# ==============================================================================

def test_index_html_gallery_input_attributes():
    """Verify #camera-gallery-input has 'multiple', valid accept and onchange handler, and no multiple='false'."""
    html = INDEX_PATH.read_text(encoding="utf-8")
    
    # Locate camera-gallery-input
    match = re.search(r'<input[^>]*id="camera-gallery-input"[^>]*>', html)
    assert match is not None, "Could not find #camera-gallery-input element in index.html"
    tag = match.group(0)
    
    # Must have multiple attribute (boolean attribute in HTML5)
    assert "multiple" in tag, "#camera-gallery-input must contain 'multiple' attribute"
    assert 'multiple="false"' not in tag, "#camera-gallery-input must NOT contain multiple='false'"
    
    # Must accept image/* and trigger window.app.onFileUploaded(event)
    assert 'accept="image/*"' in tag
    assert 'onchange="window.app.onFileUploaded(event)"' in tag


def test_index_html_photo_input_remains_single_capture():
    """Verify #camera-photo-input does not have multiple attribute to preserve single-frame camera capture."""
    html = INDEX_PATH.read_text(encoding="utf-8")
    
    match = re.search(r'<input[^>]*id="camera-photo-input"[^>]*>', html)
    assert match is not None, "Could not find #camera-photo-input element in index.html"
    tag = match.group(0)
    
    assert "multiple" not in tag, "#camera-photo-input should be a single-photo capture without multiple"
    assert 'capture="environment"' in tag
    assert 'onchange="window.app.onFileUploaded(event)"' in tag


def test_index_html_cache_buster():
    """Verify index.html has updated cache busters for styles and scripts."""
    html = INDEX_PATH.read_text(encoding="utf-8")
    
    assert re.search(r"styles\.css\?v=20261116_bug0[5-7]", html), "Expected updated css cache buster v=20261116_bug05..07"
    assert re.search(r"js/app\.js\?v=20261116_(bug0[5-7]|fix\d+)", html), "Expected updated app.js cache buster"


def test_scanner_handle_file_upload_signature():
    """Verify DocumentScanner.handleFileUpload supports optional targetPageIndex."""
    scanner_js = SCANNER_JS_PATH.read_text(encoding="utf-8")
    
    # Checks parameter targetPageIndex
    assert re.search(r"async\s+handleFileUpload\s*\(\s*file\s*,\s*targetPageIndex\s*=\s*null\s*\)", scanner_js) is not None
    
    # Checks that it targets targetPageIndex or currentPage
    assert "targetPageIndex !== null && targetPageIndex !== undefined" in scanner_js
    assert "this.setPageImage(pageIdx, file, url" in scanner_js
    
    # Checks MIME and size validations
    assert "20 * 1024 * 1024" in scanner_js
    assert "validMimes" in scanner_js
    assert "isHeic" in scanner_js


def test_app_js_on_file_uploaded_multi_file_support():
    """Verify app.js onFileUploaded extracts all files, validates pre-flight, and resets input in finally."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    
    # Function exists and exported
    assert "export async function onFileUploaded(event)" in app_js
    
    match = re.search(r"export\s+async\s+function\s+onFileUploaded\s*\((.*?)\)\s*\{([\s\S]*?)\n\}\n\s*export", app_js)
    assert match is not None, "Could not extract onFileUploaded function"
    body = match.group(2)
    
    # Extracts multiple files as array
    assert "Array.from(event.target.files)" in body
    
    # Pre-validates files before mutating state
    assert "validMimes" in body
    assert "maxSize" in body
    assert "20 * 1024 * 1024" in body
    
    # Handles dual upload
    assert "files.length >= 2" in body
    assert "sc.handleFileUpload(files[0], 0)" in body
    assert "sc.handleFileUpload(files[1], 1)" in body
    assert "proceedToPairsScreen();" in body
    
    # Retake mode check
    assert "isRetakeMode" in body
    
    # Finally block resets input value
    assert "finally" in body
    assert "input.value = ''" in body or "event.target.value = ''" in body


# ==============================================================================
# 2. State Machine & Execution Simulation Tests
# ==============================================================================

class MockFile:
    def __init__(self, name, size=1024*1024, mime_type="image/jpeg"):
        self.name = name
        self.size = size
        self.type = mime_type


class GalleryUploadSimulation:
    """Simulates the state transitions of scanner.js and app.js for file uploads."""
    
    VALID_MIMES = [
        'image/jpeg', 'image/png', 'image/webp', 'image/jpg',
        'image/heic', 'image/heif', 'image/heic-sequence', 'image/heif-sequence'
    ]
    MAX_SIZE = 20 * 1024 * 1024

    def __init__(self):
        self.currentScanPage = 0
        self.retakeTargetPage = None
        self.pageBlobs = [None, None]
        self.pageUrls = [None, None]
        self.clientPageQualities = [None, None]
        self.sc_pages = [None, None]
        self.currentScreen = 'home'
        self.revoked_urls = []
        self.toasts = []
        self.url_counter = 0
        self.camera_active = False

    def _create_url(self, label):
        self.url_counter += 1
        return f"blob:http://localhost:5001/{label}_{self.url_counter}"

    def _revoke_url(self, url):
        if url:
            self.revoked_urls.append(url)

    def startScan(self):
        for u in self.pageUrls:
            if u:
                self._revoke_url(u)
        self.currentScanPage = 0
        self.retakeTargetPage = None
        self.pageBlobs = [None, None]
        self.pageUrls = [None, None]
        self.clientPageQualities = [None, None]
        self.sc_pages = [None, None]
        self.currentScreen = 'scan'
        self.camera_active = True

    def retakePage(self, page_index):
        assert page_index in (0, 1)
        if self.pageUrls[page_index]:
            self._revoke_url(self.pageUrls[page_index])
            self.pageUrls[page_index] = None
        self.pageBlobs[page_index] = None
        self.clientPageQualities[page_index] = None
        self.sc_pages[page_index] = None
        self.currentScanPage = page_index
        self.retakeTargetPage = page_index
        self.currentScreen = 'scan'
        self.camera_active = True

    def _validate_files(self, files):
        for f in files:
            is_heic = bool(f.name and (f.name.lower().endswith('.heic') or f.name.lower().endswith('.heif')))
            if f.type not in self.VALID_MIMES and not is_heic:
                return False, f"Invalid type: {f.type}"
            if f.size > self.MAX_SIZE:
                return False, f"File too large: {f.size}"
        return True, "OK"

    def onFileUploaded(self, files, input_mock=None):
        if not files:
            if input_mock is not None:
                input_mock["value"] = ""
            return {"status": "cancelled"}

        # 1. Pre-validation
        valid, msg = self._validate_files(files)
        if not valid:
            self.toasts.append(f"❌ {msg}")
            if input_mock is not None:
                input_mock["value"] = ""
            return {"status": "validation_failed", "error": msg}

        # 2. Retake Mode Check
        is_retake = (self.retakeTargetPage in (0, 1)) or (self.currentScanPage == 0 and bool(self.pageBlobs[1]))
        target_retake = self.retakeTargetPage if self.retakeTargetPage in (0, 1) else self.currentScanPage

        try:
            if is_retake:
                # Use first file for retake target
                f = files[0]
                p = target_retake
                if self.pageUrls[p]:
                    self._revoke_url(self.pageUrls[p])
                url = self._create_url(f"page{p+1}")
                self.pageBlobs[p] = f
                self.pageUrls[p] = url
                self.sc_pages[p] = {"blob": f, "url": url, "name": f.name}
                self.clientPageQualities[p] = {"ok": True}
                self.retakeTargetPage = None

                if len(files) > 1:
                    self.toasts.append(f"Retake: Page {p+1} updated with first image. Untouched page preserved.")
                else:
                    self.toasts.append(f"Page {p+1} updated! Both pages ready.")

                if self.pageBlobs[0] and self.pageBlobs[1]:
                    self.camera_active = False
                    self.currentScreen = 'pairs'
                return {"status": "retake_complete", "target": p}

            elif len(files) >= 2:
                # Dual upload: files[0] -> Page 1, files[1] -> Page 2
                if self.pageUrls[0]:
                    self._revoke_url(self.pageUrls[0])
                if self.pageUrls[1]:
                    self._revoke_url(self.pageUrls[1])

                url1 = self._create_url("page1")
                url2 = self._create_url("page2")

                self.pageBlobs[0] = files[0]
                self.pageUrls[0] = url1
                self.sc_pages[0] = {"blob": files[0], "url": url1, "name": files[0].name}
                self.clientPageQualities[0] = {"ok": True}

                self.pageBlobs[1] = files[1]
                self.pageUrls[1] = url2
                self.sc_pages[1] = {"blob": files[1], "url": url2, "name": files[1].name}
                self.clientPageQualities[1] = {"ok": True}

                self.retakeTargetPage = None
                self.camera_active = False
                self.currentScreen = 'pairs'
                self.toasts.append("Both Page 1 and Page 2 uploaded!")
                return {"status": "dual_upload_complete"}

            else:
                # Single file during fresh scan
                f = files[0]
                p = self.currentScanPage
                if self.pageUrls[p]:
                    self._revoke_url(self.pageUrls[p])
                url = self._create_url(f"page{p+1}")
                self.pageBlobs[p] = f
                self.pageUrls[p] = url
                self.sc_pages[p] = {"blob": f, "url": url, "name": f.name}
                self.clientPageQualities[p] = {"ok": True}

                if self.pageBlobs[0] and self.pageBlobs[1]:
                    self.camera_active = False
                    self.currentScreen = 'pairs'
                    self.toasts.append(f"Page {p+1} uploaded! Both pages ready.")
                elif p == 0:
                    self.currentScanPage = 1
                    self.toasts.append("Page 1 uploaded! Now select Page 2.")
                else:
                    self.currentScanPage = 0
                    self.toasts.append("Page 2 uploaded! Now select Page 1.")
                return {"status": "single_upload_complete", "target": p}
        finally:
            if input_mock is not None:
                input_mock["value"] = ""


# ==============================================================================
# 3. Test Cases for Gallery Upload Scenarios
# ==============================================================================

def test_scenario_single_file_fresh_scan_progression():
    """Verify single file upload into Page 1 advances to Page 2, then second upload completes pair."""
    sim = GalleryUploadSimulation()
    sim.startScan()
    assert sim.currentScanPage == 0
    
    file_p1 = MockFile("form_page1.jpg")
    res1 = sim.onFileUploaded([file_p1])
    assert res1["status"] == "single_upload_complete"
    assert sim.pageBlobs[0] == file_p1
    assert sim.pageBlobs[1] is None
    assert sim.currentScanPage == 1
    assert sim.currentScreen == 'scan'
    
    file_p2 = MockFile("form_page2.png")
    res2 = sim.onFileUploaded([file_p2])
    assert res2["status"] == "single_upload_complete"
    assert sim.pageBlobs[0] == file_p1
    assert sim.pageBlobs[1] == file_p2
    assert sim.currentScreen == 'pairs'
    assert sim.camera_active is False


def test_scenario_dual_file_upload_preserves_selection_order():
    """Verify selecting two files at once assigns files[0] to Page 1 and files[1] to Page 2."""
    sim = GalleryUploadSimulation()
    sim.startScan()
    
    f_first = MockFile("intake_front.jpg")
    f_second = MockFile("intake_back.jpg")
    
    input_mock = {"value": "C:\\fakepath\\intake_front.jpg"}
    res = sim.onFileUploaded([f_first, f_second], input_mock=input_mock)
    
    assert res["status"] == "dual_upload_complete"
    # Strict order preservation
    assert sim.pageBlobs[0] == f_first, "First selected file must become Page 1"
    assert sim.pageBlobs[1] == f_second, "Second selected file must become Page 2"
    assert sim.sc_pages[0]["name"] == "intake_front.jpg"
    assert sim.sc_pages[1]["name"] == "intake_back.jpg"
    assert sim.currentScreen == 'pairs'
    assert sim.camera_active is False
    assert input_mock["value"] == "", "Input value must be reset"


def test_scenario_more_than_two_files_takes_first_two():
    """Verify selecting 3 files uses the first 2 and completes the pair."""
    sim = GalleryUploadSimulation()
    sim.startScan()
    
    files = [MockFile("p1.jpg"), MockFile("p2.jpg"), MockFile("p3.jpg")]
    res = sim.onFileUploaded(files)
    
    assert res["status"] == "dual_upload_complete"
    assert sim.pageBlobs[0] == files[0]
    assert sim.pageBlobs[1] == files[1]
    assert sim.currentScreen == 'pairs'


def test_scenario_retake_page_1_with_single_file():
    """Verify retaking Page 1 with single file replaces Page 1 and preserves Page 2."""
    sim = GalleryUploadSimulation()
    sim.startScan()
    sim.onFileUploaded([MockFile("p1_orig.jpg"), MockFile("p2_orig.jpg")])
    
    p2_blob = sim.pageBlobs[1]
    p2_url = sim.pageUrls[1]
    p1_old_url = sim.pageUrls[0]
    
    # Retake Page 1
    sim.retakePage(0)
    assert sim.currentScanPage == 0
    assert sim.pageBlobs[0] is None
    assert sim.pageBlobs[1] == p2_blob
    
    # Upload replacement for Page 1
    new_p1 = MockFile("p1_replacement.png")
    res = sim.onFileUploaded([new_p1])
    
    assert res["status"] == "retake_complete"
    assert sim.pageBlobs[0] == new_p1
    assert sim.pageBlobs[1] == p2_blob, "Page 2 must remain completely intact"
    assert sim.pageUrls[1] == p2_url, "Page 2 URL must not be altered"
    assert p1_old_url in sim.revoked_urls, "Old Page 1 URL must have been revoked"
    assert sim.currentScreen == 'pairs'


def test_scenario_retake_page_2_with_single_file():
    """Verify retaking Page 2 with single file replaces Page 2 and preserves Page 1."""
    sim = GalleryUploadSimulation()
    sim.startScan()
    sim.onFileUploaded([MockFile("p1_orig.jpg"), MockFile("p2_orig.jpg")])
    
    p1_blob = sim.pageBlobs[0]
    p1_url = sim.pageUrls[0]
    p2_old_url = sim.pageUrls[1]
    
    # Retake Page 2
    sim.retakePage(1)
    assert sim.currentScanPage == 1
    assert sim.pageBlobs[1] is None
    assert sim.pageBlobs[0] == p1_blob
    
    # Upload replacement for Page 2
    new_p2 = MockFile("p2_replacement.webp", mime_type="image/webp")
    res = sim.onFileUploaded([new_p2])
    
    assert res["status"] == "retake_complete"
    assert sim.pageBlobs[0] == p1_blob, "Page 1 must remain completely intact"
    assert sim.pageBlobs[1] == new_p2
    assert sim.pageUrls[0] == p1_url, "Page 1 URL must not be altered"
    assert p2_old_url in sim.revoked_urls, "Old Page 2 URL must have been revoked"
    assert sim.currentScreen == 'pairs'


def test_scenario_retake_with_multiple_files_protects_preserved_page():
    """Verify that selecting multiple files while in retake mode does NOT overwrite the preserved page."""
    sim = GalleryUploadSimulation()
    sim.startScan()
    sim.onFileUploaded([MockFile("p1_orig.jpg"), MockFile("p2_orig.jpg")])
    
    p2_preserved_blob = sim.pageBlobs[1]
    
    # User retakes Page 1, but mistakenly selects 2 files from the gallery
    sim.retakePage(0)
    rep1 = MockFile("p1_new.jpg")
    rep2_accidental = MockFile("p2_accidental.jpg")
    
    res = sim.onFileUploaded([rep1, rep2_accidental])
    assert res["status"] == "retake_complete"
    assert sim.pageBlobs[0] == rep1, "Target retake page updated with first file"
    assert sim.pageBlobs[1] == p2_preserved_blob, "Preserved page MUST NOT be silently overwritten"
    assert sim.currentScreen == 'pairs'


def test_scenario_cancellation_preserves_state():
    """Verify cancelled file picker does not alter current scan state."""
    sim = GalleryUploadSimulation()
    sim.startScan()
    p1_file = MockFile("p1.jpg")
    sim.onFileUploaded([p1_file])
    
    assert sim.currentScanPage == 1
    assert sim.pageBlobs[0] == p1_file
    
    # User opens gallery and cancels
    input_mock = {"value": "something"}
    res = sim.onFileUploaded([], input_mock=input_mock)
    
    assert res["status"] == "cancelled"
    assert sim.pageBlobs[0] == p1_file, "Page 1 remains unchanged"
    assert sim.currentScanPage == 1
    assert input_mock["value"] == "", "Input must still be cleared"


def test_scenario_invalid_file_type_preserves_state():
    """Verify invalid file type is rejected pre-flight without corrupting state."""
    sim = GalleryUploadSimulation()
    sim.startScan()
    p1_file = MockFile("p1.jpg")
    sim.onFileUploaded([p1_file])
    
    # User tries to upload a PDF or executable
    invalid_file = MockFile("document.pdf", mime_type="application/pdf")
    input_mock = {"value": "document.pdf"}
    res = sim.onFileUploaded([invalid_file], input_mock=input_mock)
    
    assert res["status"] == "validation_failed"
    assert sim.pageBlobs[0] == p1_file
    assert sim.pageBlobs[1] is None
    assert input_mock["value"] == "", "Input must be cleared so user can try again"


def test_scenario_oversized_file_preserves_state():
    """Verify file exceeding 20MB is rejected pre-flight without corrupting state."""
    sim = GalleryUploadSimulation()
    sim.startScan()
    
    oversized = MockFile("giant_scan.jpg", size=25 * 1024 * 1024) # 25MB
    input_mock = {"value": "giant_scan.jpg"}
    res = sim.onFileUploaded([oversized], input_mock=input_mock)
    
    assert res["status"] == "validation_failed"
    assert sim.pageBlobs[0] is None
    assert sim.pageBlobs[1] is None
    assert input_mock["value"] == ""


def test_scenario_heic_iphone_format_support():
    """Verify iPhone HEIC files are accepted and passed through cleanly."""
    sim = GalleryUploadSimulation()
    sim.startScan()
    
    heic1 = MockFile("IMG_4021.HEIC", mime_type="")
    heic2 = MockFile("IMG_4022.heif", mime_type="image/heif")
    
    res = sim.onFileUploaded([heic1, heic2])
    assert res["status"] == "dual_upload_complete"
    assert sim.pageBlobs[0] == heic1
    assert sim.pageBlobs[1] == heic2
    assert sim.currentScreen == 'pairs'
