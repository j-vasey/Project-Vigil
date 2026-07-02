import asyncio
import logging
from src.router import MessagingRouter
from src.models import InboundMessage
from src.database import SessionLocal
from src.repository import MessageRepository
from src.llm import get_llm_client

logger = logging.getLogger("project_vigil.orchestrator")

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
                
                # Fetch LLM configurations
                backend = repo.get_config("llm_backend", "mock")
                url = repo.get_config("llm_url", "http://localhost:11434")
                model = repo.get_config("llm_model", "gemma-4-26B-A-4B-it-UD-Q3_K_M:latest")
                num_ctx_str = repo.get_config("llm_num_ctx", "8192")
                try:
                    num_ctx = int(num_ctx_str)
                except ValueError:
                    num_ctx = 8192
                
                from src.llm import get_llm_client
                client = get_llm_client(backend=backend, url=url, model=model, num_ctx=num_ctx)
                
                # Dual-lane dynamic routing intent classification
                classifier_system = (
                    "You are an orchestrator intent classification routing agent. Your job is to classify the user's prompt "
                    "into one of two categories: 'SYNC_CHAT' or 'ASYNC_AGENT'.\n\n"
                    "Classify as 'SYNC_CHAT' if the user's input is standard conversational text, general questions/answers, "
                    "a request to search or look up past chat/memory history (e.g., 'What did we talk about earlier?', "
                    "'Do you remember X?', 'Look up my habits'), or an immediate status check.\n"
                    "Classify as 'ASYNC_AGENT' if the user's input requires running a heavy multi-step task, background research, "
                    "batch system/file adjustments, network scans/discoveries, VM management, or calendar modifications.\n\n"
                    "Respond with exactly 'SYNC_CHAT' or 'ASYNC_AGENT'."
                )
                classifier_prompt = f"Prompt to classify: {message.message_body}"
                route_decision = await client.generate_response(prompt=classifier_prompt, system_prompt=classifier_system)
                
                use_background = "ASYNC_AGENT" in route_decision.upper()
                
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
                    start_msg_text = await client.generate_response(prompt=start_prompt, system_prompt=start_system)
                    
                    # Send immediate response with the job_id
                    await router.send_message(
                        platform=message.platform,
                        user_id=message.user_id,
                        text=start_msg_text.strip()
                    )
                else:
                    logger.info(f"[Orchestrator] Routing prompt to Synchronous SYNC_CHAT Lane: {message.message_body}")
                    
                    # 1. Execute Active Memory MCP server's recall_memories() tool in-thread
                    recalled_context = ""
                     # Fallback check to avoid tool lookup failures in basic tests
                    from src.tools.registry import tool_registry
                    try:
                        recalled_context = await tool_registry.execute("recall_memories", {"query_string": message.message_body})
                    except Exception as e:
                        logger.warning(f"[Orchestrator] In-thread recall_memories execution failed: {e}")
                    
                    # 2. Retrieve history for context (sliding window of last 10 messages)
                    history = repo.get_sliding_window_history(
                        channel=message.platform,
                        user_id=message.user_id,
                        limit=10
                     )
                     
                    # 3. Construct prompt
                    dialogue_lines = []
                    # Strictly injected as an independent message object with a role of system
                    if recalled_context and "No memories found" not in recalled_context:
                        dialogue_lines.append(f"System: [Retrieved Memory Context]\n{recalled_context}")
                        
                    for hist_msg in history:
                        role = "User" if hist_msg.sender_type == "user" else "Companion"
                        dialogue_lines.append(f"{role}: {hist_msg.text}")
                    dialogue_lines.append("Companion:")
                    prompt = "\n".join(dialogue_lines)
                     
                    # 4. Generate response with inline memory instructions
                    inline_system_prompt = (
                        f"{system_prompt}\n\n"
                        "Memory Guideline: If you need to search or recall previous memories/facts to answer the user's question, "
                        "you MUST include the tag `[RECALL: search keywords]` in your response text. This will dynamically retrieve the facts.\n"
                        "Voice Constraint: You are provided with tool data behind the scenes. Never repeat the raw tool output or copy bracketed headers like '[Recalled Memories]:' into your direct speech. Speak only in your natural character voice."
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
