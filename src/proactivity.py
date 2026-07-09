import asyncio
import logging
import random
import re
import os
import json
from datetime import datetime, time, timezone, timedelta
from typing import Optional

from src.database import SessionLocal
from src.repository import MessageRepository
from src.router import MessagingRouter
from src.llm import get_llm_client

logger = logging.getLogger("project_vigil.proactivity")

# ---------------------------------------------------------------------------
# Lifestyle context aggregator
# ---------------------------------------------------------------------------

async def extract_lifestyle_context() -> str:
    """
    Aggregates personal metrics: M365 calendar, local goals.json, and todo.json.
    """
    # 1. Fetch Calendar from M365 Calendar MCP server
    calendar_text = "No calendar events."
    try:
        from src.mcp.servers.calendar import view_upcoming_agenda
        calendar_text = await view_upcoming_agenda(days_ahead=3)
    except Exception as e:
        logger.warning(f"[Proactivity] Failed to fetch calendar agenda: {e}")

    # 2. Fetch Goals from goals.json (if present)
    goals_text = "No goals configured."
    goals_path = "goals.json"
    if os.path.exists(goals_path):
        try:
            with open(goals_path, "r", encoding="utf-8") as f:
                goals_data = json.load(f)
                goals_text = json.dumps(goals_data, indent=2)
        except Exception as e:
            logger.warning(f"[Proactivity] Failed to read goals.json: {e}")

    # 3. Fetch Tasks from todo.json (if present)
    todo_text = "No pending tasks."
    todo_path = "todo.json"
    if os.path.exists(todo_path):
        try:
            with open(todo_path, "r", encoding="utf-8") as f:
                todo_data = json.load(f)
                todo_text = json.dumps(todo_data, indent=2)
        except Exception as e:
            logger.warning(f"[Proactivity] Failed to read todo.json: {e}")

    return (
        "=== LIFESTYLE COORDINATOR CONTEXT ===\n"
        f"[Upcoming Calendar Events]:\n{calendar_text}\n\n"
        f"[Financial & Life Goals]:\n{goals_text}\n\n"
        f"[Pending To-Do Tasks]:\n{todo_text}\n"
        "====================================="
    )

# ---------------------------------------------------------------------------
# DND helpers
# ---------------------------------------------------------------------------

def is_time_in_range(start: time, end: time, current: time) -> bool:
    """Checks if current time is inside [start, end], handling midnight wraparound."""
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def is_dnd_active(dnd_start_str: str, dnd_end_str: str) -> bool:
    """Returns True if the current local time falls inside the DND window."""
    try:
        sh, sm = map(int, dnd_start_str.split(":"))
        eh, em = map(int, dnd_end_str.split(":"))
        return is_time_in_range(time(sh, sm), time(eh, em), datetime.now().time())
    except Exception as e:
        logger.error(f"[Proactivity] Failed parsing DND config '{dnd_start_str}'-'{dnd_end_str}': {e}")
        return False

# ---------------------------------------------------------------------------
# Proactive outreach
# ---------------------------------------------------------------------------

