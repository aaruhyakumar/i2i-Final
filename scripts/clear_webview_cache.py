import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.cdp_client import eval_in_webview

clear_js = """
(async () => {
    if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        for (let r of regs) await r.unregister();
    }
    if ('caches' in window) {
        const keys = await caches.keys();
        for (let k of keys) await caches.delete(k);
    }
    setTimeout(() => { window.location.href = 'http://127.0.0.1:5001/?v=' + Date.now(); }, 200);
    return 'Cache cleared';
})()
"""
res = eval_in_webview(clear_js)
print("Clear cache result:", res)
time.sleep(4)

res2 = eval_in_webview("window.app?.renderStaticReview ? window.app.renderStaticReview.toString().includes('record.dob') : false")
print("Has record.dob in WebView app.js:", res2.get('result', {}).get('result', {}).get('value'))
