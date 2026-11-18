import requests
import json

payload = {
    "email": "i2i_confirmed_1794815591@gmail.com",
    "password": "SecurePassword123!"
}

res = requests.post("http://127.0.0.1:5001/api/auth/login", json=payload, timeout=10)
print(f"Login API Status: {res.status_code}")
data = res.json()
print("Success:", data.get("success"))
print("User:", data.get("user"))
print("Doctor:", data.get("doctor"))
print("Community:", data.get("community"))
print("Token type:", data.get("token_type"))
print("Token present:", bool(data.get("token")))
print("Token prefix:", (data.get("token") or "")[:25])
