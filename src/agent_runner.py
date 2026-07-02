import asyncio
import json
import logging
import re
import uuid
import time
import os
from datetime import datetime
from typing import Dict, Any, List

from src.database import SessionLocal
from src.repository import MessageRepository
from src.llm import get_llm_client
from src.router import MessagingRouter

logger = logging.getLogger("project_vigil.agent_runner")


# ---------------------------------------------------------------------------
# Inline tag executor — mirrors the SYNC_CHAT intercept logic so the async
# worker can resolve tool tags that the LLM emits as text fallbacks.
# ---------------------------------------------------------------------------

async def _resolve_inline_tags(text: str, repo: MessageRepository) -> tuple[str, str]:
    """
    Checks for any inline tool trigger tags in `text`, executes the
    corresponding tool, and returns (clean_text, tool_result_block).
    Returns (text, "") if no tag is found.
    """
    from src.tools.registry import tool_registry

    # [RECALL: query]
    m = re.search(r"\[RECALL:\s*(.*?)\]", text)
    if m:
        query = m.group(1).strip()
        memories = repo.search_memories(query)
        if memories:
            mem_text = "\n".join([f"- [{mem.category}] {mem.fact}" for mem in memories])
            result = f"[Recalled Memories]:\n{mem_text}"
        else:
            result = f"[Recalled Memories]: No memories found for '{query}'."
        clean = re.sub(r"\[RECALL:\s*(.*?)\]", "", text).strip()
        return clean, result

    # [SEARCH: query]
    m = re.search(r"\[SEARCH:\s*(.*?)\]", text)
    if m:
        query = m.group(1).strip()
        try:
            result = await tool_registry.execute("web_search", {"query": query})
        except Exception as e:
            result = f"Web search failed: {e}"
        clean = re.sub(r"\[SEARCH:\s*(.*?)\]", "", text).strip()
        return clean, f"[Web Search Results for '{query}']:\n{result}"

    # [VIEW_UPCOMING_AGENDA: N]
    m = re.search(
        r"\[(VIEW_UPCOMING_AGENDA|LIST_CALENDAR_EVENTS|VIEW_TODAY_SCHEDULE)(?::\s*(.*?))?\]",
        text, re.IGNORECASE
    )
    if m:
        arg = (m.group(2) or "").strip()
        try:
            days = int(arg) if arg and arg.isdigit() else 7
        except Exception:
            days = 7
        try:
            result = await tool_registry.execute("view_upcoming_agenda", {"days_ahead": days})
        except Exception as e:
            result = f"Calendar lookup failed: {e}"
        clean = re.sub(
            r"\[(VIEW_UPCOMING_AGENDA|LIST_CALENDAR_EVENTS|VIEW_TODAY_SCHEDULE)(?::\s*(.*?))?\]",
            "", text, flags=re.IGNORECASE
        ).strip()
        return clean, f"[Calendar Events]:\n{result}"

    return text, ""


