import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

ws_url, _ = get_page_ws_url()
client = DevToolsClient(ws_url)
print("apiBase in JS:", client.evaluate("window.app.apiBase"))
print("location.href:", client.evaluate("window.location.href"))
print("localStorage server_url:", client.evaluate("localStorage.getItem('server_url')"))
