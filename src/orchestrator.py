import asyncio
import logging
from datetime import datetime, timezone, timedelta
from src.router import MessagingRouter
from src.models import InboundMessage
from src.database import SessionLocal
from src.repository import MessageRepository
from src.llm import get_llm_client

logger = logging.getLogger("project_vigil.orchestrator")


def _datetime_header() -> str:
    """Returns a current-date block to prepend to every system prompt.
    Providing the exact local date, UTC time, and day-of-week prevents the model
    from defaulting to its training-data era (2024) when resolving relative dates.
    """
    try:
        from src.mcp.servers.calendar import _get_user_timezone
        user_tz = _get_user_timezone()
        from datetime import timezone as _tz
        now_local = datetime.now(user_tz)
        now_utc = datetime.now(timezone.utc)
        tz_name = now_local.strftime("%Z")  # e.g. 'BST', 'GMT', 'EST'
    except Exception:
        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)
        tz_name = "local"
    day_name = now_local.strftime("%A")          # e.g. 'Friday'
    date_str = now_local.strftime("%d %B %Y")    # e.g. '04 July 2026'
    time_local = now_local.strftime("%H:%M")     # e.g. '19:05'
    time_utc = now_utc.strftime("%H:%M")
    return (
        f"[SYSTEM DATE/TIME — USE THIS FOR ALL DATE CALCULATIONS]\n"
        f"Today is {day_name}, {date_str}.\n"
        f"Current local time: {time_local} {tz_name}. UTC time: {time_utc} UTC.\n"
        f"Any date the user calls 'next week', 'this Sunday', 'tomorrow' etc. must be "
        f"calculated relative to today: {date_str}.\n"
        f"When creating calendar events, always pass times in LOCAL time ({tz_name}), "
        f"not UTC. The system will handle UTC conversion automatically.\n"
        f"[END SYSTEM DATE/TIME]\n\n"
    )

_inbound_queue = None

def get_queue() -> asyncio.Queue:
    """
    Returns the global inbound message queue, initializing it lazily to ensure
    it binds to the active running asyncio event loop.
    """
    global _inbound_queue
    if _inbound_queue is None:
        _inbound_queue = asyncio.Queue()
    return _inbound_queue

async def enqueue_inbound_message(message: InboundMessage) -> None:
    """
    Callback registered to the router. Enqueues the inbound message instantly,
    allowing the HTTP webhook route to respond immediately.
    """
    logger.info(f"[Orchestrator] Enqueuing inbound message from user {message.user_id} on platform '{message.platform}'")
    await get_queue().put(message)


def infer_stress_and_topics(text: str):
    lower_text = text.lower()
    stress = "low"
    high_stress_words = ["broken", "crash", "critical", "error", "failed", "urgent", "broke", "panic", "disaster"]
    med_stress_words = ["warn", "slow", "annoyed", "issue", "bug", "stuck", "loop", "fail"]
    
    if any(w in lower_text for w in high_stress_words):
        stress = "high"
    elif any(w in lower_text for w in med_stress_words):
        stress = "medium"
        
    topics = []
    if any(w in lower_text for w in ["server", "hyperv", "vm", "host", "linux"]):
        topics.append("infrastructure")
    if any(w in lower_text for w in ["calendar", "m365", "outlook", "meeting", "schedule"]):
        topics.append("calendar")
    if any(w in lower_text for w in ["file", "disk", "workspace", "folder", "drive"]):
        topics.append("filesystem")
    if any(w in lower_text for w in ["memory", "fact", "habit"]):
        topics.append("memory")
    if not topics:
        topics.append("general")
        
    return stress, ",".join(topics)


