import sys
sys.path.insert(0, ".")
from src.database import DB_PATH
import sqlite3

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("INSERT OR IGNORE INTO configurations (key, value) VALUES (?, ?)", ("m365_user_timezone", "Europe/London"))
conn.commit()

# Verify
c.execute("SELECT value FROM configurations WHERE key='m365_user_timezone'")
row = c.fetchone()
print(f"m365_user_timezone = {row[0] if row else 'NOT SET'}")
conn.close()
