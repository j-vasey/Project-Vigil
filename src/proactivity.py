import asyncio
import logging
import random
import os
import json
from datetime import datetime, time
from typing import Optional

logger = logging.getLogger("project_vigil.proactivity")

async def extract_lifestyle_context() -> str:
    """
    Aggregates personal metrics: M365 calendar, local goals.json, and todo.json.
    """
    # 1. Fetch Calendar from M365 Calendar server
    calendar_text = "No calendar events."
    try:
        from src.mcp.servers.calendar import view_upcoming_agenda
        calendar_text = await view_upcoming_agenda(days_ahead=3)
    except Exception as e:
        logger.warning(f"[Proactivity] Failed to fetch calendar agenda: {e}")

    # 2. Fetch Goals from goals.json
    goals_text = "No goals configured."
    goals_path = "goals.json"
    if os.path.exists(goals_path):
        try:
            with open(goals_path, "r", encoding="utf-8") as f:
                goals_data = json.load(f)
                goals_text = json.dumps(goals_data, indent=2)
        except Exception as e:
            logger.warning(f"[Proactivity] Failed to read goals.json: {e}")

    # 3. Fetch Tasks from todo.json
    todo_text = "No pending tasks."
    todo_path = "todo.json"
    if os.path.exists(todo_path):
        try:
            with open(todo_path, "r", encoding="utf-8") as f:
                todo_data = json.load(f)
                todo_text = json.dumps(todo_data, indent=2)
        except Exception as e:
            logger.warning(f"[Proactivity] Failed to read todo.json: {e}")

    context = (
        "=== LIFESTYLE COORDINATOR CONTEXT ===\n"
        f"[Upcoming Calendar Events]:\n{calendar_text}\n\n"
        f"[Financial & Life Goals]:\n{goals_text}\n\n"
        f"[Pending To-Do Tasks]:\n{todo_text}\n"
        "====================================="
    )
    return context
from src.database import SessionLocal
from src.repository import MessageRepository
from src.router import MessagingRouter
from src.llm import get_llm_client

logger = logging.getLogger("project_vigil.proactivity")

def is_time_in_range(start: time, end: time, current: time) -> bool:
    """
    Utility checking if a time falls in a range, handling overnight wraps.
    E.g. start=22:00, end=08:00, current=23:00 -> True
    """
    if start <= end:
        return start <= current <= end
    else:  # Wrap around midnight
        return current >= start or current <= end


def is_dnd_active(dnd_start_str: str, dnd_end_str: str) -> bool:
    """
    Determines if the current system time lies inside the DND constraint boundary.
    """
    try:
        sh, sm = map(int, dnd_start_str.split(":"))
        eh, em = map(int, dnd_end_str.split(":"))
        start_time = time(sh, sm)
        end_time = time(eh, em)
        current_time = datetime.now().time()
        return is_time_in_range(start_time, end_time, current_time)
    except Exception as e:
        logger.error(f"[Proactivity] Failed parsing DND configurations '{dnd_start_str}'-'{dnd_end_str}': {e}")
        return False


