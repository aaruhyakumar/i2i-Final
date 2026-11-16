"""
BUG-02 Test Suite: Camera Viewfinder Margin Clipping and Framing Verification
Tests CSS styling, capture consistency, and mathematical framing across viewports.
"""

import re
import pytest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CSS_PATH = BASE_DIR / "frontend" / "css" / "styles.css"
SCANNER_PATH = BASE_DIR / "frontend" / "js" / "scanner.js"
INDEX_PATH = BASE_DIR / "frontend" / "index.html"


def test_viewfinder_css_contain_and_centering():
    """Verify that .vf video uses object-fit: contain and object-position: center."""
    css = CSS_PATH.read_text(encoding="utf-8")
    
    # Locate .vf video selector block
    match = re.search(r"\.vf\s+video\s*\{([^}]+)\}", css)
    assert match is not None, "Selector .vf video not found in styles.css"
    
    block = match.group(1)
    
    # Verify required properties
    assert "object-fit: contain" in block, "Expected 'object-fit: contain' in .vf video"
    assert "object-fit: cover" not in block, "Forbidden 'object-fit: cover' found in .vf video"
    assert "object-position: center" in block, "Expected 'object-position: center' in .vf video"
    assert "border-radius" in block, "Expected 'border-radius' in .vf video"


def test_index_html_cache_buster():
    """Verify index.html has an updated cache-buster for styles.css."""
    html = INDEX_PATH.read_text(encoding="utf-8")
    assert re.search(r'href="css/styles\.css\?v=[^"]+"', html), "Cache buster query string missing from styles.css link"


def test_scanner_capture_consistency():
    """Verify DocumentScanner.captureCurrentFrame() captures uncropped 1:1 video frame."""
    scanner_js = SCANNER_PATH.read_text(encoding="utf-8")
    
    # Extract captureCurrentFrame function body up to handleFileUpload
    match = re.search(r"async\s+captureCurrentFrame\s*\(\)\s*\{([\s\S]*?)\n\s*async\s+handleFileUpload", scanner_js)
    assert match is not None, "Could not extract captureCurrentFrame() method"
    
    body = match.group(1)
    
    # Check width/height assignments match video feed
    assert "canvas.width = width" in body
    assert "canvas.height = height" in body
    assert "video.videoWidth" in body
    assert "video.videoHeight" in body
    
    # Check drawImage draws the full 0,0,width,height frame without sub-rect clipping
    assert "ctx.drawImage(video, 0, 0, width, height)" in body
    
    # Check JPEG capture quality is preserved
    assert "'image/jpeg', 0.92" in body


def compute_framing(vf_width, vf_height, video_width, video_height, object_fit):
    """
    Computes visible dimensions, letterbox/pillarbox margins, and crop percentages
    for replaced video content inside a container box.
    """
    vf_ar = vf_width / vf_height
    video_ar = video_width / video_height
    
    if object_fit == "cover":
        if vf_ar > video_ar:
            # Container is wider than video: scale width to match container width
            rendered_w = vf_width
            rendered_h = vf_width / video_ar
            crop_h = rendered_h - vf_height
            crop_pct = (crop_h / rendered_h) * 100.0
            visible_w = vf_width
            visible_h = vf_height
        else:
            # Container is taller than video: scale height to match container height
            rendered_h = vf_height
            rendered_w = vf_height * video_ar
            crop_w = rendered_w - vf_width
            crop_pct = (crop_w / rendered_w) * 100.0
            visible_w = vf_width
            visible_h = vf_height
        letterbox_x = 0
        letterbox_y = 0
    elif object_fit == "contain":
        crop_pct = 0.0
        if vf_ar > video_ar:
            # Scale height to match container height
            rendered_h = vf_height
            rendered_w = vf_height * video_ar
            letterbox_x = (vf_width - rendered_w) / 2.0
            letterbox_y = 0.0
        else:
            # Scale width to match container width
            rendered_w = vf_width
            rendered_h = vf_width / video_ar
            letterbox_x = 0.0
            letterbox_y = (vf_height - rendered_h) / 2.0
        visible_w = rendered_w
        visible_h = rendered_h
    else:
        raise ValueError(f"Unsupported fit: {object_fit}")
        
    return {
        "rendered_w": rendered_w,
        "rendered_h": rendered_h,
        "visible_w": visible_w,
        "visible_h": visible_h,
        "letterbox_x": letterbox_x,
        "letterbox_y": letterbox_y,
        "crop_pct": crop_pct,
    }


def test_framing_scenarios():
    """
    Test portrait, landscape, desktop, and mobile viewports comparing
    'cover' (old bug) vs 'contain' (fix).
    """
    scenarios = [
        # (name, vf_w, vf_h, stream_w, stream_h)
        ("Portrait Phone (375x667 screen) with Portrait Camera (1080x1920)", 343, 300, 1080, 1920),
        ("Portrait Phone (375x667 screen) with Landscape Camera (1920x1080)", 343, 300, 1920, 1080),
        ("Narrow Mobile (320x568 screen) with Portrait Camera (720x1280)", 288, 250, 720, 1280),
        ("Landscape Mobile (667x375 screen) with Landscape Camera (1920x1080)", 635, 220, 1920, 1080),
        ("Desktop Standard (420px container) with HD Webcam (1280x720)", 388, 350, 1280, 720),
        ("Desktop Expanded (900px container) with HD Webcam (1280x720)", 868, 400, 1280, 720),
        ("A4 Document (Aspect Ratio 1:1.414) in Portrait Stream", 343, 300, 1080, 1527),
    ]
    
    for name, vf_w, vf_h, vid_w, vid_h in scenarios:
        old_cover = compute_framing(vf_w, vf_h, vid_w, vid_h, "cover")
        new_contain = compute_framing(vf_w, vf_h, vid_w, vid_h, "contain")
        
        # Verify old behavior caused significant margin clipping
        assert old_cover["crop_pct"] > 0, f"Expected cropping under cover for {name}"
        
        # Verify new contain behavior has zero cropping
        assert new_contain["crop_pct"] == 0.0, f"Expected 0% cropping under contain for {name}"
        
        # Verify rendered dimensions fit within container
        assert new_contain["rendered_w"] <= vf_w + 1e-6, f"Width overflow in {name}"
        assert new_contain["rendered_h"] <= vf_h + 1e-6, f"Height overflow in {name}"
        
        # Verify aspect ratio of displayed video exactly matches source camera feed
        rendered_ar = new_contain["rendered_w"] / new_contain["rendered_h"]
        expected_ar = vid_w / vid_h
        assert abs(rendered_ar - expected_ar) < 1e-5, f"Aspect ratio distortion in {name}"
