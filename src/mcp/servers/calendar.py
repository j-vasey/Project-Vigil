import os
import sqlite3
import asyncio
import httpx
from datetime import datetime, timedelta
from src.mcp.servers.base import MCPServer

server = MCPServer("server-m365-calendar")

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
        if row and row[0]:
            return row[0]
    except Exception:
        pass
        
    # Provide default fallback credentials
    if key == "m365_client_id":
        return "40e854ab-e28d-4f10-9c16-95ffc06cb4e5"
    if key == "m365_tenant_id":
        return "common"
    if key == "m365_client_secret":
        return "m365-calendar-app-secret"
        
    return default

def db_set_config(key: str, value: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO configurations (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

async def get_valid_access_token() -> str:
    """Retrieves access token, refreshing it if expired."""
    tenant_id = db_get_config("m365_tenant_id", "common")
    client_id = db_get_config("m365_client_id", "")
    access_token = db_get_config("m365_access_token", "")
    refresh_token = db_get_config("m365_refresh_token", "")
    expiry_str = db_get_config("m365_token_expiry", "")
    
    if not client_id:
        raise ValueError("Microsoft M365 Calendar is not configured: m365_client_id is empty.")
    if not access_token:
        raise ValueError("Microsoft M365 Calendar is not authorized. Please link your account in the Web UI.")
        
    is_expired = True
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str)
            # Add a 60-second buffer
            if datetime.utcnow() < (expiry - timedelta(seconds=60)):
                is_expired = False
        except Exception:
            pass
            
    if not is_expired:
        return access_token
        
    if not refresh_token:
        raise ValueError("M365 Access token expired and no refresh token available. Re-authorization required.")
        
    # Refresh the token
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    client_secret = db_get_config("m365_client_secret", "")
    payload = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "https://graph.microsoft.com/Calendars.ReadWrite offline_access"
    }
    if client_secret:
        payload["client_secret"] = client_secret
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=payload)
        if response.status_code != 200:
            raise RuntimeError(f"Token refresh failed: {response.text}")
            
        data = response.json()
        new_access = data["access_token"]
        new_refresh = data.get("refresh_token", refresh_token)
        expires_in = data["expires_in"]
        new_expiry = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()
        
        db_set_config("m365_access_token", new_access)
        db_set_config("m365_refresh_token", new_refresh)
        db_set_config("m365_token_expiry", new_expiry)
        
        return new_access

@server.register_tool(
    name="view_upcoming_agenda",
    description="Retrieve upcoming calendar agenda events for Microsoft Outlook.",
    input_schema={
        "type": "object",
        "properties": {
            "days_ahead": {"type": "integer", "description": "Number of days ahead to search (default 7)."}
        }
    }
)
async def view_upcoming_agenda(days_ahead: int = 7) -> str:
    try:
        token = await get_valid_access_token()
    except Exception as e:
        return str(e)
        
    start_time = datetime.utcnow().isoformat() + "Z"
    end_time = (datetime.utcnow() + timedelta(days=days_ahead)).isoformat() + "Z"
    
    url = "https://graph.microsoft.com/v1.0/me/calendarview"
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": 'outlook.timezone="UTC"'
    }
    params = {
        "startDateTime": start_time,
        "endDateTime": end_time,
        "$select": "id,subject,start,end,location,bodyPreview",
        "$orderby": "start/dateTime",
        "$top": "20"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        if response.status_code != 200:
            return f"M365 Calendar API error: {response.text}"
            
        events = response.json().get("value", [])
        if not events:
            return "No upcoming calendar events found."
            
        summary = []
        for e in events:
            event_id = e.get("id", "N/A")
            subj = e.get("subject", "No Title")
            start = e.get("start", {}).get("dateTime", "")
            end = e.get("end", {}).get("dateTime", "")
            loc = e.get("location", {}).get("displayName", "N/A")
            desc = e.get("bodyPreview", "")
            summary.append(
                f"- **{subj}** (ID: {event_id})\n"
                f"  Start: {start}\n"
                f"  End: {end}\n"
                f"  Location: {loc}\n"
                f"  Details: {desc}\n"
            )
        return "\n".join(summary)

@server.register_tool(
    name="create_calendar_event",
    description="Create a new calendar entry event in the Microsoft Outlook calendar.",
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Subject/Title of the event."},
            "start_time": {"type": "string", "description": "ISO 8601 start time (e.g. '2026-06-29T18:00:00')."},
            "end_time": {"type": "string", "description": "ISO 8601 end time (e.g. '2026-06-29T19:00:00')."},
            "description": {"type": "string", "description": "Body summary description details."}
        },
        "required": ["title", "start_time", "end_time"]
    }
)
async def create_calendar_event(title: str, start_time: str, end_time: str, description: str = "") -> str:
    try:
        token = await get_valid_access_token()
    except Exception as e:
        return str(e)
        
    url = "https://graph.microsoft.com/v1.0/me/events"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # Ensure times are correctly formatted for Outlook API (Outlook expects timezone defined)
    payload = {
        "subject": title,
        "body": {
            "contentType": "HTML",
            "content": description
        },
        "start": {
            "dateTime": start_time,
            "timeZone": "UTC"
        },
        "end": {
            "dateTime": end_time,
            "timeZone": "UTC"
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code == 201:
            return f"Success: Calendar event '{title}' created successfully."
        return f"Failed to create event: {response.text}"

@server.register_tool(
    name="modify_calendar_event",
    description="Modify/Update details of an existing calendar event using its unique event ID.",
    input_schema={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "The unique Outlook event ID of the calendar entry to update."},
            "title": {"type": "string", "description": "Optional. New Subject/Title of the event."},
            "start_time": {"type": "string", "description": "Optional. New ISO 8601 start time (e.g. '2026-06-29T18:00:00')."},
            "end_time": {"type": "string", "description": "Optional. New ISO 8601 end time (e.g. '2026-06-29T19:00:00')."},
            "description": {"type": "string", "description": "Optional. New body summary description details."}
        },
        "required": ["event_id"]
    }
)
async def modify_calendar_event(
    event_id: str,
    title: str = None,
    start_time: str = None,
    end_time: str = None,
    description: str = None
) -> str:
    try:
        token = await get_valid_access_token()
    except Exception as e:
        return str(e)
        
    url = f"https://graph.microsoft.com/v1.0/me/events/{event_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    payload = {}
    if title:
        payload["subject"] = title
    if description is not None:
        payload["body"] = {
            "contentType": "HTML",
            "content": description
        }
    if start_time:
        payload["start"] = {
            "dateTime": start_time,
            "timeZone": "UTC"
        }
    if end_time:
        payload["end"] = {
            "dateTime": end_time,
            "timeZone": "UTC"
        }
        
    if not payload:
        return "No parameters provided to update."
        
    async with httpx.AsyncClient() as client:
        response = await client.patch(url, headers=headers, json=payload)
        if response.status_code == 200:
            return f"Success: Calendar event '{event_id}' modified successfully."
        return f"Failed to modify event: {response.text}"

