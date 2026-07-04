"""
Live diagnostic test for Microsoft Graph Calendar API.
Tests token refresh, event creation, and lists to find the root cause
of events appearing created but not showing in Outlook.
"""
import asyncio
import sys
import json
sys.path.insert(0, ".")

import httpx
from datetime import datetime, timezone, timedelta
from src.mcp.servers.calendar import (
    get_valid_access_token, db_get_config,
    create_calendar_event, view_upcoming_agenda
)


async def _graph_get(token: str, path: str, params: dict = None) -> dict:
    url = f"https://graph.microsoft.com/v1.0{path}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=15)
    return r.status_code, r.json()


async def _graph_post(token: str, path: str, payload: dict) -> dict:
    url = f"https://graph.microsoft.com/v1.0{path}"
    async with httpx.AsyncClient() as client:
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=15
        )
    return r.status_code, r.text, r.json() if r.headers.get("content-type", "").startswith("application/json") else {}


async def main():
    print("=" * 60)
    print("  M365 CALENDAR LIVE DIAGNOSTIC")
    print("=" * 60)

    # ── 1. Token check ────────────────────────────────────────────
    print("\n[1] Checking / refreshing access token...")
    try:
        token = await get_valid_access_token()
        print(f"    OK  Token obtained ({token[:20]}...)")
    except Exception as e:
        print(f"    FAIL  {e}")
        return

    # ── 2. Who am I? ─────────────────────────────────────────────
    print("\n[2] Verifying identity (/me)...")
    status, me = await _graph_get(token, "/me", {"$select": "displayName,mail,userPrincipalName"})
    if status == 200:
        print(f"    OK  Signed in as: {me.get('displayName')} <{me.get('mail') or me.get('userPrincipalName')}>")
    else:
        print(f"    FAIL  HTTP {status}: {me}")
        return

    # ── 3. List available calendars ───────────────────────────────
    print("\n[3] Listing available Outlook calendars...")
    status, cals = await _graph_get(token, "/me/calendars", {"$select": "id,name,canEdit,isDefaultCalendar"})
    if status != 200:
        print(f"    FAIL  HTTP {status}: {cals}")
        return
    default_cal_id = None
    for cal in cals.get("value", []):
        marker = " <-- DEFAULT" if cal.get("isDefaultCalendar") else ""
        editable = "RW" if cal.get("canEdit") else "RO"
        print(f"    [{editable}] {cal['name']} (ID: {cal['id'][:20]}...){marker}")
        if cal.get("isDefaultCalendar"):
            default_cal_id = cal["id"]

    if not default_cal_id:
        print("    WARN  No default calendar found — events may be going to a non-default calendar!")

    # ── 4. Create a test event via raw Graph API (bypass our wrapper) ──
    print("\n[4] Creating test event directly via Graph API...")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    test_start = (now + timedelta(hours=1)).isoformat()
    test_end   = (now + timedelta(hours=2)).isoformat()
    test_title = f"[Vigil Diagnostic] Test Event {now.strftime('%H:%M')}"

    raw_payload = {
        "subject": test_title,
        "body": {"contentType": "text", "content": "Auto-created by Project Vigil diagnostic script."},
        "start": {"dateTime": test_start, "timeZone": "UTC"},
        "end":   {"dateTime": test_end,   "timeZone": "UTC"},
    }
    print(f"    Payload: start={test_start}  end={test_end}")

    # Try default calendar explicitly
    if default_cal_id:
        cal_path = f"/me/calendars/{default_cal_id}/events"
    else:
        cal_path = "/me/events"

    status, raw_text, resp_json = await _graph_post(token, cal_path, raw_payload)
    print(f"    HTTP {status}")
    if status == 201:
        created_id = resp_json.get("id", "N/A")
        created_start = resp_json.get("start", {}).get("dateTime", "N/A")
        created_tz    = resp_json.get("start", {}).get("timeZone", "N/A")
        web_link      = resp_json.get("webLink", "N/A")
        print(f"    OK  Event created!")
        print(f"        ID:         {created_id[:30]}...")
        print(f"        Start:      {created_start}  (TZ: {created_tz})")
        print(f"        Web link:   {web_link}")

        # ── 5. Verify it appears in calendarView ─────────────────────
        print("\n[5] Verifying event appears in calendarView query...")
        v_start = now.isoformat()
        v_end   = (now + timedelta(hours=4)).isoformat()
        status2, view_json = await _graph_get(token, "/me/calendarview", {
            "startDateTime": v_start,
            "endDateTime": v_end,
            "$select": "subject,start,end",
            "$top": "10"
        })
        found = False
        for ev in view_json.get("value", []):
            if test_title in ev.get("subject", ""):
                print(f"    OK  Event visible in calendarView: {ev['subject']}")
                found = True
        if not found:
            print(f"    WARN  Event NOT found in calendarView (HTTP {status2})")
            print(f"          This could mean it went to a different calendar or timezone mismatch.")
            print(f"          calendarView response: {json.dumps(view_json, indent=2)[:600]}")

        # ── 6. Delete the test event to keep the calendar clean ───────
        print(f"\n[6] Cleaning up: deleting test event {created_id[:20]}...")
        del_url = f"https://graph.microsoft.com/v1.0/me/events/{created_id}"
        async with httpx.AsyncClient() as client:
            del_r = await client.delete(del_url, headers={"Authorization": f"Bearer {token}"})
        if del_r.status_code == 204:
            print("    OK  Test event deleted.")
        else:
            print(f"    WARN  Delete returned HTTP {del_r.status_code}: {del_r.text[:100]}")

    else:
        print(f"    FAIL  Response body:\n{raw_text[:800]}")
        print("\n    Common causes:")
        print("    - Insufficient Graph API permission (need Calendars.ReadWrite)")
        print("    - App registration missing delegate vs application permission")
        print("    - start/end datetime format rejected by Graph")

    # ── 7. Test our wrapper function too ─────────────────────────
    print("\n[7] Testing create_calendar_event() wrapper (the function models call)...")
    wrapper_start = (now + timedelta(hours=3)).isoformat().replace("+00:00", "")
    wrapper_end   = (now + timedelta(hours=4)).isoformat().replace("+00:00", "")
    wrapper_result = await create_calendar_event(
        title="[Vigil Wrapper Test]",
        start_time=wrapper_start,
        end_time=wrapper_end,
        description="Test from wrapper"
    )
    print(f"    Result: {wrapper_result}")

    print("\n" + "=" * 60)
    print("  DIAGNOSTIC COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
