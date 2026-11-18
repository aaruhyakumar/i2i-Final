import requests

login_payload = {
    "email": "i2i_confirmed_1794815591@gmail.com",
    "password": "SecurePassword123!"
}
res_login = requests.post("http://127.0.0.1:5001/api/auth/login", json=login_payload, timeout=10)
token = res_login.json()["token"]

headers = {
    "Authorization": f"Bearer {token}"
}

res_camps = requests.get("http://127.0.0.1:5001/api/camps", headers=headers, timeout=5)
print(f"/api/camps: HTTP {res_camps.status_code}")
print("Camps data:", res_camps.json())

res_prof = requests.get("http://127.0.0.1:5001/api/doctor/profile", headers=headers, timeout=5)
print(f"/api/doctor/profile: HTTP {res_prof.status_code}")
print("Doctor Profile data:", res_prof.json())
