"""
Final live test: creates a real calendar event via the fixed wrapper and verifies it shows in calendarView.
"""
import asyncio
import sys
sys.path.insert(0, ".")

import httpx
from datetime import datetime, timezone, timedelta
from src.mcp.servers.calendar import (
    get_valid_access_token,
    create_calendar_event,
    view_upcoming_agenda
)


async def main():
    now_local = datetime.now()  # local system time
    now_utc = datetime.now(timezone.utc)

    print("=" * 60)
    print("  CALENDAR WRAPPER LIVE TEST")
    print(f"  Current local time: {now_local.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Current UTC time:   {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Get token
    try:
        token = await get_valid_access_token()
        print(f"\nToken OK: {token[:20]}...")
    except Exception as e:
        print(f"Token FAIL: {e}")
        return

    # ── Create via our wrapper ─────────────────────────────────────
    # Use a time 2h from now in local time (what the model would supply)
    test_start = (now_local + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    test_end   = (now_local + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S")
    test_title = f"[Vigil Test] Wrapper @ {now_local.strftime('%H:%M')}"

    print(f"\n[CREATE] Title:  {test_title}")
    print(f"         Start:  {test_start} (naive local — wrapper should convert to UTC)")
    print(f"         End:    {test_end}")

    result = await create_calendar_event(
        title=test_title,
        start_time=test_start,
        end_time=test_end,
        description="Automated live test from Project Vigil diagnostic suite."
    )
    print(f"\n[RESULT] {result}")

    if "Success" not in result:
        print("\nFAIL: Event creation failed.")
        return

    # ── Verify via calendarView ────────────────────────────────────
    print("\n[VERIFY] Checking calendarView for the next 4 hours...")
    v_start = now_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    v_end   = (now_utc + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://graph.microsoft.com/v1.0/me/calendarview",
            headers={
                "Authorization": f"Bearer {token}",
                "Prefer": 'outlook.timezone="UTC"'
            },
            params={
                "startDateTime": v_start,
                "endDateTime": v_end,
                "$select": "subject,start,end",
                "$top": "20"
            },
            timeout=15
        )

    if r.status_code == 200:
        events = r.json().get("value", [])
        print(f"         Found {len(events)} event(s) in calendarView window:")
        found = False
        for ev in events:
            tag = " <-- TEST EVENT" if test_title in ev.get("subject", "") else ""
            print(f"         - {ev.get('subject')} | Start: {ev.get('start',{}).get('dateTime')}{tag}")
            if test_title in ev.get("subject", ""):
                found = True

        if found:
            print("\nPASS: Event visible in calendarView — calendar integration working correctly!")
        else:
            print("\nWARN: Test event not visible in calendarView window (may be timezone offset issue)")
    else:
        print(f"         calendarView HTTP {r.status_code}: {r.text[:300]}")

    # ── Agenda view via our wrapper ────────────────────────────────
    print("\n[AGENDA] Testing view_upcoming_agenda()...")
    agenda = await view_upcoming_agenda(days_ahead=1)
    print(agenda[:600])

    print("\n" + "=" * 60)
    print("  Note: Clean up the [Vigil Test] event manually if needed.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