async def trigger_proactive_outreach(reason_code: str, router: MessagingRouter) -> Optional[str]:
    """
    Triggers an outbound proactive AI message:
    1. Validates system status and DND window.
    2. Gathers lifestyle context and recent history.
    3. Generates a response with the LLM (supports [SEARCH:] and [IMAGE:] tags).
    4. Dispatches via the router and writes to audit logs.
    """
    db = SessionLocal()
    try:
        repo = MessageRepository(db)

        # Resolve target platform + user
        platform = repo.get_config("proactive_platform", "mock")
        if platform == "telegram":
            user_id = repo.get_config("telegram_user_id") or repo.get_config("proactive_user_id", "mock_user")
        elif platform == "discord":
            user_id = repo.get_config("discord_user_id") or repo.get_config("proactive_user_id", "mock_user")
        else:
            user_id = repo.get_config("proactive_user_id", "mock_user")

        # 1. System health gate
        if repo.get_config("system_health", "healthy") == "paused":
            logger.info("[Proactivity] Skipped: system paused.")
            repo.log_proactivity(reason_code=f"{reason_code}_SKIPPED_PAUSED")
            return None

        # 1.5 Temporary proactivity pause
        paused_until_str = repo.get_config("proactivity_paused_until", "")
        if paused_until_str:
            try:
                paused_until = datetime.fromisoformat(paused_until_str)
                if datetime.utcnow() < paused_until:
                    logger.info(f"[Proactivity] Skipped: paused until {paused_until_str}.")
                    repo.log_proactivity(reason_code=f"{reason_code}_SKIPPED_TEMP_PAUSE")
                    return None
            except Exception as e:
                logger.error(f"[Proactivity] Bad paused_until value '{paused_until_str}': {e}")

        # 2. DND gate
        dnd_start = repo.get_config("dnd_start", "22:00")
        dnd_end = repo.get_config("dnd_end", "08:00")
        if is_dnd_active(dnd_start, dnd_end):
            logger.info(f"[Proactivity] Blocked by DND ({dnd_start}–{dnd_end}).")
            repo.log_proactivity(reason_code=f"{reason_code}_SKIPPED_DND")
            return None

        # 2.5 Dedup cooldown: prevent firing the same reason_code twice within 2 hours
        if reason_code in ("morning_brief", "evening_summary", "autonomous_check_in"):
            try:
                two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
                recent_logs = repo.get_recent_proactivity_logs(limit=20)
                for log in recent_logs:
                    if log.reason_code == reason_code:
                        log_time = log.execution_time
                        # Make aware if naive
                        if log_time.tzinfo is None:
                            log_time = log_time.replace(tzinfo=timezone.utc)
                        if log_time > two_hours_ago:
                            logger.info(f"[Proactivity] Dedup gate: '{reason_code}' already fired within 2h. Skipping.")
                            return None
            except Exception as dedup_err:
                logger.warning(f"[Proactivity] Dedup check failed (non-blocking): {dedup_err}")

        logger.info(f"[Proactivity] Initiating '{reason_code}' outreach → {user_id} via {platform}...")

        # 3. Context gathering
        history = repo.get_sliding_window_history(channel=platform, user_id=user_id, limit=5)
        history_str = "\n".join(
            [f"{'User' if h.sender_type == 'user' else 'Companion'}: {h.text}" for h in history]
        )

        habit_memories = [m for m in repo.search_memories("") if m.category == "user_habit"]
        focus_habit = habit_memories[0].fact if habit_memories else ""
        lifestyle_context = await extract_lifestyle_context()

        if focus_habit:
            focus = (
                f"Discovered negative behavioral trend: '{focus_habit}'. "
                "Empathetically initiate outreach and offer to check their schedule."
            )
        elif reason_code == "morning_brief":
            focus = (
                "It is morning (08:00). Review the user's day. Flag upcoming events, "
                "offer commute buffer tips, and provide encouraging reinforcement if goals are on track."
            )
        elif reason_code == "evening_summary":
            focus = (
                "It is evening (21:00). Ask how the day went. Summarise goal progress "
                "and pending tasks in a warm, supportive way."
            )
        else:
            focus = "Initiate a friendly, casual check-in."

        prompt = "System Context: Companion initiating outbound outreach.\n"
        if history_str:
            prompt += f"Recent Chat History:\n{history_str}\n\n"
        prompt += f"User Lifestyle Context:\n{lifestyle_context}\n\nOutreach Focus: {focus}\n"
        prompt += "Companion (outbound outreach message):"

        # 4. LLM call
        backend = repo.get_config("llm_backend", "mock")
        llm_url = repo.get_config("llm_url", "http://localhost:11434")
        model = repo.get_config("llm_model", "gemma:4")
        try:
            num_ctx = int(repo.get_config("llm_num_ctx", "8192"))
        except ValueError:
            num_ctx = 8192

        system_prompt = repo.get_config(
            "system_prompt",
            "You are a warm, helpful local AI companion named Project Vigil."
        )
        # Inject current date/time into system prompt so proactive messages use correct dates
        from src.orchestrator import _datetime_header
        system_prompt = _datetime_header() + system_prompt
        llm_client = get_llm_client(backend=backend, url=llm_url, model=model, num_ctx=num_ctx)
        generated_msg = await llm_client.generate_response(prompt=prompt, system_prompt=system_prompt)

        # 4a. Handle [SEARCH:] tag
        search_match = re.search(r"\[SEARCH:\s*(.*?)\]", generated_msg)
        if search_match:
            search_query = search_match.group(1).strip()
            logger.info(f"[Proactivity] [SEARCH:] tag detected: '{search_query}'")
            try:
                from src.tools.registry import tool_registry
                search_results = await tool_registry.execute("web_search", {"query": search_query})
            except Exception as se:
                search_results = f"Search failed: {se}"

            clean_first = re.sub(r"\[SEARCH:\s*(.*?)\]", "", generated_msg).strip()
            prompt_lines = prompt.rstrip().split("\n")
            if clean_first:
                prompt_lines.append(f"Companion: {clean_first}")
            prompt_lines.append(
                f"System: Web search results for '{search_query}':\n{search_results}\n"
                "Answer the user naturally using these facts."
            )
            prompt_lines.append("Companion (outbound outreach message):")
            generated_msg = await llm_client.generate_response(
                prompt="\n".join(prompt_lines), system_prompt=system_prompt
            )

        # 4b. Handle [IMAGE:] tag
        image_match = re.search(r"\[IMAGE:\s*(.*?)\]", generated_msg)
        if image_match:
            image_prompt_text = image_match.group(1).strip()
            logger.info(f"[Proactivity] [IMAGE:] tag detected: '{image_prompt_text}'")
            clean_text = re.sub(r"\[IMAGE:\s*(.*?)\]", "", generated_msg).strip()

            comfy_backend = repo.get_config("comfyui_backend", "mock")
            comfy_url = repo.get_config("comfyui_url", "http://localhost:8188")
            comfy_ckpt = repo.get_config("comfyui_ckpt", "v1-5-pruned-emaonly.safetensors")
            from src.comfyui import ComfyUIClient
            comfy = ComfyUIClient(base_url=comfy_url, backend=comfy_backend, ckpt_name=comfy_ckpt)
            img_bytes = await comfy.generate_image(image_prompt_text)

            repo.save_message(channel=platform, user_id=user_id, sender_type="bot",
                              text=f"[IMAGE: {image_prompt_text}] {clean_text}")
            if img_bytes:
                sent = await router.send_image(
                    platform=platform, user_id=user_id,
                    image_bytes=img_bytes, filename="vigil_outreach.png", caption=clean_text
                )
            else:
                logger.error("[Proactivity] Image bytes empty — falling back to text.")
                sent = await router.send_message(platform=platform, user_id=user_id, text=clean_text)
        else:
            # 5. Save + dispatch plain text
            repo.save_message(channel=platform, user_id=user_id, sender_type="bot", text=generated_msg)
            sent = await router.send_message(platform=platform, user_id=user_id, text=generated_msg)

        # 6. Audit log
        repo.log_proactivity(
            reason_code=reason_code,
            message_dispatched=generated_msg if sent else f"[ROUTING_FAILED] {generated_msg}"
        )
        return generated_msg

    except Exception as e:
        logger.exception(f"[Proactivity] Outreach failed: {e}")
        return None
    finally:
        db.close()

