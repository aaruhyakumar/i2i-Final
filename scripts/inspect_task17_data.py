import json

path = r"C:\Users\ADMIN\.gemini\antigravity\brain\08c82ba0-7e7b-48ad-a73e-53596d537b47\task14b_extraction_data.json"
with open(path, "r", encoding="utf-8") as f:
    d = json.load(f)

tel_data = d.get("telemetry", {}).get("data", {})
fields = tel_data.get("fields", {})

print("--- ALL FIELDS EXTRACTED BY CLAUDE (with confidence) ---")
for k, v in sorted(fields.items()):
    val, conf = v[0], v[1]
    if val is not None:
        print(f"  {k}: {repr(val)} (conf: {conf})")
    else:
        print(f"  {k}: <null> (conf: {conf})")
