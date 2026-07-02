import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from src.mcp.servers.base import MCPServer

server = MCPServer("server-scheduler")

def get_db_connection():
    from src.database import DB_PATH
    return sqlite3.connect(DB_PATH)

def db_get_config(key: str, default: str = "") -> str:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM configurations WHERE key=?", (key,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception:
        pass
    return default

def db_set_config(key: str, value: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO configurations (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

@server.register_tool(
    name="get_active_schedules",
    description="Retrieve all current proactive scheduler configurations and active Do-Not-Disturb limits.",
    input_schema={"type": "object", "properties": {}}
)
async def get_active_schedules() -> str:
    configs = {
        "proactive_platform": db_get_config("proactive_platform", "telegram"),
        "proactive_interval_seconds": db_get_config("proactive_interval_seconds", "14400"),
        "proactive_probability": db_get_config("proactive_probability", "0.25"),
        "proactive_jitter_percentage": db_get_config("proactive_jitter_percentage", "0.30"),
        "dnd_start": db_get_config("dnd_start", "22:00"),
        "dnd_end": db_get_config("dnd_end", "08:00"),
        "system_health": db_get_config("system_health", "healthy"),
        "proactivity_paused_until": db_get_config("proactivity_paused_until", "")
    }
    import json
    return json.dumps(configs, indent=2)

@server.register_tool(
    name="modify_schedule",
    description="Modify the proactivity scheduler configurations dynamically.",
    input_schema={
        "type": "object",
        "properties": {
            "interval_seconds": {"type": "integer", "description": "New proactivity interval in seconds."},
            "proactive_probability": {"type": "number", "description": "New trigger probability (0.0 to 1.0)."},
            "proactive_jitter_percentage": {"type": "number", "description": "New trigger random sleep jitter offset (0.0 to 1.0)."},
            "dnd_start": {"type": "string", "description": "New Do-Not-Disturb start hour format 'HH:MM'."},
            "dnd_end": {"type": "string", "description": "New Do-Not-Disturb end hour format 'HH:MM'."}
        }
    }
)
async def modify_schedule(
    interval_seconds: int = None,
    proactive_probability: float = None,
    proactive_jitter_percentage: float = None,
    dnd_start: str = None,
    dnd_end: str = None
) -> str:
    updates = {}
    if interval_seconds is not None:
        db_set_config("proactive_interval_seconds", str(interval_seconds))
        updates["proactive_interval_seconds"] = interval_seconds
    if proactive_probability is not None:
        db_set_config("proactive_probability", str(proactive_probability))
        updates["proactive_probability"] = proactive_probability
    if proactive_jitter_percentage is not None:
        db_set_config("proactive_jitter_percentage", str(proactive_jitter_percentage))
        updates["proactive_jitter_percentage"] = proactive_jitter_percentage
    if dnd_start is not None:
        db_set_config("dnd_start", dnd_start)
        updates["dnd_start"] = dnd_start
    if dnd_end is not None:
        db_set_config("dnd_end", dnd_end)
        updates["dnd_end"] = dnd_end
        
    return f"Success: Scheduler configurations updated with values: {updates}"

@server.register_tool(
    name="pause_proactivity_window",
    description="Pause autonomous proactive outreach temporarily for a set duration in minutes.",
    input_schema={
        "type": "object",
        "properties": {
            "duration_minutes": {"type": "integer", "description": "Duration in minutes to pause the engine."}
        },
        "required": ["duration_minutes"]
    }
)
async def pause_proactivity_window(duration_minutes: int) -> str:
    paused_until = datetime.utcnow() + timedelta(minutes=duration_minutes)
    paused_until_iso = paused_until.isoformat()
    db_set_config("proactivity_paused_until", paused_until_iso)
    return f"Success: Outbound outreach engine paused temporarily until {paused_until_iso} UTC."

if __name__ == "__main__":
    asyncio.run(server.run())
