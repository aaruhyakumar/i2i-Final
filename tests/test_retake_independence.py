"""
BUG-04 Test Suite: Retake Clearing Both Scanned Pages Fix
Tests independent page preservation, single-page retake handlers,
URL lifecycle management, and page swapping across all required scenarios.
"""

import re
import pytest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_JS_PATH = BASE_DIR / "frontend" / "js" / "app.js"
INDEX_PATH = BASE_DIR / "frontend" / "index.html"
CSS_PATH = BASE_DIR / "frontend" / "css" / "styles.css"


def test_app_js_retake_page_function():
    """Verify retakePage function exists, handles single page, revokes URL, and preserves other page."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    
    # Verify export of retakePage and backToScan
    assert "export async function retakePage(pageIndex)" in app_js
    assert "export async function backToScan()" in app_js
    
    # Extract retakePage function body
    match = re.search(r"export\s+async\s+function\s+retakePage\s*\((.*?)\)\s*\{([\s\S]*?)\n\}\n\s*export", app_js)
    assert match is not None, "Could not extract retakePage function"
    body = match.group(2)
    
    # Verify it targets only the specified page
    assert "state.pageUrls[targetPage] = null" in body
    assert "state.pageBlobs[targetPage] = null" in body
    assert "URL.revokeObjectURL" in body
    assert "sc.removePage(targetPage)" in body
    assert "sc.currentPage = targetPage" in body
    assert "state.currentScanPage = targetPage" in body


def test_app_js_start_scan_delegation():
    """Verify startScan delegates to retakePage when a pageIndex is provided."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    
    match = re.search(r"export\s+async\s+function\s+startScan\s*\((.*?)\)\s*\{([\s\S]*?)\n\}\n\s*export\s+async\s+function\s+retakePage", app_js)
    assert match is not None, "Could not extract startScan function"
    body = match.group(2)
    
    # Checks targetPageIndex handling
    assert "targetPageIndex === 0 || targetPageIndex === 1" in body
    assert "return retakePage(targetPageIndex)" in body
    
    # Checks full reset revokes both URLs
    assert "URL.revokeObjectURL(state.pageUrls[0])" in body
    assert "URL.revokeObjectURL(state.pageUrls[1])" in body


def test_app_js_apply_capture_completion():
    """Verify applyCapture and applyUpload check for both pages to complete the pair after retake."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    
    # Check applyCapture
    assert "state.pageBlobs[0] && state.pageBlobs[1]" in app_js
    assert "proceedToPairsScreen();" in app_js


def test_app_js_window_app_exports():
    """Verify window.app exports retakePage and backToScan."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    
    match = re.search(r"window\.app\s*=\s*\{([\s\S]*?)\};", app_js)
    assert match is not None, "Could not extract window.app definition"
    exports = match.group(1)
    
    assert "retakePage" in exports
    assert "backToScan" in exports
    assert "startScan" in exports
    assert "swapScanPages" in exports


def test_index_html_retake_triggers():
    """Verify index.html contains independent retake buttons and navigation."""
    html = INDEX_PATH.read_text(encoding="utf-8")
    
    # Thumbnails click to retake
    assert 'onclick="window.app.retakePage(0)"' in html
    assert 'onclick="window.app.retakePage(1)"' in html
    
    # Pair check screen back button does not clear pages
    assert 'onclick="window.app.backToScan()"' in html
    
    # Pair preview slots retake buttons
    assert 'id="btn-retake-p1"' in html
    assert 'id="btn-retake-p2"' in html
    assert 'id="pairs-retake-p1-btn"' in html
    assert 'id="pairs-retake-p2-btn"' in html


