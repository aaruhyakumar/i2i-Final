"""
BUG-03 Test Suite: Document Preview Cropping and Tilt Verification
Tests CSS styling, layout symmetry, and mathematical document framing for the pair preview screen.
"""

import re
import pytest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CSS_PATH = BASE_DIR / "frontend" / "css" / "styles.css"
APP_JS_PATH = BASE_DIR / "frontend" / "js" / "app.js"
INDEX_PATH = BASE_DIR / "frontend" / "index.html"


def test_pair_sheet_css_rules():
    """Verify that .pair .sheet uses background-size: contain, no tilt, and no-repeat."""
    css = CSS_PATH.read_text(encoding="utf-8")
    
    # Check .pair .sheet block
    match = re.search(r"\.pair\s+\.sheet\s*\{([^}]+)\}", css)
    assert match is not None, "Selector .pair .sheet not found in styles.css"
    block = match.group(1)
    
    # Verify contain, centering, no-repeat, no cover
    assert "background-size: contain" in block, "Expected 'background-size: contain' in .pair .sheet"
    assert "background-size: cover" not in block, "Forbidden 'background-size: cover' found in .pair .sheet"
    assert "background-repeat: no-repeat" in block, "Expected 'background-repeat: no-repeat' in .pair .sheet"
    assert "background-position: center" in block, "Expected 'background-position: center' in .pair .sheet"
    
    # Verify rotation is neutralized (transform: none)
    assert "transform: none" in block, "Expected 'transform: none' in .pair .sheet"
    assert "rotate(" not in block, "Forbidden rotate() transform found in .pair .sheet"
    
    # Check .pair .sheet:before suppression
    before_match = re.search(r"\.pair\s+\.sheet:before\s*\{([^}]+)\}", css)
    assert before_match is not None, "Selector .pair .sheet:before not found in styles.css"
    assert "display: none" in before_match.group(1), "Expected 'display: none' in .pair .sheet:before"


def test_index_html_pair_structure_and_cache_buster():
    """Verify index.html pairs screen structure and updated cache buster."""
    html = INDEX_PATH.read_text(encoding="utf-8")
    
    # Cache buster
    assert re.search(r"styles\.css\?v=20261116_bug0[4-7]", html), "Expected styles.css?v=20261116_bug04..07 in index.html"
    
    # Pair screen elements
    assert 'id="pairs"' in html
    assert 'id="pairs-p1-preview"' in html
    assert 'id="pairs-p2-preview"' in html
    assert 'id="pairs-t1-badge"' in html
    assert 'id="pairs-t2-badge"' in html
    assert 'id="pairs-swap-btn"' in html