# ---------------------------------------------------------------------------
# Background scheduler loop
# ---------------------------------------------------------------------------

async def start_proactivity_engine(router: MessagingRouter) -> None:
    """
    Background schedule runner. Handles:
    - Morning Briefing at 08:xx
    - Evening Reflection at 21:xx
    - Pseudo-random interval check-ins with configurable probability + jitter
    """
    logger.info("[Proactivity] Starting Proactivity Engine loop...")
    while True:
        try:
            now = datetime.now()
            current_date_str = now.date().isoformat()

            db = SessionLocal()
            try:
                repo = MessageRepository(db)

                if now.hour == 8:
                    if repo.get_config("last_morning_brief_date", "") != current_date_str:
                        logger.info("[Proactivity] Triggering Morning Briefing (08:00)...")
                        repo.set_config("last_morning_brief_date", current_date_str)
                        db.commit()
                        db.close()
                        await trigger_proactive_outreach("morning_brief", router)
                        db = SessionLocal()
                        repo = MessageRepository(db)

                elif now.hour == 21:
                    if repo.get_config("last_evening_summary_date", "") != current_date_str:
                        logger.info("[Proactivity] Triggering Evening Reflection (21:00)...")
                        repo.set_config("last_evening_summary_date", current_date_str)
                        db.commit()
                        db.close()
                        await trigger_proactive_outreach("evening_summary", router)
                        db = SessionLocal()
                        repo = MessageRepository(db)

            except Exception as sched_err:
                logger.error(f"[Proactivity] Scheduling error: {sched_err}")
            finally:
                db.close()

            # Load interval + jitter settings
            db = SessionLocal()
            repo = MessageRepository(db)
            try:
                interval = int(repo.get_config("proactive_interval_seconds", "3600"))
                jitter_pct = float(repo.get_config("proactive_jitter_percentage", "0.30"))
            finally:
                db.close()

            max_jitter = interval * jitter_pct
            sleep_secs = max(1.0, interval + random.uniform(-max_jitter, max_jitter))
            logger.info(f"[Proactivity] Next check in {sleep_secs:.1f}s (base {interval}s ±{jitter_pct*100:.0f}%)")
            await asyncio.sleep(sleep_secs)

            # Probability roll for autonomous check-in
            db = SessionLocal()
            try:
                repo = MessageRepository(db)
                if repo.get_config("system_health", "healthy") == "paused":
                    logger.info("[Proactivity] Paused — skipping roll.")
                    continue

                probability = float(repo.get_config("proactive_probability", "0.25"))
                roll = random.random()
                if roll < probability:
                    logger.info(f"[Proactivity] Roll passed ({roll:.3f} < {probability}). Initiating check-in.")
                    db.close()
                    await trigger_proactive_outreach("autonomous_check_in", router)
                else:
                    logger.info(f"[Proactivity] Roll failed ({roll:.3f} >= {probability}). Skipping.")
                    repo.log_proactivity(reason_code="autonomous_check_in_SKIPPED_ROLL")
                    db.close()
            except Exception as inner_ex:
                logger.exception(f"[Proactivity] Roll check error: {inner_ex}")
                db.close()

        except asyncio.CancelledError:
            logger.info("[Proactivity] Engine shut down.")
            break
        except Exception as e:
            logger.exception(f"[Proactivity] Loop error: {e}")
            await asyncio.sleep(15)