async def start_queue_worker(router: MessagingRouter) -> None:
    """
    Background worker loop. Consumes inbound_queue, performs database management,
    generates AI response out-of-band via the active LLM Client, and sends the response.
    """
    logger.info("[Orchestrator] Starting Decoupled Inbound Queue Worker...")
    from src.agent_runner import BackgroundAgentRunner
    runner = BackgroundAgentRunner(router)
    
    async def run_summarizer(channel: str, user_id: str, repo: MessageRepository):
        unsummarised = repo.get_unsummarised_window(channel, user_id, batch_size=20)
        if not unsummarised:
            return
            
        logger.info(f"[Orchestrator] Summarising {len(unsummarised)} old messages for {user_id}...")
        
        # Build text to summarize
        dialogue = []
        for m in unsummarised:
            role = "User" if m.sender_type == "user" else "Companion"
            dialogue.append(f"{role}: {m.text}")
            
        prompt = (
            "Summarize the following chunk of conversation into a concise, factual paragraph. "
            "Focus on what was discussed, any decisions made, or facts revealed. "
            "Do NOT output a transcript, just a high-level summary.\n\n"
            + "\n".join(dialogue)
        )
        
        backend = repo.get_config("llm_backend", "mock")
        url = repo.get_config("llm_url", "http://localhost:11434")
        model = repo.get_config("llm_model", "gemma:4")
        
        client = get_llm_client(backend=backend, url=url, model=model)
        try:
            summary = await client.generate_response(prompt=prompt, system_prompt="You are a helpful compression agent.", use_tools=False)
            
            # Get previous summary to combine if it exists
            prev_sum = repo.get_latest_conversation_summary(channel, user_id)
            if prev_sum:
                combine_prompt = (
                    "Combine the following two chronological conversation summaries into one cohesive, concise summary paragraph. "
                    "Keep it strictly under 3 sentences.\n\n"
                    f"Previous summary: {prev_sum.summary_text}\n\n"
                    f"New summary: {summary}"
                )
                summary = await client.generate_response(prompt=combine_prompt, system_prompt="You are a helpful compression agent.", use_tools=False)
                
            repo.save_conversation_summary(
                channel=channel, 
                user_id=user_id, 
                summary=summary.strip(), 
                from_id=unsummarised[0].id, 
                to_id=unsummarised[-1].id
            )
            logger.info("[Orchestrator] Summarization complete.")
        except Exception as e:
            logger.error(f"[Orchestrator] Summarization failed: {e}")

    
    while True:
        try:
            message: InboundMessage = await get_queue().get()
            logger.info(f"[Orchestrator] Worker picked up message from user {message.user_id} on '{message.platform}'")
            
            # Open direct session for background task
            db = SessionLocal()
            try:
                repo = MessageRepository(db)
                
                # Check system health status
                health_status = repo.get_config("system_health", "healthy")
                if health_status == "paused":
                    logger.warning("[Orchestrator] System is paused, ignoring incoming message processing.")
                    continue
                
                # 1. Save inbound message to Database
                repo.save_message(
                    channel=message.platform,
                    user_id=message.user_id,
                    sender_type="user",
                    text=message.message_body,
                    timestamp=message.timestamp
                )
                
                # 2. Track meta-metrics/sentiment on every user interaction
                stress_lvl, topic_str = infer_stress_and_topics(message.message_body)
                repo.log_user_trend(
                    stress_level=stress_lvl,
                    topics=topic_str,
                    user_message=message.message_body
                )
                logger.info(f"[Orchestrator] Inferred stress level '{stress_lvl}' and topics '{topic_str}' for incoming message.")
                
                # 3. Retrieve active configurations
                system_prompt = repo.get_config(
                    "system_prompt",
                    "You are a helpful, empathetic local AI companion named Project Vigil. Keep responses concise, warm, and supportive."
                )
                # Prepend current date/time so the model never resolves relative dates
                # against its training-data era (2024).
                system_prompt = _datetime_header() + system_prompt
                
                # Fetch LLM configurations
                backend = repo.get_config("llm_backend", "mock")
                url = repo.get_config("llm_url", "http://localhost:11434")
                model = repo.get_config("llm_model", "gemma:4")
                num_ctx_str = repo.get_config("llm_num_ctx", "32768")
                try:
                    num_ctx = int(num_ctx_str)
                except ValueError:
                    num_ctx = 32768
                
                from src.llm import get_llm_client
                client = get_llm_client(backend=backend, url=url, model=model, num_ctx=num_ctx)
                
                # Tri-lane dynamic routing intent classification
                classifier_system = (
                    "You are an orchestrator intent classification routing agent. Your job is to classify the user's prompt "
                    "into one of three categories: 'SYNC_CHAT', 'ASYNC_AGENT', or 'WEB_SEARCH'.\n\n"
                    "Classify as 'WEB_SEARCH' if the user explicitly asks to look up live information on the internet, "
                    "search the web, or asks for current events/news that require real-time knowledge.\n"
                    "Classify as 'SYNC_CHAT' if the user's input is standard conversational text, general questions/answers, "
                    "a request to look up past chat/memory history (e.g., 'What did we talk about earlier?', "
                    "'Do you remember X?', 'Look up my habits'), or an immediate status check.\n"
                    "Classify as 'ASYNC_AGENT' if the user's input requires running a heavy multi-step task, background research, "
                    "batch system/file adjustments, network scans/discoveries, VM management, or calendar modifications.\n\n"
                    "Respond with exactly 'SYNC_CHAT', 'ASYNC_AGENT', or 'WEB_SEARCH'."
                )
                classifier_prompt = f"Prompt to classify: {message.message_body}"
                route_decision = await client.generate_response(prompt=classifier_prompt, system_prompt=classifier_system, use_tools=False)
                
                use_background = "ASYNC_AGENT" in route_decision.upper()
                use_web_search = "WEB_SEARCH" in route_decision.upper()
                
                if use_background:
                    logger.info(f"[Orchestrator] Routing prompt to BackgroundAgentRunner (ASYNC_AGENT lane): {message.message_body}")
                    # Delegate to BackgroundAgentRunner for asynchronous job state machine processing
                    job_id = runner.start_job(
                        platform=message.platform,
                        user_id=message.user_id,
                        request=message.message_body,
                        system_prompt=system_prompt
                    )
                    
                    # Dynamic start message generation
                    start_system = (
                        "You are Project Vigil, a warm, helpful local AI companion. Given the user's request, "
                        f"write a single, brief, friendly sentence confirming you have started a background task [#{job_id[:8]}] to process it. "
                        "Incorporate the job token naturally into the text."
                    )
                    start_prompt = f"User request to start: {message.message_body}"
                    start_msg_text = await client.generate_response(prompt=start_prompt, system_prompt=start_system, use_tools=False)
                    
                    # Send immediate response with the job_id
                    await router.send_message(
                        platform=message.platform,
                        user_id=message.user_id,
                        text=start_msg_text.strip()
                    )
                elif use_web_search:
                    logger.info(f"[Orchestrator] Routing prompt to Autonomous Search Pipeline (WEB_SEARCH lane): {message.message_body}")
                    from src.search_pipeline import run_search_pipeline
                    
                    # 1. Provide an immediate typing / searching indicator if possible
                    # (Here we just execute it inline as a sync response)
                    search_response = await run_search_pipeline(message.message_body)
                    
                    # 2. Save bot message to Database
                    repo.save_message(
                        channel=message.platform,
                        user_id=message.user_id,
                        sender_type="bot",
                        text=search_response
                    )
                    
                    # 3. Send response back
                    await router.send_message(
                        platform=message.platform,
                        user_id=message.user_id,
                        text=search_response
                    )
                else:
                    logger.info(f"[Orchestrator] Routing prompt to Synchronous SYNC_CHAT Lane: {message.message_body}")
                    
                    # 1. Retrieve all stored user facts/memories up to a reasonable limit
                    recalled_context = ""
                    try:
                        memories = repo.search_memories("")[:50] # Get up to 50 most recent facts
                        if memories:
                            mem_lines = [f"[{m.category}] {m.fact}" for m in memories]
                            recalled_context = "\n".join(mem_lines)
                    except Exception as e:
                        logger.warning(f"[Orchestrator] Failed to fetch active memories: {e}")
                    
                    # 2. Retrieve history for context (sliding window of last 10 messages)
                    history = repo.get_sliding_window_history(
                        channel=message.platform,
                        user_id=message.user_id,
                        limit=10,
                        max_tokens=3000
                     )
                     
                    # 2.5 Retrieve long-term summary
                    long_term_summary = repo.get_latest_conversation_summary(message.platform, message.user_id)
                     
                    # 3. Construct prompt
                    dialogue_lines = []
                    # Strictly injected as an independent message object with a role of system
                    if recalled_context and "No memories found" not in recalled_context:
                        dialogue_lines.append(f"System: [Retrieved Memory Context]\n{recalled_context}")
                        
                    if long_term_summary:
                        dialogue_lines.append(f"System: [Previous Conversation Summary]\n{long_term_summary.summary_text}")
                        
                    for hist_msg in history:
                        role = "User" if hist_msg.sender_type == "user" else "Companion"
                        dialogue_lines.append(f"{role}: {hist_msg.text}")
                    dialogue_lines.append("Companion:")
                    prompt = "\n".join(dialogue_lines)
                     
                    recent_screen = repo.get_recent_system_context(minutes=30)
                    screen_context_block = ""
                    if recent_screen:
                        import json
                        screen_lines = ["--- CURRENT DESKTOP CONTEXT ---"]
                        screen_lines.append("Within the last 30 minutes, the user was working on:")
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
                        screen_lines.append("-------------------------------")
                        if len(screen_lines) > 3:
                            screen_context_block = "\n".join(screen_lines) + "\n\n"

                    # 4. Generate response with inline tool tag instructions
                    inline_system_prompt = (
                        f"{system_prompt}\n\n"
                        f"{screen_context_block}"
                        "RUNTIME CONTEXT/INSTRUCTIONS:\n"
                        "- Review the sliding conversation history to understand the flow.\n"
                        "- Use the [Retrieved Memory Context] and [Previous Conversation Summary] to actively propose new ideas, ask relevant questions about the user's life/goals, and drive the conversation forward.\n"
                        "- Do not just passively answer questions. End your responses with an engaging hook or question when appropriate to lead the dialogue.\n"
                        "- Answer the user's most recent prompt strictly in character.\n\n"
                        "=== TOOL ACCESS ===\n"
                        "You have access to the following tools. Use them whenever you need live data:\n"
                        "  • web_search(query)            — live web / news search\n"
                        "  • get_weather(location)        — live weather forecast\n"
                        "  • view_upcoming_agenda(days_ahead) — fetch upcoming calendar events\n"
                        "  • recall_memories(query_string) — retrieve stored personal facts\n"
                        "  • get_system_metrics()         — host CPU/RAM/disk usage\n"
                        "  • view_screen()                — capture and analyze user's screen\n"
                        "PREFERRED: invoke tools natively if your runtime supports it.\n"
                        "FALLBACK: if native tool calls are unavailable, embed ONE trigger tag in your reply:\n"
                        "  [SEARCH: your query here]\n"
                        "  [WEATHER: city name]\n"
                        "  [VIEW_UPCOMING_AGENDA: 7]\n"
                        "  [RECALL: keywords]\n"
                        "  [IMAGE: description]\n"
                        "  [VIEW_SCREEN]\n"
                        "Do NOT include a tag AND prose together — use one or the other.\n"
                        "NEVER send a raw tag to the user. Tags are internal signals only.\n"
                        "=== END TOOL ACCESS ==="
                    )
                    response_text = await client.generate_response(prompt=prompt, system_prompt=inline_system_prompt)
                    
                    # Check for [RECALL: query] trigger
                    import re
                    recall_match = re.search(r"\[RECALL:\s*(.*?)\]", response_text)
                    if recall_match:
                        recall_query = recall_match.group(1).strip()
                        logger.info(f"[Orchestrator] Found memory recall trigger in synchronous response. Query: '{recall_query}'")
                        
                        memories = repo.search_memories(recall_query)
                        if memories:
                            mem_text = "\n".join([f"- [{m.category}] {m.fact}" for m in memories])
                            fact_block = f"[Recalled Memories]:\n{mem_text}"
                        else:
                            fact_block = f"[Recalled Memories]: No memories found for query '{recall_query}'."
                        
                        # Strip the tag from the assistant message
                        clean_prev_response = re.sub(r"\[RECALL:\s*(.*?)\]", "", response_text).strip()
                        
                        # Re-run LLM with fact block as an independent System message
                        second_dialogue = []
                        second_dialogue.append(f"System: {fact_block}")
                        for hist_msg in history:
                            role = "User" if hist_msg.sender_type == "user" else "Companion"
                            second_dialogue.append(f"{role}: {hist_msg.text}")
                        if clean_prev_response:
                            second_dialogue.append(f"Companion: {clean_prev_response}")
                        second_dialogue.append("Companion:")
                        
                        second_prompt = "\n".join(second_dialogue)
                        response_text = await client.generate_response(prompt=second_prompt, system_prompt=inline_system_prompt)
                    
                    # Check for [SEARCH: query] trigger
                    search_match = re.search(r"\[SEARCH:\s*(.*?)\]", response_text)
                    if search_match:
                        search_query = search_match.group(1).strip()
                        logger.info(f"[Orchestrator] Found [SEARCH:] trigger in response. Query: '{search_query}'")
                        try:
                            search_results = await tool_registry.execute("web_search", {"query": search_query})
                        except Exception as se:
                            search_results = f"Web search failed: {se}"
                        
                        clean_prev = re.sub(r"\[SEARCH:\s*(.*?)\]", "", response_text).strip()
                        search_dialogue = []
                        search_dialogue.append(f"System: [Web Search Results for '{search_query}']:\n{search_results}")
                        for hist_msg in history:
                            role = "User" if hist_msg.sender_type == "user" else "Companion"
                            search_dialogue.append(f"{role}: {hist_msg.text}")
                        if clean_prev:
                            search_dialogue.append(f"Companion: {clean_prev}")
                        search_dialogue.append("Companion:")
                        followup_text = await client.generate_response(
                            prompt="\n".join(search_dialogue),
                            system_prompt=inline_system_prompt
                        )
                        response_text = followup_text.strip() if followup_text and followup_text.strip() else (clean_prev or "I ran a web search for you.")

                    # Check for [WEATHER: location] trigger
                    weather_match = re.search(r"\[WEATHER:\s*(.*?)\]", response_text, re.IGNORECASE)
                    if weather_match:
                        weather_loc = weather_match.group(1).strip()
                        logger.info(f"[Orchestrator] Found [WEATHER:] trigger in response. Location: '{weather_loc}'")
                        try:
                            weather_results = await tool_registry.execute("get_weather", {"location": weather_loc})
                        except Exception as we:
                            weather_results = f"Weather lookup failed: {we}"
                        
                        clean_prev = re.sub(r"\[WEATHER:\s*(.*?)\]", "", response_text, flags=re.IGNORECASE).strip()
                        weather_dialogue = []
                        weather_dialogue.append(f"System: [Weather Data Retrieved]:\n{weather_results}")
                        for hist_msg in history:
                            role = "User" if hist_msg.sender_type == "user" else "Companion"
                            weather_dialogue.append(f"{role}: {hist_msg.text}")
                        if clean_prev:
                            weather_dialogue.append(f"Companion: {clean_prev}")
                        weather_dialogue.append("Companion:")
                        followup_text = await client.generate_response(
                            prompt="\n".join(weather_dialogue),
                            system_prompt=inline_system_prompt
                        )
                        response_text = followup_text.strip() if followup_text and followup_text.strip() else (clean_prev or "I checked the weather for you.")

                    # Check for inline calendar / MCP tool trigger tags emitted by the LLM.
                    # Supported patterns: [VIEW_UPCOMING_AGENDA: N], [LIST_CALENDAR_EVENTS: query],
                    # [CREATE_CALENDAR_EVENT: details], [VIEW_TODAY_SCHEDULE], etc.
                    calendar_tag_match = re.search(
                        r"\[(VIEW_UPCOMING_AGENDA|LIST_CALENDAR_EVENTS|CREATE_CALENDAR_EVENT|VIEW_TODAY_SCHEDULE)(?::\s*(.*?))?\]",
                        response_text,
                        re.IGNORECASE
                    )
                    if calendar_tag_match:
                        cal_tool_raw = calendar_tag_match.group(1).upper()
                        cal_arg = (calendar_tag_match.group(2) or "").strip()
                        logger.info(f"[Orchestrator] Found [{cal_tool_raw}:] trigger in response. Arg: '{cal_arg}'")
                        
                        # Map tag names to the actual MCP calendar server tool names and arg signatures
                        cal_tool_map = {
                            "VIEW_UPCOMING_AGENDA": "view_upcoming_agenda",
                            "LIST_CALENDAR_EVENTS": "view_upcoming_agenda",
                            "CREATE_CALENDAR_EVENT": "create_calendar_event",
                            "VIEW_TODAY_SCHEDULE": "view_upcoming_agenda",
                        }
                        mcp_tool_name = cal_tool_map.get(cal_tool_raw, "view_upcoming_agenda")
                        
                        # Build arguments matching the actual MCP tool signatures
                        if mcp_tool_name == "view_upcoming_agenda":
                            try:
                                days = int(cal_arg) if cal_arg and cal_arg.isdigit() else 7
                            except Exception:
                                days = 7
                            cal_args = {"days_ahead": days}
                        else:
                            cal_args = {"details": cal_arg} if cal_arg else {}
                        
                        try:
                            cal_results = await tool_registry.execute(mcp_tool_name, cal_args)
                        except Exception as ce:
                            cal_results = f"Calendar tool failed: {ce}"
                        
                        clean_prev = re.sub(
                            r"\[(VIEW_UPCOMING_AGENDA|LIST_CALENDAR_EVENTS|CREATE_CALENDAR_EVENT|VIEW_TODAY_SCHEDULE)(?::\s*(.*?))?\]",
                            "", response_text, flags=re.IGNORECASE
                        ).strip()
                        cal_dialogue = []
                        cal_dialogue.append(f"System: [Calendar Data Retrieved]:\n{cal_results}")
                        for hist_msg in history:
                            role = "User" if hist_msg.sender_type == "user" else "Companion"
                            cal_dialogue.append(f"{role}: {hist_msg.text}")
                        if clean_prev:
                            cal_dialogue.append(f"Companion: {clean_prev}")
                        cal_dialogue.append("Companion:")
                        followup_text = await client.generate_response(
                            prompt="\n".join(cal_dialogue),
                            system_prompt=inline_system_prompt
                        )
                        response_text = followup_text.strip() if followup_text and followup_text.strip() else (clean_prev or "I checked your calendar for you.")

                    # Check for [VIEW_SCREEN] trigger
                    screen_match = re.search(r"\[VIEW_SCREEN\]", response_text, re.IGNORECASE)
                    if screen_match:
                        logger.info(f"[Orchestrator] Found [VIEW_SCREEN] trigger in response.")
                        try:
                            screen_results = await tool_registry.execute("view_screen", {})
                        except Exception as se:
                            screen_results = f"Screen capture failed: {se}"
                        
                        clean_prev = re.sub(r"\[VIEW_SCREEN\]", "", response_text, flags=re.IGNORECASE).strip()
                        screen_dialogue = []
                        screen_dialogue.append(f"System: [Screen Capture Analyzed]:\n{screen_results}")
                        for hist_msg in history:
                            role = "User" if hist_msg.sender_type == "user" else "Companion"
                            screen_dialogue.append(f"{role}: {hist_msg.text}")
                        if clean_prev:
                            screen_dialogue.append(f"Companion: {clean_prev}")
                        screen_dialogue.append("Companion:")
                        followup_text = await client.generate_response(
                            prompt="\n".join(screen_dialogue),
                            system_prompt=inline_system_prompt
                        )
                        response_text = followup_text.strip() if followup_text and followup_text.strip() else (clean_prev or "I took a look at your screen.")

                    # Check for [IMAGE: image prompt] trigger
                    image_match = re.search(r"\[IMAGE:\s*(.*?)\]", response_text)
                    
                    if image_match:
                        image_prompt = image_match.group(1).strip()
                        logger.info(f"[Orchestrator] Found image trigger in synchronous response. Prompt: '{image_prompt}'")
                        
                        clean_text = re.sub(r"\[IMAGE:\s*(.*?)\]", "", response_text).strip()
                        
                        # Fetch ComfyUI configurations
                        comfy_backend = repo.get_config("comfyui_backend", "mock")
                        comfy_url = repo.get_config("comfyui_url", "http://localhost:8188")
                        comfy_ckpt = repo.get_config("comfyui_ckpt", "v1-5-pruned-emaonly.safetensors")
                        
                        from src.comfyui import ComfyUIClient
                        comfy_client = ComfyUIClient(base_url=comfy_url, backend=comfy_backend, ckpt_name=comfy_ckpt)
                        img_bytes = await comfy_client.generate_image(image_prompt)
                        
                        repo.save_message(
                            channel=message.platform,
                            user_id=message.user_id,
                            sender_type="bot",
                            text=f"[IMAGE: {image_prompt}] {clean_text}"
                        )
                        
                        if img_bytes:
                            await router.send_image(
                                platform=message.platform,
                                user_id=message.user_id,
                                image_bytes=img_bytes,
                                filename="vigil_generation.png",
                                caption=clean_text
                            )
                        else:
                            await router.send_message(
                                platform=message.platform,
                                user_id=message.user_id,
                                text=clean_text or f"[Image prompt: '{image_prompt}']"
                            )
                    else:
                        if not response_text or not response_text.strip():
                            logger.warning("[Orchestrator] Response text evaluated to empty. Using default fallback message.")
                            response_text = "I received your message and processed it."

                        # 4. Save bot message to Database
                        repo.save_message(
                            channel=message.platform,
                            user_id=message.user_id,
                            sender_type="bot",
                            text=response_text
                        )
                        
                        # 5. Send response back
                        await router.send_message(
                            platform=message.platform,
                            user_id=message.user_id,
                            text=response_text
                        )
                        # 6. Trigger background summarizer check
                    try:
                        asyncio.create_task(run_summarizer(message.platform, message.user_id, repo))
                        
                        # 7. Trigger background memory extraction
                        from src.memory_extractor import extract_and_store_memories
                        message_text = getattr(message, 'text', None) or getattr(message, 'message_body', None) or str(message)
                        asyncio.create_task(extract_and_store_memories(message_text, repo))
                    except Exception as bg_ex:
                        logger.error(f"[Orchestrator] Non-fatal error during background logging setup: {bg_ex}")
                
            except Exception as ex:
                logger.exception(f"[Orchestrator] Database transaction error in queue worker: {ex}")
            finally:
                db.close()
                get_queue().task_done()
                
        except asyncio.CancelledError:
            logger.info("[Orchestrator] Decoupled Inbound Queue Worker cancelled.")
            break
        except Exception as e:
            logger.exception(f"[Orchestrator] Unexpected exception in worker loop: {e}")
            await asyncio.sleep(2) # Avoid aggressive looping on persistent exceptions
