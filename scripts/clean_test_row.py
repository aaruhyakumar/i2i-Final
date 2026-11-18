import sqlite3

conn = sqlite3.connect("clinic_records.db")
cur = conn.cursor()
cur.execute("DELETE FROM camps WHERE id = ?", ("camp_20261117124625_2355b6",))
conn.commit()
print("Deleted rows:", cur.rowcount)