# ---------------------------------------------------------------------------
# Reminder Engine loop
# ---------------------------------------------------------------------------

async def start_reminder_engine(router: MessagingRouter) -> None:
    """
    Background loop that polls the reminders table every 30 seconds and dispatches
    messages when their scheduled UTC time has passed.
    """
    logger.info("[Reminders] Starting Reminder Engine loop...")
    while True:
        try:
            db = SessionLocal()
            try:
                repo = MessageRepository(db)
                now_utc = datetime.now(timezone.utc)
                now_iso = now_utc.isoformat()
                
                # Use raw SQLite connection to manage reminders
                from src.database import DB_PATH
                import sqlite3
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                
                cursor.execute(
                    "SELECT id, message FROM reminders WHERE fired = 0 AND remind_at <= ?",
                    (now_iso,)
                )
                due_reminders = cursor.fetchall()
                
                for row in due_reminders:
                    rem_id = row[0]
                    message = row[1]
                    
                    # Mark as fired
                    cursor.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (rem_id,))
                    conn.commit()
                    
                    logger.info(f"[Reminders] Firing reminder #{rem_id}: '{message[:20]}...'")
                    
                    platform = repo.get_config("proactive_platform", "mock")
                    if platform == "telegram":
                        user_id = repo.get_config("telegram_user_id") or repo.get_config("proactive_user_id", "mock_user")
                    elif platform == "discord":
                        user_id = repo.get_config("discord_user_id") or repo.get_config("proactive_user_id", "mock_user")
                    else:
                        user_id = repo.get_config("proactive_user_id", "mock_user")
                        
                    outbound_msg = f"🔔 **Reminder**\n{message}"
                    repo.save_message(channel=platform, user_id=user_id, sender_type="bot", text=outbound_msg)
                    await router.send_message(platform=platform, user_id=user_id, text=outbound_msg)
                    
                conn.close()
                
            except Exception as sched_err:
                logger.error(f"[Reminders] Polling error: {sched_err}")
            finally:
                db.close()
                
            await asyncio.sleep(30)
            
        except asyncio.CancelledError:
            logger.info("[Reminders] Engine shut down.")
            break
        except Exception as e:
            logger.exception(f"[Reminders] Loop error: {e}")
            await asyncio.sleep(15)

# ---------------------------------------------------------------------------
# Proactive Memory Evaluator
# ---------------------------------------------------------------------------