async def trigger_proactive_outreach(reason_code: str, router: MessagingRouter) -> Optional[str]:
    """
    Triggers an outbound proactive AI greeting:
    1. Validates system active/paused status.
    2. Runs DND boundary checks.
    3. Retrieves context history for sliding prompt insertion.
    4. Submits prompt to configured LLM client.
    5. Dispatches response and writes to audit logs.
    """
    db = SessionLocal()
    try:
        repo = MessageRepository(db)
        
        # Load proactive endpoint coordinates
        platform = repo.get_config("proactive_platform", "mock")
        if platform == "telegram":
            user_id = repo.get_config("telegram_user_id") or repo.get_config("proactive_user_id", "mock_user")
        elif platform == "discord":
            user_id = repo.get_config("discord_user_id") or repo.get_config("proactive_user_id", "mock_user")
        else:
            user_id = repo.get_config("proactive_user_id", "mock_user")
        
        # 1. Check system status
        health_status = repo.get_config("system_health", "healthy")
        if health_status == "paused":
            logger.info("[Proactivity] Skipped: System engine is paused.")
            repo.log_proactivity(reason_code=f"{reason_code}_SKIPPED_PAUSED", message_dispatched=None)
            return None
            
        # 1.5 Check if proactivity is temporarily paused
        paused_until_str = repo.get_config("proactivity_paused_until", "")
        if paused_until_str:
            try:
                from datetime import datetime
                paused_until = datetime.fromisoformat(paused_until_str)
                if datetime.utcnow() < paused_until:
                    logger.info(f"[Proactivity] Skipped: Proactivity is temporarily paused until {paused_until_str} UTC.")
                    repo.log_proactivity(reason_code=f"{reason_code}_SKIPPED_TEMP_PAUSE", message_dispatched=None)
                    return None
            except Exception as e:
                logger.error(f"[Proactivity] Failed parsing proactivity_paused_until '{paused_until_str}': {e}")
            
        # 2. Check DND
        dnd_start = repo.get_config("dnd_start", "22:00")
        dnd_end = repo.get_config("dnd_end", "08:00")
        
        if is_dnd_active(dnd_start, dnd_end):
            logger.info(f"[Proactivity] Blocked by DND ({dnd_start} - {dnd_end}). Waiting for tomorrow.")
            repo.log_proactivity(reason_code=f"{reason_code}_SKIPPED_DND", message_dispatched=None)
            return None
            
        logger.info(f"[Proactivity] Initiating outreach '{reason_code}' to {user_id} via '{platform}'...")
        
        # 3. Retrieve recent history for context
        history = repo.get_sliding_window_history(channel=platform, user_id=user_id, limit=5)
        history_str = ""
        if history:
            history_str = "\n".join([f"{'User' if h.sender_type == 'user' else 'Companion'}: {h.text}" for h in history])
            
        # Compile prompt details
        # Check active memories for negative behavioral macro-trends
        habit_memories = repo.search_memories(query="")
        habit_memories = [m for m in habit_memories if m.category == "user_habit"]
        
        focus_habit = ""
        if habit_memories:
            focus_habit = habit_memories[0].fact
            
        # Get lifestyle coordinator context
        lifestyle_context = await extract_lifestyle_context()
            
        if focus_habit:
            logger.info(f"[Proactivity] Discovered negative behavioral macro-trend: '{focus_habit}'")
            focus = (
                f"Discovered a negative user behavioral macro-trend: '{focus_habit}'. "
                "Autonomously initiate an empathetic, protective outreach conversation. "
                "Refer to this trend (e.g. pulling long hours on Tuesdays) and offer to use the M365 scheduling tools to check their schedule or clear space."
            )
        elif reason_code == "morning_brief":
            focus = (
                "It is morning (08:00). Review the user's day. If they have an event or trip coming up, "
                "proactively calculate commute buffers or flag potential weather changes based on event details. "
                "If they are on track with their personal life or financial goals, occasionally offer empathetic, encouraging "
                "reinforcement in your true companion voice. Keep notifications concise, actionable, and supportive."
            )
        elif reason_code == "evening_summary":
            focus = (
                "It is evening (21:00). Ask the user how their day went and summarize in a supportive way. "
                "Review their progress against their life goals and pending tasks from today. "
                "Keep notifications concise, actionable, and supportive."
            )
        else:
            focus = "Initiate a friendly, casual check-in to see how they are doing."
            
        prompt = "System Context: The companion system is initiating outbound outreach to the user.\n"
        if history_str:
            prompt += f"Recent Chat History:\n{history_str}\n\n"
        prompt += f"User Lifestyle Context:\n{lifestyle_context}\n\n"
        prompt += f"Outreach Focus: {focus}\n"
        prompt += "Companion (outbound outreach message):"
        
        # Load LLM settings
        backend = repo.get_config("llm_backend", "mock")
        url = repo.get_config("llm_url", "http://localhost:11434")
        model = repo.get_config("llm_model", "gemma:4")
        num_ctx_str = repo.get_config("llm_num_ctx", "8192")
        try:
            num_ctx = int(num_ctx_str)
        except ValueError:
            num_ctx = 8192
            
        system_prompt = repo.get_config(
            "system_prompt", 
            "You are a warm, helpful local AI companion named Project Vigil. Write a single, brief conversational opening sentence. No hashtags, no scripts."
        )
        
        # 4. Invoke LLM backend
        client = get_llm_client(backend=backend, url=url, model=model, num_ctx=num_ctx)
        generated_msg = await client.generate_response(prompt=prompt, system_prompt=system_prompt)
        
        # Check for [SEARCH: search query] tool invocation
        import re
        search_match = re.search(r"\[SEARCH:\s*(.*?)\]", generated_msg)
        if search_match:
            search_query = search_match.group(1).strip()
            logger.info(f"[Proactivity] Model requested web search: '{search_query}'")
            
            from src.tools.search import search_web_tool
            search_results = await search_web_tool(search_query)
            
            # Rebuild prompt incorporating search results
            prompt_lines = prompt.split("\n")
            prompt_lines.pop()  # Remove "Companion (outbound outreach message):"
            
            clean_first = re.sub(r"\[SEARCH:\s*(.*?)\]", "", generated_msg).strip()
            if clean_first:
                prompt_lines.append(f"Companion: (Searching the web for '{search_query}'...) {clean_first}")
            
            prompt_lines.append(f"System: Web search results for '{search_query}':\n{search_results}\nAnswer the user using these facts.")
            prompt_lines.append("Companion (outbound outreach message):")
            
            new_prompt = "\n".join(prompt_lines)
            generated_msg = await client.generate_response(prompt=new_prompt, system_prompt=system_prompt)
            
        # Check for [IMAGE: image prompt] trigger
        import re
        image_match = re.search(r"\[IMAGE:\s*(.*?)\]", generated_msg)
        
        if image_match:
            image_prompt = image_match.group(1).strip()
            logger.info(f"[Proactivity] Found image trigger in outbound greeting. Prompt: '{image_prompt}'")
            
            # Strip trigger tag from text
            clean_text = re.sub(r"\[IMAGE:\s*(.*?)\]", "", generated_msg).strip()
            
            # Fetch ComfyUI configurations
            comfy_backend = repo.get_config("comfyui_backend", "mock")
            comfy_url = repo.get_config("comfyui_url", "http://localhost:8188")
            comfy_ckpt = repo.get_config("comfyui_ckpt", "v1-5-pruned-emaonly.safetensors")
            
            # Generate image
            from src.comfyui import ComfyUIClient
            comfy_client = ComfyUIClient(base_url=comfy_url, backend=comfy_backend, ckpt_name=comfy_ckpt)
            img_bytes = await comfy_client.generate_image(image_prompt)
            
            # 5. Save generated message (with tag) to history
            repo.save_message(channel=platform, user_id=user_id, sender_type="bot", text=f"[IMAGE: {image_prompt}] {clean_text}")
            
            # 6. Send image via Router
            if img_bytes:
                sent = await router.send_image(
                    platform=platform,
                    user_id=user_id,
                    image_bytes=img_bytes,
                    filename="vigil_outreach.png",
                    caption=clean_text
                )
            else:
                logger.error("[Proactivity] Image generation returned empty bytes. Falling back to text send.")
                sent = await router.send_message(platform=platform, user_id=user_id, text=clean_text or f"[Image prompt: '{image_prompt}']")
        else:
            # 5. Save generated message to history
            repo.save_message(channel=platform, user_id=user_id, sender_type="bot", text=generated_msg)
            
            # 6. Send message via Router
            sent = await router.send_message(platform=platform, user_id=user_id, text=generated_msg)
            
        # 7. Audit log write
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


