import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.cdp_client import eval_in_webview

res = eval_in_webview("window.app.renderStaticReview.toString()")
code = res.get('result', {}).get('result', {}).get('value')
print("--- renderStaticReview in WebView ---")
for line in code.split("\n"):
    if "rev-dob" in line or "rev-blood-group" in line or "p_info" in line:
        print(line)
