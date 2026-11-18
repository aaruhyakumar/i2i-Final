import urllib.request
import json

url = "http://127.0.0.1:5001/api/records/rec_20261117134632_f32f41"
req = urllib.request.Request(url, headers={"Authorization": "Bearer dr_test_token"})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode())

print("--- BACKEND /api/records/rec_20261117134632_f32f41 RESPONSE ---")
print("id:", data.get("id"))
print("person_name:", data.get("person_name"))
print("gender:", data.get("gender"))
print("dob:", repr(data.get("dob")))
print("blood_group:", repr(data.get("blood_group")))
print("height:", data.get("height"))
print("weight:", data.get("weight"))
