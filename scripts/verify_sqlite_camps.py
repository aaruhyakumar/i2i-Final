import sqlite3

conn = sqlite3.connect("clinic_records.db")
c = conn.cursor()
c.execute("SELECT id, name, community_id, created_by, created_at FROM camps WHERE name LIKE '%Supabase Outreach%'")
rows = c.fetchall()
print(f"Total matching camps in SQLite: {len(rows)}")
for r in rows:
    print(r)

c.execute("SELECT id, name, community_id, created_by, created_at FROM camps ORDER BY created_at DESC LIMIT 5")
print("\nLatest 5 camps in SQLite:")
for r in c.fetchall():
    print(r)