async def start_memory_evaluator_engine(router: MessagingRouter):
    """
    Periodically evaluates recent screen memory logs to push proactive alerts.
    """
    logger.info("[Proactivity] Memory Evaluator engine started.")
    last_alert_time = None
    last_alert_text = None
    
    while True:
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            enabled = repo.get_config("proactivity_enabled", "True").lower() == "true"
            if not enabled:
                await asyncio.sleep(60)
                continue

            eval_interval = int(repo.get_config("memory_evaluator_interval", "300"))
            await asyncio.sleep(eval_interval)

            # 1. System health and DND gate
            if repo.get_config("system_health", "healthy") == "paused":
                continue

            dnd_start = repo.get_config("dnd_start", "22:00")
            dnd_end = repo.get_config("dnd_end", "08:00")
            if is_dnd_active(dnd_start, dnd_end):
                continue

            # 2. Fetch last 10 minutes of screen activity
            recent_screen = repo.get_recent_system_context(minutes=10)
            if not recent_screen:
                continue

            # Parse and deduplicate
            screen_lines = []
            last_activity = None
            for s_msg in recent_screen:
                try:
                    ctx = json.loads(s_msg.text)
                    activity = ctx.get("captured_activity", "")
                    if activity and activity != last_activity:
                        tstamp = s_msg.timestamp.strftime("%H:%M")
                        screen_lines.append(f"- [{tstamp}] {activity}")
                        last_activity = activity
                except Exception:
                    pass
            
            if not screen_lines:
                continue

            activity_log = "\n".join(screen_lines)

            # 3. LLM Gating Prompt
            system_prompt = (
                "You are an advanced proactive AI agent evaluating the user's desktop activity log over the past 10 minutes.\n"
                "Determine if there is an immediate, helpful optimization, warning, or task reminder required.\n"
                "If NO proactive action is needed, return exactly: 'IGNORE'.\n"
                "If action IS needed, return a helpful, brief proactive suggestion starting with 'NOTIFY: [your message]'."
            )
            
            prompt = (
                f"--- RECENT DESKTOP ACTIVITY ---\n"
                f"{activity_log}\n"
                f"-------------------------------\n"
                f"Evaluate this log now."
            )

            # Use fast backend
            backend = repo.get_config("llm_backend", "mock")
            url = repo.get_config("llm_url", "http://localhost:11434")
            model = repo.get_config("proactive_model", "qwen2.5:7b")
            client = get_llm_client(backend=backend, url=url, model=model)

            response_text = await client.generate_response(prompt=prompt, system_prompt=system_prompt)
            
            if not response_text or response_text.strip().upper().startswith("IGNORE"):
                continue

            notify_match = re.search(r"NOTIFY:\s*(.*)", response_text, re.IGNORECASE | re.DOTALL)
            if notify_match:
                alert_msg = notify_match.group(1).strip()
                
                # Cooldown logic (no alerts within 30 min, or identical alert)
                now = datetime.now(timezone.utc)
                if last_alert_time and (now - last_alert_time).total_seconds() < 1800:
                    logger.info("[Proactivity] Memory Evaluator blocked by 30-minute cooldown.")
                    continue
                if last_alert_text and alert_msg == last_alert_text:
                    logger.info("[Proactivity] Memory Evaluator blocked by duplicate alert text.")
                    continue

                # 4. Dispatch alert
                platform = repo.get_config("proactive_platform", "mock")
                if platform == "telegram":
                    user_id = repo.get_config("telegram_user_id") or repo.get_config("proactive_user_id", "mock_user")
                elif platform == "discord":
                    user_id = repo.get_config("discord_user_id") or repo.get_config("proactive_user_id", "mock_user")
                else:
                    user_id = repo.get_config("proactive_user_id", "mock_user")

                # Dispatch via router
                await router.send_message(
                    platform=platform,
                    user_id=user_id,
                    text=f"Proactive Alert: {alert_msg}"
                )

                # Save to DB
                repo.save_message(
                    channel=platform,
                    user_id=user_id,
                    sender_type="bot",
                    text=f"[Proactive Desktop Alert] {alert_msg}"
                )
                repo.log_proactivity(reason_code="desktop_activity_alert")
                
                last_alert_time = now
                last_alert_text = alert_msg

        except asyncio.CancelledError:
            logger.info("[Proactivity] Memory Evaluator engine shut down.")
            break
        except Exception as e:
            logger.error(f"[Proactivity] Memory Evaluator loop error: {e}")
            await asyncio.sleep(60)
        finally:
            db.close()
