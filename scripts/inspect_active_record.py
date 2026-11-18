import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.cdp_client import eval_in_webview

res = eval_in_webview("JSON.stringify(window.app.state.activeRecord)")
val = res.get('result', {}).get('result', {}).get('value')
if val:
    rec = json.loads(val)
    print("Keys in activeRecord on phone:", sorted(list(rec.keys())))
    print("id:", rec.get("id"))
    print("person_name:", rec.get("person_name"))
    print("gender:", rec.get("gender"))
    print("dob:", repr(rec.get("dob")))
    print("blood_group:", repr(rec.get("blood_group")))
    print("is_paediatric:", repr(rec.get("is_paediatric")))
    print("structured_record:", type(rec.get("structured_record")), repr(rec.get("structured_record"))[:200])
else:
    print("activeRecord is None or empty")
