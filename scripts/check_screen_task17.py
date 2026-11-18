import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.cdp_client import eval_in_webview

try:
    res = eval_in_webview("JSON.stringify({ screen: window.app?.state?.currentScreen, camp: window.app?.state?.selectedCampId, doctor: window.app?.state?.doctorName })")
    val = res.get('result', {}).get('result', {}).get('value')
    print("CDP status:", val)
except Exception as e:
    print("CDP err:", e)
