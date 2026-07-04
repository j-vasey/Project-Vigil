import os
import sqlite3
import asyncio
from datetime import datetime, timezone
from src.mcp.servers.base import MCPServer

server = MCPServer("server-reminders")

db_initialized = False

def get_db_connection():
    global db_initialized
    if not db_initialized:
        from src.database import init_db
        init_db()
        db_initialized = True
    from src.database import DB_PATH
    return sqlite3.connect(DB_PATH)


@server.register_tool(
    name="set_reminder",
    description="Set a future reminder or notification to be sent to the user. Times must be provided in LOCAL time. The system will send the message automatically when the time is reached.",
    input_schema={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "The exact message to send when the reminder fires."},
            "remind_at_local": {"type": "string", "description": "The target time in the user's LOCAL timezone as ISO 8601 (e.g. '2026-07-06T13:00:00'). Do NOT convert to UTC."}
        },
        "required": ["message", "remind_at_local"]
    }
)
async def set_reminder(message: str, remind_at_local: str) -> str:
    try:
        from src.mcp.servers.calendar import _normalise_datetime
        
        # 1. Normalise to aware UTC datetime
        aware_utc = _normalise_datetime(remind_at_local)
        utc_iso = aware_utc.isoformat()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO reminders (message, remind_at, fired, created_at) VALUES (?, ?, 0, ?)",
            (message, utc_iso, datetime.now(timezone.utc).isoformat())
        )
        reminder_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return f"Success: Reminder #{reminder_id} scheduled for {remind_at_local} (local)."
    except Exception as e:
        return f"Failed to set reminder: {e}"


@server.register_tool(
    name="list_reminders",
    description="List all pending upcoming reminders.",
    input_schema={"type": "object", "properties": {}}
)
async def list_reminders() -> str:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, message, remind_at FROM reminders WHERE fired = 0 ORDER BY remind_at ASC")
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return "No pending reminders."
            
        summary = []
        for row in rows:
            summary.append(f"#{row[0]} | Due: {row[2]} UTC | Message: '{row[1]}'")
            
        return "\n".join(summary)
    except Exception as e:
        return f"Database Error: {e}"


@server.register_tool(
    name="cancel_reminder",
    description="Cancel a scheduled reminder by its ID.",
    input_schema={
        "type": "object",
        "properties": {
            "reminder_id": {"type": "integer", "description": "The ID of the reminder to cancel."}
        },
        "required": ["reminder_id"]
    }
)
async def cancel_reminder(reminder_id: int) -> str:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM reminders WHERE id = ?", (reminder_id,))
        if not cursor.fetchone():
            conn.close()
            return f"Reminder #{reminder_id} not found."
            
        cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()
        conn.close()
        
        return f"Success: Reminder #{reminder_id} cancelled."
    except Exception as e:
        return f"Database Error: {e}"


if __name__ == "__main__":
    asyncio.run(server.run())
