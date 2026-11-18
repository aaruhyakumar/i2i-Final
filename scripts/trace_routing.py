import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from backend.services.record_service import RecordService
from backend.services.supabase_service import supabase_service

camp_id = "d95c06fa-5c10-4991-8afb-6071bef5b3fe"
status_filter = None
target_org = None

records = supabase_service.list_records(
    camp_id=camp_id,
    status=status_filter,
    community_id=target_org,
    limit=10000
)
camps = supabase_service.list_camps_with_stats(community_id=target_org)
print("Records count:", len(records))
print("Camps count:", len(camps))

selected_camp = None
if camp_id:
    matching = [c for c in camps if c["id"] == camp_id]
    if matching:
        selected_camp = matching[0]
    camps = matching

print("selected_camp:", selected_camp.get("name") if selected_camp else None)

def _is_paed_rec(rec):
    if not rec:
        return False
    tg = str(rec.get("target_group") or "").lower()
    if tg in ("paediatric", "pediatric", "school"):
        return True
    if bool(rec.get("is_paediatric")) or rec.get("is_paediatric") in (1, True, "1"):
        return True
    tpl = str(rec.get("template_id") or rec.get("form_type") or "").lower()
    if any(k in tpl for k in ("paediatric", "pediatric", "school")):
        return True
    return False

def _is_paed_camp(c):
    if not c:
        return False
    tg = str(c.get("target_group") or "").lower()
    if tg in ("paediatric", "pediatric", "school"):
        return True
    c_name = str(c.get("name") or "").lower()
    if any(k in c_name for k in ("school", "paediatric", "pediatric", "vidyalaya", "academy", "matriculation")):
        return True
    return False

camp_is_paed = _is_paed_camp(selected_camp)
print("camp_is_paed:", camp_is_paed)
has_adult = any(not _is_paed_rec(r) and not camp_is_paed for r in records) if records else (not camp_is_paed)
has_paediatric = any(_is_paed_rec(r) or camp_is_paed for r in records) if records else camp_is_paed
print("has_adult:", has_adult)
print("has_paediatric:", has_paediatric)
if has_adult and has_paediatric and not camp_is_paed:
    print("BRANCH: multi_family")
elif has_paediatric or camp_is_paed:
    print("BRANCH: paediatric")
else:
    print("BRANCH: adult")
