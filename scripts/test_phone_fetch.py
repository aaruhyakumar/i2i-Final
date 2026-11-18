import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

ws_url, _ = get_page_ws_url()
client = DevToolsClient(ws_url)
res = client.evaluate("""
    fetch('http://127.0.0.1:5001/api/health')
        .then(r => r.json())
        .then(d => JSON.stringify(d))
        .catch(e => 'FETCH_ERROR: ' + e.message)
""")
print("Health fetch from phone webview:", res)
