import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

ws_url, _ = get_page_ws_url()
client = DevToolsClient(ws_url)

client.evaluate("""
    const ov = document.getElementById('ov');
    if (ov) ov.classList.remove('on');
    // Also dismiss react native warning bar by clicking its X
    const closeBtn = document.querySelector('[aria-label=\"Close\"], .dismiss');
    if (closeBtn) closeBtn.click();
""")
print("Closed overlay 'ov'")