class ScannerStateSimulation:
    """Simulates the state transitions of the app.js scanning & retake workflow."""
    def __init__(self):
        self.currentScanPage = 0
        self.pageBlobs = [None, None]
        self.pageUrls = [None, None]
        self.clientPageQualities = [None, None]
        self.revoked_urls = []
        self.url_counter = 0
        self.currentScreen = 'home'
        self.sc_pages = [None, None]

    def _create_url(self, label):
        self.url_counter += 1
        return f"blob:http://localhost:5001/{label}_{self.url_counter}"

    def _revoke_url(self, url):
        if url:
            self.revoked_urls.append(url)

    def startScan(self, targetPageIndex=None):
        if targetPageIndex in (0, 1):
            return self.retakePage(targetPageIndex)
            
        if self.pageUrls[0]:
            self._revoke_url(self.pageUrls[0])
        if self.pageUrls[1]:
            self._revoke_url(self.pageUrls[1])
            
        self.currentScanPage = 0
        self.pageBlobs = [None, None]
        self.pageUrls = [None, None]
        self.clientPageQualities = [None, None]
        self.sc_pages = [None, None]
        self.currentScreen = 'scan'

    def capture(self, blob_content):
        p = self.currentScanPage
        new_url = self._create_url(f"page{p+1}")
        self.pageBlobs[p] = f"Blob({blob_content})"
        self.pageUrls[p] = new_url
        self.sc_pages[p] = {"blob": self.pageBlobs[p], "url": new_url}
        
        # Apply capture transition
        if self.pageBlobs[0] and self.pageBlobs[1]:
            self.currentScreen = 'pairs'
        elif self.currentScanPage == 0:
            self.currentScanPage = 1
        else:
            self.currentScanPage = 0

    def retakePage(self, pageIndex):
        assert pageIndex in (0, 1)
        if self.pageUrls[pageIndex]:
            self._revoke_url(self.pageUrls[pageIndex])
            self.pageUrls[pageIndex] = None
        self.pageBlobs[pageIndex] = None
        self.clientPageQualities[pageIndex] = None
        self.sc_pages[pageIndex] = None
        self.currentScanPage = pageIndex
        self.currentScreen = 'scan'

    def backToScan(self):
        self.currentScanPage = 1 if self.pageBlobs[1] else 0
        self.currentScreen = 'scan'

    def swapScanPages(self):
        self.pageBlobs[0], self.pageBlobs[1] = self.pageBlobs[1], self.pageBlobs[0]
        self.pageUrls[0], self.pageUrls[1] = self.pageUrls[1], self.pageUrls[0]
        self.sc_pages[0], self.sc_pages[1] = self.sc_pages[1], self.sc_pages[0]


def test_scenario_1_retake_page_1():
    """Scenario 1: Scan both pages, retake Page 1. Verify Page 2 remains unchanged."""
    sim = ScannerStateSimulation()
    sim.startScan()
    sim.capture("Page 1 Initial")
    sim.capture("Page 2 Original")
    assert sim.currentScreen == 'pairs'
    
    p2_original_blob = sim.pageBlobs[1]
    p2_original_url = sim.pageUrls[1]
    p1_first_url = sim.pageUrls[0]
    
    # Retake Page 1
    sim.retakePage(0)
    assert sim.currentScreen == 'scan'
    assert sim.currentScanPage == 0
    assert sim.pageBlobs[0] is None
    assert sim.pageUrls[0] is None
    assert p1_first_url in sim.revoked_urls
    
    # Page 2 MUST remain unchanged
    assert sim.pageBlobs[1] == p2_original_blob
    assert sim.pageUrls[1] == p2_original_url
    assert p2_original_url not in sim.revoked_urls
    
    # Capture replacement Page 1
    sim.capture("Page 1 Replacement")
    assert sim.currentScreen == 'pairs'
    assert sim.pageBlobs[0] == "Blob(Page 1 Replacement)"
    assert sim.pageBlobs[1] == p2_original_blob
    assert sim.pageUrls[1] == p2_original_url


def test_scenario_2_retake_page_2():
    """Scenario 2: Scan both pages, retake Page 2. Verify Page 1 remains unchanged."""
    sim = ScannerStateSimulation()
    sim.startScan()
    sim.capture("Page 1 Original")
    sim.capture("Page 2 Initial")
    assert sim.currentScreen == 'pairs'
    
    p1_original_blob = sim.pageBlobs[0]
    p1_original_url = sim.pageUrls[0]
    p2_first_url = sim.pageUrls[1]
    
    # Retake Page 2
    sim.retakePage(1)
    assert sim.currentScreen == 'scan'
    assert sim.currentScanPage == 1
    assert sim.pageBlobs[1] is None
    assert sim.pageUrls[1] is None
    assert p2_first_url in sim.revoked_urls
    
    # Page 1 MUST remain unchanged
    assert sim.pageBlobs[0] == p1_original_blob
    assert sim.pageUrls[0] == p1_original_url
    assert p1_original_url not in sim.revoked_urls
    
    # Capture replacement Page 2
    sim.capture("Page 2 Replacement")
    assert sim.currentScreen == 'pairs'
    assert sim.pageBlobs[0] == p1_original_blob
    assert sim.pageBlobs[1] == "Blob(Page 2 Replacement)"


