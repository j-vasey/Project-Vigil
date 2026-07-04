import sys
sys.path.insert(0, ".")
from src.database import DB_PATH
import sqlite3

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("SELECT key, value FROM configurations WHERE key LIKE 'm365%'")
for key, val in c.fetchall():
    if "token" in key or "secret" in key:
        display = val[:30] + "..." if val and len(val) > 30 else val
    else:
        display = val
    print(f"{key} = {display!r}")
conn.close()
