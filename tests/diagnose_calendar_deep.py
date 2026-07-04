"""
Deep diagnostic - decodes the JWT to inspect actual scopes,
then tests calendar endpoints directly without /me to isolate the issue.
"""
import asyncio
import sys
import json
import base64
sys.path.insert(0, ".")

import httpx
from datetime import datetime, timezone, timedelta
from src.mcp.servers.calendar import get_valid_access_token, db_get_config


def decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload (no verification needed, just inspection)."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        # Pad base64
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        decoded = base64.b64decode(payload_b64)
        return json.loads(decoded)
    except Exception as e:
        return {"decode_error": str(e)}


async def main():
    print("=" * 60)
    print("  M365 DEEP TOKEN + CALENDAR DIAGNOSTIC")
    print("=" * 60)

    # ── 1. Get token ──────────────────────────────────────────────
    print("\n[1] Getting access token...")
    try:
        token = await get_valid_access_token()
        print(f"    OK  Token obtained ({token[:20]}...)")
    except Exception as e:
        print(f"    FAIL  {e}")
        return

    # ── 2. Decode JWT to inspect actual granted scopes ───────────
    print("\n[2] Decoding JWT payload to check granted scopes...")
    payload = decode_jwt_payload(token)
    print(f"    iss (issuer):     {payload.get('iss', 'N/A')}")
    print(f"    app_displayname:  {payload.get('app_displayname', 'N/A')}")
    print(f"    oid:              {payload.get('oid', 'N/A')}")
    print(f"    upn:              {payload.get('upn', payload.get('unique_name', 'N/A'))}")
    print(f"    tid (tenant):     {payload.get('tid', 'N/A')}")
    
    scp = payload.get("scp", "")
    roles = payload.get("roles", [])
    print(f"    scp (delegate):   {scp!r}")
    print(f"    roles (app):      {roles}")
    
    exp = payload.get("exp", 0)
    exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None
    print(f"    exp:              {exp_dt}")

    has_calendar = "Calendars.ReadWrite" in scp or "Calendars.ReadWrite" in roles
    has_user_read = "User.Read" in scp or "profile" in scp or "openid" in scp
    print(f"\n    Calendars.ReadWrite present: {has_calendar}")
    print(f"    User.Read / profile present: {has_user_read}")

    if not has_calendar:
        print("\n    !! ISSUE FOUND: Calendars.ReadWrite NOT in token scopes !!")
        print("       The OAuth flow did not request or was not granted calendar access.")
        print("       The user must re-authorise with the correct scopes.")

    if not has_user_read:
        print("\n    !! ISSUE FOUND: No User.Read in token - explains /me 403 !!")

    # ── 3. Try calendar directly (even without User.Read) ────────
    print("\n[3] Attempting direct /me/calendars call (may need User.Read)...")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://graph.microsoft.com/v1.0/me/calendars",
            headers={"Authorization": f"Bearer {token}"},
            params={"$select": "id,name,canEdit,isDefaultCalendar"},
            timeout=15
        )
    print(f"    HTTP {r.status_code}")
    if r.status_code == 200:
        cals = r.json().get("value", [])
        for cal in cals:
            marker = " <-- DEFAULT" if cal.get("isDefaultCalendar") else ""
            print(f"    [{('RW' if cal.get('canEdit') else 'RO')}] {cal['name']}{marker}")
            print(f"         ID: {cal['id']}")
    else:
        print(f"    {r.text[:400]}")

    # ── 4. Try creating event directly ────────────────────────────
    print("\n[4] Attempting direct event creation on /me/events...")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload_evt = {
        "subject": f"[Vigil Deep Test] {now.strftime('%H:%M:%S')}",
        "body": {"contentType": "text", "content": "Deep diagnostic test"},
        "start": {"dateTime": (now + timedelta(hours=1)).isoformat(), "timeZone": "UTC"},
        "end":   {"dateTime": (now + timedelta(hours=2)).isoformat(), "timeZone": "UTC"},
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://graph.microsoft.com/v1.0/me/events",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload_evt,
            timeout=15
        )
    print(f"    HTTP {r.status_code}")
    if r.status_code == 201:
        created = r.json()
        ev_id = created.get("id", "")
        print(f"    OK  Event created: {created.get('subject')}")
        print(f"        Start:    {created.get('start', {}).get('dateTime')}")
        print(f"        Timezone: {created.get('start', {}).get('timeZone')}")
        print(f"        WebLink:  {created.get('webLink', '')}")
        # Clean up
        async with httpx.AsyncClient() as client:
            client.delete(f"https://graph.microsoft.com/v1.0/me/events/{ev_id}",
                         headers={"Authorization": f"Bearer {token}"})
        print("    OK  Cleaned up test event.")
    else:
        err = r.json() if "application/json" in r.headers.get("content-type","") else r.text
        print(f"    FAIL: {json.dumps(err, indent=2)[:600]}")

    # ── 5. Check what scopes the app registration has ────────────
    print("\n[5] Summary / Recommended Actions:")
    print(f"    Token scopes (scp): {scp!r}")
    if not has_calendar:
        print("\n    ACTION REQUIRED: Re-authorise M365 with full scope:")
        print("       https://graph.microsoft.com/Calendars.ReadWrite User.Read offline_access")
        print("    The current token is missing Calendars.ReadWrite. This is why events")
        print("    may appear to succeed (if a fallback basic scope allows it) but not")
        print("    show in the real calendar.")
    else:
        print("    Calendars.ReadWrite is present - issue may be timezone or calendar selection.")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
