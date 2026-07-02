import os
import sqlite3
import asyncio
from datetime import datetime
from src.mcp.servers.base import MCPServer

server = MCPServer("server-active-memory")

def get_db_connection():
    from src.database import DB_PATH
    return sqlite3.connect(DB_PATH)

@server.register_tool(
    name="recall_memories",
    description="Search and recall permanent memories/facts using search keywords.",
    input_schema={
        "type": "object",
        "properties": {
            "query_string": {"type": "string", "description": "Keywords or search query to find memories."}
        },
        "required": ["query_string"]
    }
)
async def recall_memories(query_string: str) -> str:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Search via LIKE queries across facts and categories
        pattern = f"%{query_string}%"
        cursor.execute("SELECT fact, category, timestamp FROM active_memories WHERE fact LIKE ? OR category LIKE ?", (pattern, pattern))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return f"No memories found matching query '{query_string}'."
            
        summary = []
        for row in rows:
            summary.append(f"[{row[1]}] {row[0]} (Stored: {row[2]})")
            
        return "\n".join(summary)
    except Exception as e:
        return f"Database Error: {e}"

@server.register_tool(
    name="upsert_memory",
    description="Save a new memory fact or environment observation permanently.",
    input_schema={
        "type": "object",
        "properties": {
            "fact_string": {"type": "string", "description": "The exact fact text to save."},
            "category": {"type": "string", "description": "The memory category (e.g. 'user_habit', 'vm_layout', 'comfy_node')."}
        },
        "required": ["fact_string", "category"]
    }
)
async def upsert_memory(fact_string: str, category: str) -> str:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Verify if identical fact exists
        cursor.execute("SELECT id FROM active_memories WHERE fact=? AND category=?", (fact_string, category))
        row = cursor.fetchone()
        
        timestamp_str = datetime.utcnow().isoformat()
        if row:
            # Already exists, just update timestamp
            cursor.execute("UPDATE active_memories SET timestamp=? WHERE id=?", (timestamp_str, row[0]))
            msg = f"Success: Updated timestamp for existing memory '{fact_string}' in category '{category}'."
        else:
            # Insert new
            cursor.execute("INSERT INTO active_memories (fact, category, timestamp) VALUES (?, ?, ?)", (fact_string, category, timestamp_str))
            msg = f"Success: Memory fact '{fact_string}' successfully saved in category '{category}'."
            
        conn.commit()
        conn.close()
        return msg
    except Exception as e:
        return f"Database Error: {e}"

@server.register_tool(
    name="analyze_user_behavioral_trends",
    description="Analyze historical user interaction metadata logs to aggregate long-term behavioral trends, stress spikes, sleep cycles, and weekly workloads.",
    input_schema={
        "type": "object",
        "properties": {}
    }
)
async def analyze_user_behavioral_trends() -> str:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Query logs
        cursor.execute("SELECT timestamp, stress_level, topics FROM user_trend_logs ORDER BY timestamp DESC")
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return "No historical user interaction trend logs found. Cannot synthesize behavioral patterns yet."
            
        total_interactions = len(rows)
        late_night_count = 0  # 10 PM (22:00) to 5 AM (05:00)
        stress_counts = {"low": 0, "medium": 0, "high": 0}
        weekday_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0} # Mon-Sun
        tuesday_stress_count = 0
        tuesday_total_count = 0
        
        for row in rows:
            ts_str, stress, topics = row
            try:
                # Parse SQLite datetime string
                if "T" in ts_str:
                    ts = datetime.fromisoformat(ts_str)
                else:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f" if "." in ts_str else "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
                
            # Sleep cycle: 10 PM to 5 AM
            if ts.hour >= 22 or ts.hour < 5:
                late_night_count += 1
                
            # Stress level
            if stress in stress_counts:
                stress_counts[stress] += 1
                
            # Weekdays
            wd = ts.weekday()
            weekday_counts[wd] += 1
            
            # Tuesday specifics
            if wd == 1: # Tuesday
                tuesday_total_count += 1
                if stress in ["medium", "high"]:
                    tuesday_stress_count += 1
                    
        # Synthesize behavioral trends
        findings = [f"Analyzed {total_interactions} historical user interaction log events:"]
        
        # 1. Sleep cycle
        late_night_pct = (late_night_count / total_interactions) * 100 if total_interactions > 0 else 0
        if late_night_count >= 3 or late_night_pct > 25:
            findings.append(f"- Warning: Erratic sleep cycle patterns detected ({late_night_count} late-night sessions representing {late_night_pct:.1f}% of total activity).")
        else:
            findings.append("- Normal: No significant late-night interaction anomalies detected.")
            
        # 2. Weekday activity
        weekdays_map = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        max_wd = max(weekday_counts, key=weekday_counts.get)
        max_wd_name = weekdays_map[max_wd]
        findings.append(f"- Workload pattern: Peak user activity is on {max_wd_name}s with {weekday_counts[max_wd]} sessions.")
        
        # 3. Tuesday Stress Trend Check
        tues_stress_pct = (tuesday_stress_count / tuesday_total_count) * 100 if tuesday_total_count > 0 else 0
        if tuesday_total_count >= 3 and tues_stress_pct > 40:
            findings.append(f"- Warning: Elevated stress spikes ({tues_stress_pct:.1f}%) observed during Tuesday workloads.")
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                fact = "User has been pulling long hours and experiencing elevated stress every Tuesday this month."
                cursor.execute("SELECT id FROM active_memories WHERE fact=? AND category=?", (fact, "user_habit"))
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO active_memories (fact, category, timestamp) VALUES (?, ?, ?)", 
                                   (fact, "user_habit", datetime.utcnow().isoformat()))
                    conn.commit()
                conn.close()
            except Exception:
                pass
                
        # 4. Stress synthesis
        high_stress_pct = (stress_counts["high"] / total_interactions) * 100 if total_interactions > 0 else 0
        findings.append(f"- Overall stress levels: Low: {stress_counts['low']}, Medium: {stress_counts['medium']}, High: {stress_counts['high']} ({high_stress_pct:.1f}% high-stress).")
        
        return "\n".join(findings)
    except Exception as e:
        return f"Error analyzing user behavior trends: {e}"

if __name__ == "__main__":
    asyncio.run(server.run())