class BackgroundAgentRunner:
    """
    Manages long-running stateful multi-agent jobs executing in background workers.
    Each worker step now genuinely executes tool calls (native Ollama tool_calls or
    inline tag fallback) and checkpoints real findings — not hallucinated text.
    """

    def __init__(self, router: MessagingRouter):
        self.router = router

    def start_job(self, platform: str, user_id: str, request: str, system_prompt: str) -> str:
        """Immediately spawns and returns a unique job_id for a long-running task."""
        job_id = str(uuid.uuid4())

        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            repo.save_agent_job_state(
                job_id=job_id,
                status="running",
                plan_json="[]",
                findings_json="{}",
                step_count=0
            )
        finally:
            db.close()

        asyncio.create_task(self._run_job_loop(job_id, platform, user_id, request, system_prompt))
        return job_id

    async def _run_job_loop(
        self, job_id: str, platform: str, user_id: str,
        request: str, base_system_prompt: str
    ):
        logger.info(f"[Agent Runner] Starting background job {job_id} for user {user_id} on {platform}...")

        db = SessionLocal()
        repo = MessageRepository(db)

        backend = repo.get_config("llm_backend", "mock")
        url = repo.get_config("llm_url", "http://localhost:11434")
        model = repo.get_config("llm_model", "gemma:4")
        num_ctx_str = repo.get_config("llm_num_ctx", "8192")
        try:
            num_ctx = int(num_ctx_str)
        except ValueError:
            num_ctx = 8192
        client = get_llm_client(backend=backend, url=url, model=model, num_ctx=num_ctx)

        # ── Persona wrapper ───────────────────────────────────────────────
        async def call_with_persona(prompt: str, sub_system: str) -> str:
            system_prompt_db = repo.get_config(
                "system_prompt",
                "You are a helpful, empathetic local AI companion named Project Vigil."
            )
            guidelines_db = repo.get_config("behavioral_guidelines", "")
            user_habit_db = repo.get_config("user_habit", "")
            full_system = (
                f"PRIMARY COMPANION PERSONALITY:\n{system_prompt_db}\n\n"
                f"BEHAVIORAL GUIDELINES:\n{guidelines_db}\n\n"
                f"USER TREND SUMMARY:\n{user_habit_db}\n\n"
                "--- END PERSONA ---\n\n"
            ) + sub_system
            return await client.generate_response(prompt=prompt, system_prompt=full_system)

        # ── Worker step with real tool execution ──────────────────────────
        async def execute_worker_step(task_desc: str, context_findings: dict) -> str:
            """
            Calls the LLM with tool schemas for the given task. If the model
            invokes a native tool_call, the OllamaClient handles it automatically.
            If the model emits an inline tag fallback, we resolve it here and
            re-run the LLM with the real data injected as context.
            """
            worker_system = (
                f"You are the Worker Engine for Project Vigil's background agent. "
                f"Current task: '{task_desc}'\n"
                f"Findings so far: {json.dumps(context_findings)}\n\n"
                "=== TOOL ACCESS ===\n"
                "Use your available tools to complete this task step:\n"
                "  • web_search(query)            — live web search\n"
                "  • view_upcoming_agenda(days_ahead) — calendar events\n"
                "  • recall_memories(query_string) — personal memory store\n"
                "  • get_system_metrics()         — system CPU/RAM/disk\n"
                "  • manage_hyperv_vm(vm_name, action) — VM management\n"
                "  • discover_local_infrastructure() — network scan\n"
                "PREFERRED: use native tool calls.\n"
                "FALLBACK: embed a single tag ([SEARCH:], [VIEW_UPCOMING_AGENDA:], [RECALL:]) if native tools unavailable.\n"
                "Return a concise, factual summary of what you found/did for this task step."
            )
            worker_prompt = f"Execute task step: {task_desc}"

            raw_response = await call_with_persona(worker_prompt, worker_system)

            # Resolve any inline tag fallback the model emitted
            clean_response, tool_data = await _resolve_inline_tags(raw_response, repo)

            if tool_data:
                # Re-run LLM with real tool data injected so it produces a grounded answer
                logger.info(f"[Agent Runner] [{job_id}] Resolved inline tag for step '{task_desc[:40]}', re-running with data.")
                grounded_prompt = (
                    f"System context:\n{tool_data}\n\n"
                    f"Original task step: {task_desc}\n"
                    f"Partial response: {clean_response}\n\n"
                    "Using the tool data above, write a concise factual finding for this task step."
                )
                grounded_system = (
                    f"You are the Worker Engine for Project Vigil. Summarize the tool result "
                    f"concisely as a finding for task: '{task_desc}'. Be factual. Do not repeat the raw data verbatim."
                )
                raw_response = await client.generate_response(
                    prompt=grounded_prompt,
                    system_prompt=grounded_system
                )

            return raw_response.strip()

        start_time = time.time()
        last_status_update = time.time()
        step_count = 0
        plan_list = []
        findings: Dict[str, Any] = {}

        try:
            # ── Step 1: Coordinator — build task plan ─────────────────────
            logger.info(f"[Agent Runner] [{job_id}] Invoking Coordinator Agent...")
            coord_system = (
                "You are 'The Coordinator' for Project Vigil. Analyse the user's request and "
                "produce a step-by-step execution plan. Break it into logical sub-tasks "
                "(e.g. web search, read calendar, check system, compile report).\n"
                "Output STRICTLY as a JSON list: "
                '[{"step": 1, "task": "description"}, ...]\n'
                "No preamble, no post-text. JSON only."
            )
            coord_resp = await call_with_persona(
                f"User request to plan: {request}", coord_system
            )
            step_count += 1

            try:
                clean_json = coord_resp.strip()
                if clean_json.startswith("```"):
                    clean_json = clean_json.split("\n", 1)[1].rsplit("\n", 1)[0].strip()
                if clean_json.lower().startswith("json"):
                    clean_json = clean_json[4:].strip()
                plan_list = json.loads(clean_json)
                if not isinstance(plan_list, list):
                    plan_list = [{"step": 1, "task": request}]
            except Exception:
                logger.warning(f"[Agent Runner] [{job_id}] Coordinator JSON parse failed, using single-step fallback.")
                plan_list = [{"step": 1, "task": request}]

            repo.save_agent_job_state(
                job_id=job_id, status="running",
                plan_json=json.dumps(plan_list),
                findings_json=json.dumps(findings),
                step_count=step_count
            )

            # ── Step 2: Worker Engine — execute each plan step ────────────
            logger.info(f"[Agent Runner] [{job_id}] Worker Engine executing {len(plan_list)} steps...")
            for idx, plan_step in enumerate(plan_list):
                current_task = plan_step.get("task", "")
                step_idx = plan_step.get("step", idx + 1)

                if step_count >= 15:
                    logger.warning(f"[Agent Runner] [{job_id}] Step ceiling reached (15). Stopping early.")
                    findings[f"step_{step_idx}_error"] = "Truncated: 15-step ceiling reached."
                    break

                # Periodic progress update
                if time.time() - last_status_update > 60:
                    update_system = (
                        "You are Project Vigil. Write one brief, warm sentence telling the user "
                        f"you are working on a background task [#{job_id[:8]}]."
                    )
                    update_text = await call_with_persona(
                        f"Working on: {current_task}", update_system
                    )
                    await self.router.send_message(
                        platform=platform, user_id=user_id, text=update_text.strip()
                    )
                    last_status_update = time.time()

                logger.info(f"[Agent Runner] [{job_id}] Executing step {step_idx}: '{current_task}'")
                finding = await execute_worker_step(current_task, findings)
                step_count += 1

                findings[f"step_{step_idx}_result"] = finding
                logger.info(f"[Agent Runner] [{job_id}] Step {step_idx} finding: {finding[:120]}...")

                repo.save_agent_job_state(
                    job_id=job_id, status="running",
                    plan_json=json.dumps(plan_list),
                    findings_json=json.dumps(findings),
                    step_count=step_count
                )

            # ── Step 3: Reviewer — compile and format final report ────────
            logger.info(f"[Agent Runner] [{job_id}] Reviewer/Editor compiling final report...")
            reviewer_system = (
                "You are 'The Reviewer' for Project Vigil. Compile the worker findings into a "
                "clear, warm, well-formatted response for the user. "
                "Be concise but complete. Use markdown where helpful. "
                "Do NOT hallucinate — only use the facts in the findings."
            )
            reviewer_prompt = (
                f"Original request: {request}\n"
                f"Plan executed: {json.dumps(plan_list)}\n"
                f"Worker findings: {json.dumps(findings)}\n\n"
                "Write the final compiled report:"
            )
            final_report = await call_with_persona(reviewer_prompt, reviewer_system)
            step_count += 1

            # Handle [IMAGE:] trigger in final report
            image_match = re.search(r"\[IMAGE:\s*(.*?)\]", final_report)
            local_artifacts: List[str] = []
            clean_report = final_report

            if image_match:
                image_prompt_text = image_match.group(1).strip()
                logger.info(f"[Agent Runner] [{job_id}] Image trigger in report: '{image_prompt_text}'")
                clean_report = re.sub(r"\[IMAGE:\s*(.*?)\]", "", final_report).strip()

                comfy_backend = repo.get_config("comfyui_backend", "mock")
                comfy_url = repo.get_config("comfyui_url", "http://localhost:8188")
                comfy_ckpt = repo.get_config("comfyui_ckpt", "v1-5-pruned-emaonly.safetensors")

                from src.comfyui import ComfyUIClient
                comfy = ComfyUIClient(base_url=comfy_url, backend=comfy_backend, ckpt_name=comfy_ckpt)
                img_bytes = await comfy.generate_image(image_prompt_text)

                if img_bytes:
                    base_data_dir = os.path.join(
                        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ProjectVigil"
                    )
                    os.makedirs(base_data_dir, exist_ok=True)
                    artifact_path = os.path.join(base_data_dir, f"comfy_{job_id}.png")
                    with open(artifact_path, "wb") as f:
                        f.write(img_bytes)
                    local_artifacts.append(artifact_path)

            prefix = ""
            if time.time() - start_time > 60:
                prefix = f"✅ Agent Update [#{job_id[:8]}]: Background task complete.\n\n"

            repo.save_agent_job_state(
                job_id=job_id, status="completed",
                plan_json=json.dumps(plan_list),
                findings_json=json.dumps(findings),
                step_count=step_count,
                artifacts_json=json.dumps(local_artifacts)
            )

            job_state = repo.get_agent_job_state(job_id)
            artifacts: List[str] = []
            if job_state and job_state.artifacts:
                try:
                    artifacts = json.loads(job_state.artifacts)
                except Exception:
                    pass

            repo.save_message(
                channel=platform, user_id=user_id,
                sender_type="bot", text=prefix + clean_report
            )

            logger.info(f"[Agent Runner] [{job_id}] Dispatching final report ({len(artifacts)} artifacts).")
            await self.router.send_job_result(
                platform=platform, user_id=user_id,
                text=prefix + clean_report, artifacts=artifacts
            )

        except Exception as e:
            logger.exception(f"[Agent Runner] [{job_id}] Job failed: {e}")
            repo.save_agent_job_state(
                job_id=job_id, status="failed",
                plan_json=json.dumps(plan_list),
                findings_json=json.dumps(findings),
                step_count=step_count
            )
            await self.router.send_message(
                platform=platform, user_id=user_id,
                text=f"⚠️ Agent job [#{job_id[:8]}] failed: {e}"
            )
        finally:
            db.close()