def test_scenario_3_multiple_retakes_page_1():
    """Scenario 3: Retake Page 1 multiple times. Page 2 remains intact, old URLs revoked."""
    sim = ScannerStateSimulation()
    sim.startScan()
    sim.capture("P1-v1")
    sim.capture("P2-fixed")
    
    p2_blob = sim.pageBlobs[1]
    p2_url = sim.pageUrls[1]
    
    for attempt in range(2, 5):
        sim.retakePage(0)
        assert sim.pageBlobs[1] == p2_blob
        assert sim.pageUrls[1] == p2_url
        sim.capture(f"P1-v{attempt}")
        assert sim.currentScreen == 'pairs'
        assert sim.pageBlobs[0] == f"Blob(P1-v{attempt})"
        assert sim.pageBlobs[1] == p2_blob
        
    assert p2_url not in sim.revoked_urls
    assert len(sim.revoked_urls) >= 3


def test_scenario_4_multiple_retakes_page_2():
    """Scenario 4: Retake Page 2 multiple times. Page 1 remains intact, old URLs revoked."""
    sim = ScannerStateSimulation()
    sim.startScan()
    sim.capture("P1-fixed")
    sim.capture("P2-v1")
    
    p1_blob = sim.pageBlobs[0]
    p1_url = sim.pageUrls[0]
    
    for attempt in range(2, 5):
        sim.retakePage(1)
        assert sim.pageBlobs[0] == p1_blob
        assert sim.pageUrls[0] == p1_url
        sim.capture(f"P2-v{attempt}")
        assert sim.currentScreen == 'pairs'
        assert sim.pageBlobs[1] == f"Blob(P2-v{attempt})"
        assert sim.pageBlobs[0] == p1_blob
        
    assert p1_url not in sim.revoked_urls


def test_scenario_5_retake_and_swap():
    """Scenario 5: Retake a page and then swap the two pages."""
    sim = ScannerStateSimulation()
    sim.startScan()
    sim.capture("P1-Old")
    sim.capture("P2-Keep")
    
    # Retake Page 1
    sim.retakePage(0)
    sim.capture("P1-New")
    assert sim.currentScreen == 'pairs'
    
    # Now Swap Pages
    sim.swapScanPages()
    assert sim.pageBlobs[0] == "Blob(P2-Keep)"
    assert sim.pageBlobs[1] == "Blob(P1-New)"


def test_scenario_6_back_from_pairing_screen():
    """Scenario 6: Retake a page after navigating back from the pairing screen."""
    sim = ScannerStateSimulation()
    sim.startScan()
    sim.capture("P1")
    sim.capture("P2")
    assert sim.currentScreen == 'pairs'
    
    # Navigate back to scan via backToScan()
    sim.backToScan()
    assert sim.currentScreen == 'scan'
    # Both pages still present
    assert sim.pageBlobs[0] is not None
    assert sim.pageBlobs[1] is not None
    
    # Now user chooses to retake Page 1
    sim.retakePage(0)
    assert sim.pageBlobs[0] is None
    assert sim.pageBlobs[1] == "Blob(P2)"
    
    # Re-capture Page 1
    sim.capture("P1-Updated")
    assert sim.currentScreen == 'pairs'
    assert sim.pageBlobs[0] == "Blob(P1-Updated)"
    assert sim.pageBlobs[1] == "Blob(P2)"


def test_scenario_7_both_pages_available_for_extraction():
    """Scenario 7: Confirm both pages remain non-null and valid before extraction."""
    sim = ScannerStateSimulation()
    sim.startScan()
    sim.capture("Page 1 Content")
    sim.capture("Page 2 Content")
    
    # Retake page 2
    sim.retakePage(1)
    sim.capture("Page 2 Final")
    
    # Verify extraction precondition: both pages present
    assert sim.pageBlobs[0] is not None and sim.pageBlobs[0] != ""
    assert sim.pageBlobs[1] is not None and sim.pageBlobs[1] != ""
    assert sim.pageUrls[0] is not None and sim.pageUrls[0].startswith("blob:")
    assert sim.pageUrls[1] is not None and sim.pageUrls[1].startswith("blob:")
    assert sim.pageUrls[0] not in sim.revoked_urls
    assert sim.pageUrls[1] not in sim.revoked_urls
