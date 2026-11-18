import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

ws_url, _ = get_page_ws_url()
client = DevToolsClient(ws_url)

res = client.evaluate("""
    const el = Array.from(document.querySelectorAll('button, a, div')).find(e => e.innerText && e.innerText.trim() === 'Cancel');
    if (el) {
        el.click();
        'Clicked Cancel'
    } else {
        'Cancel not found'
    }
""")
print(res)