@server.register_tool(
    name="delete_calendar_event",
    description="Remove/Delete an existing calendar event entry from the Outlook calendar using its unique event ID.",
    input_schema={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "The unique Outlook event ID of the calendar entry to delete."}
        },
        "required": ["event_id"]
    }
)
async def delete_calendar_event(event_id: str) -> str:
    try:
        token = await get_valid_access_token()
    except Exception as e:
        return str(e)
        
    url = f"https://graph.microsoft.com/v1.0/me/events/{event_id}"
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.delete(url, headers=headers)
        if response.status_code == 204:
            return f"Success: Calendar event '{event_id}' deleted successfully."
        return f"Failed to delete event: {response.text}"

@server.register_tool(
    name="create_recurring_calendar_event",
    description="Create a recurring calendar event series in Microsoft Outlook with a simplified recurrence pattern.",
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Subject/Title of the event series."},
            "start_time_iso": {"type": "string", "description": "ISO 8601 start date and time of the first occurrence (e.g. '2026-06-29T18:00:00')."},
            "duration_minutes": {"type": "integer", "description": "Duration of each occurrence in minutes."},
            "frequency": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "The cycle frequency of recurrence."},
            "interval": {"type": "integer", "description": "The interval between cycles (e.g. every '1' week, every '2' months)."},
            "days_of_week": {
                "type": "array",
                "items": {"type": "string", "enum": ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]},
                "description": "Optional. The days of the week when the event occurs (useful for weekly frequency)."
            },
            "occurrences": {"type": "integer", "description": "Optional. Number of occurrences for the series. Specify either occurrences or end_date_iso."},
            "end_date_iso": {"type": "string", "description": "Optional. ISO 8601 end date of the series (YYYY-MM-DD or ISO string). Specify either occurrences or end_date_iso."},
            "description": {"type": "string", "description": "Optional. Body description details."}
        },
        "required": ["title", "start_time_iso", "duration_minutes", "frequency", "interval"]
    }
)
async def create_recurring_calendar_event(
    title: str,
    start_time_iso: str,
    duration_minutes: int,
    frequency: str,
    interval: int,
    days_of_week: list = None,
    occurrences: int = None,
    end_date_iso: str = None,
    description: str = ""
) -> str:
    try:
        token = await get_valid_access_token()
    except Exception as e:
        return str(e)

    # 1. Parse start time and calculate end time of the first instance
    clean_start = start_time_iso.replace("Z", "")
    try:
        start_dt = datetime.fromisoformat(clean_start)
    except Exception as e:
        return f"Invalid start_time_iso format: {e}"
        
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    end_time_iso = end_dt.isoformat()

    # 2. Build the basic Microsoft Graph payload
    payload = {
        "subject": title,
        "body": {
            "contentType": "HTML",
            "content": description
        },
        "start": {
            "dateTime": clean_start,
            "timeZone": "UTC"
        },
        "end": {
            "dateTime": end_time_iso,
            "timeZone": "UTC"
        },
        "recurrence": {
            "pattern": {
                "interval": interval
            },
            "range": {
                "startDate": clean_start.split("T")[0],
                "recurrenceTimeZone": "UTC"
            }
        }
    }

    # 3. Map pattern type based on simplified frequency
    p_type = frequency.lower()
    if p_type == "daily":
        payload["recurrence"]["pattern"]["type"] = "daily"
    elif p_type == "weekly":
        payload["recurrence"]["pattern"]["type"] = "weekly"
        days = [d.capitalize() for d in (days_of_week or [])]
        if not days:
            days = [start_dt.strftime("%A")]
        payload["recurrence"]["pattern"]["daysOfWeek"] = days
    elif p_type == "monthly":
        payload["recurrence"]["pattern"]["type"] = "absoluteMonthly"
        payload["recurrence"]["pattern"]["dayOfMonth"] = start_dt.day
    else:
        return f"Unsupported frequency: {frequency}"

    # 4. Map range type
    if occurrences is not None:
        payload["recurrence"]["range"]["type"] = "numbered"
        payload["recurrence"]["range"]["numberOfOccurrences"] = occurrences
    elif end_date_iso:
        payload["recurrence"]["range"]["type"] = "endDate"
        payload["recurrence"]["range"]["endDate"] = end_date_iso.replace("Z", "").split("T")[0]
    else:
        payload["recurrence"]["range"]["type"] = "noEnd"

    url = "https://graph.microsoft.com/v1.0/me/events"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code == 201:
            return f"Success: Recurring calendar event series '{title}' created successfully."
        return f"Failed to create recurring event: {response.text}"


