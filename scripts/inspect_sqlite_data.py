import sqlite3
import json

conn = sqlite3.connect("clinic_records.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("PRAGMA table_info(camps);")
cols = [dict(r) for r in cur.fetchall()]
print("Camps columns:", [c["name"] for c in cols])

cur.execute("SELECT * FROM camps;")
camps = [dict(r) for r in cur.fetchall()]
print(f"Total camps in SQLite: {len(camps)}")
for c in camps:
    print(c)

cur.execute("SELECT id, auth_id, email, full_name, specialization, community_id, community_code, is_active FROM doctors;")
docs = [dict(r) for r in cur.fetchall()]
print(f"\nTotal doctors in SQLite: {len(docs)}")
for d in docs:
    print(d)