async def start_proactivity_engine(router: MessagingRouter) -> None:
    """
    Background schedule runner. Wakes up periodically with timing jitter,
    and pseudo-randomly rolls a probability check before triggering outbound outreach.
    Also handles scheduled Morning Briefings (08:00) and Evening Reflections (21:00).
    """
    logger.info("[Proactivity] Starting Proactivity Engine loop...")
    while True:
        try:
            # Timed briefing schedule checks
            now = datetime.now()
            current_date_str = now.date().isoformat()
            
            db = SessionLocal()
            try:
                repo = MessageRepository(db)
                
                # Check for Morning Briefing (08:00 - 08:59)
                if now.hour == 8:
                    last_morning = repo.get_config("last_morning_brief_date", "")
                    if last_morning != current_date_str:
                        logger.info("[Proactivity] Triggering scheduled Morning Briefing (08:00)...")
                        repo.set_config("last_morning_brief_date", current_date_str)
                        db.commit()
                        db.close()
                        await trigger_proactive_outreach(reason_code="morning_brief", router=router)
                        db = SessionLocal()
                        repo = MessageRepository(db)
                
                # Check for Evening Reflection (21:00 - 21:59)
                elif now.hour == 21:
                    last_evening = repo.get_config("last_evening_summary_date", "")
                    if last_evening != current_date_str:
                        logger.info("[Proactivity] Triggering scheduled Evening Reflection (21:00)...")
                        repo.set_config("last_evening_summary_date", current_date_str)
                        db.commit()
                        db.close()
                        await trigger_proactive_outreach(reason_code="evening_summary", router=router)
                        db = SessionLocal()
                        repo = MessageRepository(db)
            except Exception as sched_err:
                logger.error(f"[Proactivity] Error in briefing schedule check: {sched_err}")
            finally:
                db.close()

            # Proceed with normal interval configuration loading
            db = SessionLocal()
            repo = MessageRepository(db)
            interval_str = repo.get_config("proactive_interval_seconds", "3600")
            jitter_str = repo.get_config("proactive_jitter_percentage", "0.30")
            
            interval = int(interval_str)
            jitter_percentage = float(jitter_str)
            db.close()
            
            # Apply timing jitter (pseudo-random delay variation)
            max_jitter = interval * jitter_percentage
            actual_sleep = interval + random.uniform(-max_jitter, max_jitter)
            actual_sleep = max(1.0, actual_sleep)
            
            logger.info(f"[Proactivity] Next check scheduled in {actual_sleep:.2f}s (base: {interval}s, jitter: ±{jitter_percentage*100}%)")
            await asyncio.sleep(actual_sleep)
            
            # Perform the pseudo-random roll check inside a fresh session
            db = SessionLocal()
            try:
                repo = MessageRepository(db)
                health_status = repo.get_config("system_health", "healthy")
                if health_status == "paused":
                    logger.info("[Proactivity] System paused. Skipping roll check.")
                    db.close()
                    continue
                
                # Check roll probability threshold
                probability_str = repo.get_config("proactive_probability", "0.25")
                probability = float(probability_str)
                
                roll = random.random()
                if roll < probability:
                    logger.info(f"[Proactivity] Successful roll (roll: {roll:.3f} < threshold: {probability}). Initiating conversation.")
                    # Close db transaction before running outreach to prevent lock contention
                    db.close()
                    await trigger_proactive_outreach(reason_code="autonomous_check_in", router=router)
                else:
                    logger.info(f"[Proactivity] Failed roll (roll: {roll:.3f} >= threshold: {probability}). Skipping outreach.")
                    repo.log_proactivity(reason_code="autonomous_check_in_SKIPPED_ROLL", message_dispatched=None)
                    db.close()
            except Exception as inner_ex:
                logger.exception(f"[Proactivity] Exception inside roll check transaction: {inner_ex}")
                db.close()
            
        except asyncio.CancelledError:
            logger.info("[Proactivity] Engine loop shut down.")
            break
        except Exception as e:
            logger.exception(f"[Proactivity] Error in background loop: {e}")
            await asyncio.sleep(15) # Wait before retry
