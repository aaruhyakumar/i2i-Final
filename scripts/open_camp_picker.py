import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

ws_url, _ = get_page_ws_url()
client = DevToolsClient(ws_url)
client.evaluate("window.app.go('home');")
client.evaluate("if (window.app.openCampPicker) window.app.openCampPicker();")
