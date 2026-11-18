import os
import sys
import json
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from backend.services.supabase_service import SupabaseService

sb = SupabaseService()
records = sb.list_records(camp_id="d95c06fa-5c10-4991-8afb-6071bef5b3fe", limit=10000)
print("Records count from Supabase:", len(records))
if records:
    r = records[0]
    print("r.get('is_paediatric'):", repr(r.get("is_paediatric")), type(r.get("is_paediatric")))
    print("r.get('target_group'):", repr(r.get("target_group")))
    print("r.get('dob'):", repr(r.get("dob")))
    print("r.get('blood_group'):", repr(r.get("blood_group")))

    def _is_paed_rec(rec):
        tg = str(rec.get("target_group") or "").lower()
        if tg in ("paediatric", "pediatric", "school"):
            return True
        if bool(rec.get("is_paediatric")) or rec.get("is_paediatric") in (1, True, "1"):
            return True
        return False

    print("_is_paed_rec(r):", _is_paed_rec(r))