@server.register_tool(
    name="modify_calendar_series",
    description="Modify or delete a calendar event. Supports applying changes to a single instance or the entire recurring series.",
    input_schema={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "The unique Outlook event ID of the calendar entry."},
            "apply_to_series": {"type": "boolean", "description": "If True, applies action to the entire recurring series (master). If False, applies only to this single occurrence."},
            "action": {"type": "string", "enum": ["update", "delete"], "description": "The action to perform: 'update' to modify details, or 'delete' to remove/cancel the event."},
            "title": {"type": "string", "description": "Optional. New Subject/Title (for update action)."},
            "start_time": {"type": "string", "description": "Optional. New ISO 8601 start time (for update action)."},
            "end_time": {"type": "string", "description": "Optional. New ISO 8601 end time (for update action)."},
            "description": {"type": "string", "description": "Optional. New body summary description details (for update action)."}
        },
        "required": ["event_id", "apply_to_series", "action"]
    }
)
async def modify_calendar_series(
    event_id: str,
    apply_to_series: bool,
    action: str,
    title: str = None,
    start_time: str = None,
    end_time: str = None,
    description: str = None
) -> str:
    try:
        token = await get_valid_access_token()
    except Exception as e:
        return str(e)

    # 1. Resolve event_id to series master ID if apply_to_series is True
    target_id = event_id
    if apply_to_series:
        url = f"https://graph.microsoft.com/v1.0/me/events/{event_id}"
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                master_id = data.get("seriesMasterId")
                if master_id:
                    target_id = master_id

    # 2. Execute Action
    url = f"https://graph.microsoft.com/v1.0/me/events/{target_id}"
    headers = {
        "Authorization": f"Bearer {token}"
    }

    if action.lower() == "delete":
        async with httpx.AsyncClient() as client:
            response = await client.delete(url, headers=headers)
            if response.status_code == 204:
                return f"Success: Calendar series/event '{event_id}' deleted successfully."
            return f"Failed to delete series/event: {response.text}"

    elif action.lower() == "update":
        payload = {}
        if title:
            payload["subject"] = title
        if description is not None:
            payload["body"] = {
                "contentType": "HTML",
                "content": description
            }
        if start_time:
            payload["start"] = {
                "dateTime": start_time,
                "timeZone": "UTC"
            }
        if end_time:
            payload["end"] = {
                "dateTime": end_time,
                "timeZone": "UTC"
            }

        if not payload:
            return "No parameters provided to update."

        headers["Content-Type"] = "application/json"
        async with httpx.AsyncClient() as client:
            response = await client.patch(url, headers=headers, json=payload)
            if response.status_code == 200:
                return f"Success: Calendar series/event '{event_id}' modified successfully."
            return f"Failed to modify series/event: {response.text}"

    else:
        return f"Unsupported action: {action}"


if __name__ == "__main__":
    asyncio.run(server.run())