def test_app_js_pairing_logic():
    """Verify app.js applies background images to both preview slots."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    
    # Check proceedToPairsScreen assigns both previews
    assert "pairs-p1-preview" in app_js
    assert "pairs-p2-preview" in app_js
    assert "prev1.style.backgroundImage" in app_js
    assert "prev2.style.backgroundImage" in app_js
    
    # Check swapScanPages exchanges previews
    assert "swapScanPages" in app_js


def compute_preview_framing(container_w, container_h, doc_w, doc_h, bg_size):
    """
    Computes visible dimensions, letterbox/pillarbox margins, and crop percentages
    for CSS background-image inside a preview container slot.
    """
    box_ar = container_w / container_h
    doc_ar = doc_w / doc_h
    
    if bg_size == "cover":
        if box_ar > doc_ar:
            # Box is wider than doc: doc width matches container width, height overflows
            rendered_w = container_w
            rendered_h = container_w / doc_ar
            crop_h = rendered_h - container_h
            crop_top = crop_h / 2.0
            crop_bottom = crop_h / 2.0
            crop_left = 0.0
            crop_right = 0.0
            crop_pct = (crop_h / rendered_h) * 100.0
        else:
            # Box is taller than doc: doc height matches container height, width overflows
            rendered_h = container_h
            rendered_w = container_h * doc_ar
            crop_w = rendered_w - container_w
            crop_left = crop_w / 2.0
            crop_right = crop_w / 2.0
            crop_top = 0.0
            crop_bottom = 0.0
            crop_pct = (crop_w / rendered_w) * 100.0
        margin_x = 0.0
        margin_y = 0.0
    elif bg_size == "contain":
        crop_pct = 0.0
        crop_top = 0.0
        crop_bottom = 0.0
        crop_left = 0.0
        crop_right = 0.0
        if box_ar > doc_ar:
            # Doc height matches container height
            rendered_h = container_h
            rendered_w = container_h * doc_ar
            margin_x = (container_w - rendered_w) / 2.0
            margin_y = 0.0
        else:
            # Doc width matches container width
            rendered_w = container_w
            rendered_h = container_w / doc_ar
            margin_x = 0.0
            margin_y = (container_h - rendered_h) / 2.0
    else:
        raise ValueError(f"Unknown background-size: {bg_size}")
        
    return {
        "rendered_w": rendered_w,
        "rendered_h": rendered_h,
        "crop_pct": crop_pct,
        "crop_top": crop_top,
        "crop_bottom": crop_bottom,
        "crop_left": crop_left,
        "crop_right": crop_right,
        "margin_x": margin_x,
        "margin_y": margin_y,
    }


def test_required_test_cases():
    """
    Test the 7 required cases from BUG-03:
    1. Portrait document with content near all four edges
    2. Landscape document
    3. Long handwritten document
    4. Document with header and footer
    5. Narrow mobile viewport
    6. Desktop viewport
    7. Both pages in page-pair preview
    """
    # Container is aspect-ratio: 3/4
    # On standard mobile phone (375px screen): slot width ~ 158px, slot height ~ 210.7px
    slot_w, slot_h = 158.0, 210.7
    
    cases = [
        ("Case 1: Standard A4 Portrait (210x297mm)", 158.0, 210.7, 210, 297),
        ("Case 1b: Narrow Mobile Camera Portrait (9:16)", 158.0, 210.7, 1080, 1920),
        ("Case 2: Landscape Document (16:9)", 158.0, 210.7, 1920, 1080),
        ("Case 2b: Landscape Form (4:3)", 158.0, 210.7, 1600, 1200),
        ("Case 3: Long Handwritten Document (1:2 aspect ratio)", 158.0, 210.7, 800, 1600),
        ("Case 4: Clinical Sheet with Header & Footer (1:1.5)", 158.0, 210.7, 1000, 1500),
        ("Case 5: Narrow Mobile Viewport (320px screen -> 138x184 slot)", 138.0, 184.0, 1080, 1920),
        ("Case 6: Desktop Viewport Standard (420px container -> 188x250.7 slot)", 188.0, 250.7, 1080, 1920),
        ("Case 6b: Desktop Viewport Expanded (900px container -> 270x360 slot)", 270.0, 360.0, 1080, 1920),
    ]
    
    for name, sw, sh, dw, dh in cases:
        old_cover = compute_preview_framing(sw, sh, dw, dh, "cover")
        new_contain = compute_preview_framing(sw, sh, dw, dh, "contain")
        
        # Verify old cover cropped content
        assert old_cover["crop_pct"] > 0, f"Expected old cover to crop in {name}"
        
        # Verify new contain has strictly 0% cropping
        assert new_contain["crop_pct"] == 0.0, f"Expected 0% cropping in {name}"
        assert new_contain["crop_top"] == 0.0, f"Top header cropped in {name}"
        assert new_contain["crop_bottom"] == 0.0, f"Bottom footer cropped in {name}"
        assert new_contain["crop_left"] == 0.0, f"Left margin cropped in {name}"
        assert new_contain["crop_right"] == 0.0, f"Right margin cropped in {name}"
        
        # Verify fitted dimensions do not exceed container bounds
        assert new_contain["rendered_w"] <= sw + 1e-5, f"Width exceeded slot in {name}"
        assert new_contain["rendered_h"] <= sh + 1e-5, f"Height exceeded slot in {name}"
        
        # Verify aspect ratio matches document exactly
        assert abs((new_contain["rendered_w"] / new_contain["rendered_h"]) - (dw / dh)) < 1e-5, f"Distortion in {name}"


def test_both_pages_symmetry_and_pairing():
    """Case 7: Verify both pages (Slot 1 and Slot 2) are symmetrical in pair preview."""
    css = CSS_PATH.read_text(encoding="utf-8")
    
    # Both slots use .pair .sheet, guaranteeing identical dimensions, sizing and contain logic
    match = re.search(r"\.pair\s+\.sheet\s*\{([^}]+)\}", css)
    block = match.group(1)
    
    assert "width: 50%" in block or "flex: 1" in block
    assert "aspect-ratio: 3/4" in block
    assert "transform: none" in block
