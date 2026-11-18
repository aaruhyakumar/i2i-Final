import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", ".env")))
from backend.services.supabase_service import supabase_service

sup = supabase_service
print("Supabase configured:", sup.is_configured())

test_email = "i2i_confirmed_1794815591@gmail.com"
test_pass = "SecurePassword123!"

res = sup.auth_sign_in(test_email, test_pass)
print(f"Sign in result for {test_email}:")
print("  Success:", res.get("success"))
if res.get("success"):
    print("  User ID:", res.get("user", {}).get("id"))
    print("  Email:", res.get("user", {}).get("email"))
    print("  Access Token (first 25 chars):", (res.get("access_token") or "")[:25])
else:
    print("  Error:", res.get("error"))
    print("  Message:", res.get("message"))
