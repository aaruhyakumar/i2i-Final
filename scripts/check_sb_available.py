import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from backend.services.supabase_service import supabase_service
print("supabase_service.is_configured():", supabase_service.is_configured())
print("supabase_service.is_available():", supabase_service.is_available())
try:
    recs = supabase_service.list_records(camp_id="d95c06fa-5c10-4991-8afb-6071bef5b3fe", limit=10000)
    print("list_records result:", len(recs) if recs else recs)
except Exception as e:
    print("list_records error:", e)
