import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.device_webview_controller import get_page_ws_url, DevToolsClient

ws_url, _ = get_page_ws_url()
client = DevToolsClient(ws_url)

# Close modal
client.evaluate("""
    const modals = document.querySelectorAll('.modal, .drawer, [id*=\"modal\"]');
    modals.forEach(m => m.classList.remove('active', 'open', 'show'));
    const overlays = document.querySelectorAll('.overlay, .modal-backdrop');
    overlays.forEach(o => o.classList.remove('active', 'open', 'show'));
    if (window.app.closeAddCampModal) window.app.closeAddCampModal();
    if (window.app.toggleAddCampModal) window.app.toggleAddCampModal(false);
""")

# Re-navigate to profile cleanly
client.evaluate("window.app.go('settings');")
