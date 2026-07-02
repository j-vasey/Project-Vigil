import asyncio
import json
import logging
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

class BackgroundAgentRunner:
    """
    Manages long-running stateful multi-agent jobs executing in background workers.
    Checkpoints task plans and findings after every turn to SQLite database.
    """
    def __init__(self, router: MessagingRouter):
        self.router = router

    def start_job(self, platform: str, user_id: str, request: str, system_prompt: str) -> str:
        """
        Immediately spawns and returns a unique job_id for a long-running task.
        """
        job_id = str(uuid.uuid4())
        
        # Initialize job state in DB
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
            
        # Spawn asynchronous background task
        asyncio.create_task(self._run_job_loop(job_id, platform, user_id, request, system_prompt))
        return job_id

    async def _run_job_loop(self, job_id: str, platform: str, user_id: str, request: str, base_system_prompt: str):
        logger.info(f"[Agent Runner] Starting background job {job_id} for user {user_id} on {platform}...")
        
        db = SessionLocal()
        repo = MessageRepository(db)
        
        backend = repo.get_config("llm_backend", "mock")
        url = repo.get_config("llm_url", "http://localhost:11434")
        model = repo.get_config("llm_model", "gemma-4-26B-A-4B-it-UD-Q3_K_M:latest")
        num_ctx_str = repo.get_config("llm_num_ctx", "8192")
        try:
            num_ctx = int(num_ctx_str)
        except ValueError:
            num_ctx = 8192
        client = get_llm_client(backend=backend, url=url, model=model, num_ctx=num_ctx)
        
        # Centralized LLM caller that forcefully prepends active companion settings and guidelines on every call
        async def call_llm_with_persona(prompt: str, sub_system: str) -> str:
            system_prompt_db = repo.get_config("system_prompt", "You are a helpful, empathetic local AI companion named Project Vigil. Keep responses concise, warm, and supportive.")
            guidelines_db = repo.get_config("behavioral_guidelines", "")
            user_habit_db = repo.get_config("user_habit", "")
            
            absolute_persona = (
                f"PRIMARY COMPANION PERSONALITY SYSTEM RULES:\n{system_prompt_db}\n\n"
                f"BEHAVIORAL GUIDELINES:\n{guidelines_db}\n\n"
                f"USER BEHAVIORAL TREND SUMMARY:\n{user_habit_db}\n\n"
                "--- END OF COMPANION PERSONALITY RULES ---\n\n"
            )
            full_system = absolute_persona + sub_system
            return await client.generate_response(prompt=prompt, system_prompt=full_system)
            
        start_time = time.time()
        last_status_update = time.time()
        step_count = 0
        plan_list = []
        findings = {}
        
        try:
            # -------------------------------------------------------------
            # Step 1: The Coordinator (Breaks request into task plans)
            # -------------------------------------------------------------
            logger.info(f"[Agent Runner] [{job_id}] Invoking Coordinator Agent...")
            coord_system = (
                "You are 'The Coordinator' agent for Project Vigil. Your role is to analyze the user's request "
                "and construct a detailed, step-by-step task plan to fulfill it. "
                "Break the request down into logical sub-tasks (e.g. read files, fetch metrics, update calendar). "
                "Output your plan STRICTLY as a JSON list of objects: [{\"step\": 1, \"task\": \"description of step 1\"}, ...] "
                "with no conversational preambles or post-text."
            )
            coord_prompt = f"User request to break down: {request}"
            
            coord_resp = await call_llm_with_persona(coord_prompt, coord_system)
            step_count += 1
            
            # Attempt to parse plan
            try:
                # Strip out potential markdown code fences if LLM generated them
                clean_json = coord_resp.strip()
                if clean_json.startswith("```"):
                    clean_json = clean_json.split("\n", 1)[1].rsplit("\n", 1)[0].strip()
                if clean_json.startswith("json"):
                    clean_json = clean_json[4:].strip()
                plan_list = json.loads(clean_json)
                if not isinstance(plan_list, list):
                    plan_list = [{"step": 1, "task": coord_resp}]
            except Exception:
                logger.warning(f"[Agent Runner] [{job_id}] Coordinator plan failed to parse JSON. Falling back.")
                plan_list = [{"step": 1, "task": request}]
                
            # Checkpoint coordinator state
            repo.save_agent_job_state(
                job_id=job_id,
                status="running",
                plan_json=json.dumps(plan_list),
                findings_json=json.dumps(findings),
                step_count=step_count
            )
            
            # -------------------------------------------------------------
            # Step 2: The Worker Engine (Executes tools and gathers findings)
            # -------------------------------------------------------------
            logger.info(f"[Agent Runner] [{job_id}] Worker Engine starting execution of {len(plan_list)} plan steps...")
            for idx, plan_step in enumerate(plan_list):
                current_task = plan_step.get("task", "")
                step_idx = plan_step.get("step", idx + 1)
                
                # Check absolute ceiling: 15 sequential model steps max
                if step_count >= 15:
                    logger.warning(f"[Agent Runner] [{job_id}] Aborting job: ceiling limit of 15 turns reached.")
                    findings[f"step_{step_idx}_error"] = "Execution truncated: loop limit ceiling of 15 runs reached."
                    break
                    
                # Check time-based updates (if longer than 60 seconds)
                if time.time() - last_status_update > 60:
                    logger.info(f"[Agent Runner] [{job_id}] Sending periodic progress update...")
                    update_system = (
                        "You are Project Vigil, a warm, helpful local AI companion. "
                        "Write a single, very brief, warm status update sentence informing the user you are currently working on a sub-task. "
                        f"Incorporate the job token: [#{job_id[:8]}] naturally in the text."
                    )
                    update_prompt = f"Sub-task in progress: {current_task}"
                    update_text = await call_llm_with_persona(update_prompt, update_system)
                    
                    await self.router.send_message(
                        platform=platform,
                        user_id=user_id,
                        text=update_text.strip()
                    )
                    last_status_update = time.time()
                    
                worker_system = (
                    f"You are 'The Worker Engine' agent for Project Vigil. Your job is to execute the current task: '{current_task}'. "
                    f"Here is the context of what we have done so far (findings): {json.dumps(findings)}. "
                    "Use any of your available tools (system metrics, calendar, files, etc.) to perform the task. "
                    "Return a concise, clean summary of your action findings for this task."
                )
                worker_prompt = f"Perform task: {current_task}"
                
                logger.info(f"[Agent Runner] [{job_id}] Worker executing step {step_idx}: '{current_task}'")
                worker_resp = await call_llm_with_persona(worker_prompt, worker_system)
                step_count += 1
                
                findings[f"step_{step_idx}_result"] = worker_resp
                
                # Checkpoint progress after every turn
                repo.save_agent_job_state(
                    job_id=job_id,
                    status="running",
                    plan_json=json.dumps(plan_list),
                    findings_json=json.dumps(findings),
                    step_count=step_count
                )
                
            # -------------------------------------------------------------
            # Step 3: The Reviewer/Editor (Validates and compiles output)
            # -------------------------------------------------------------
            logger.info(f"[Agent Runner] [{job_id}] Invoking Reviewer/Editor...")
            reviewer_system = (
                "You are 'The Reviewer/Editor' agent for Project Vigil. Your job is to compile, validate, and edit the final response "
                "for the user based on the original request, the execution plan, and all findings retrieved by the Worker Engine. "
                "Ensure the response is comprehensive, empathetic, and beautifully formatted in markdown."
            )
            reviewer_prompt = (
                f"Original User Request: {request}\n"
                f"Plan Executed: {json.dumps(plan_list)}\n"
                f"Worker Findings: {json.dumps(findings)}\n"
                "Please write the final compiled report:"
            )
            
            final_report = await call_llm_with_persona(reviewer_prompt, reviewer_system)
            step_count += 1
            
            # Check for [IMAGE: image prompt] trigger
            import re
            image_match = re.search(r"\[IMAGE:\s*(.*?)\]", final_report)
            
            # If the job took a long time, prepend status completed message
            prefix = ""
            if time.time() - start_time > 60:
                prefix = f"Vigil Agent Update [#{job_id[:8]}]: Completed background task. Final report:\n\n"
                
            local_artifacts = []
            clean_report = final_report
            
            if image_match:
                image_prompt = image_match.group(1).strip()
                logger.info(f"[Agent Runner] [{job_id}] Found image trigger in final report. Prompt: '{image_prompt}'")
                clean_report = re.sub(r"\[IMAGE:\s*(.*?)\]", "", final_report).strip()
                
                # Fetch ComfyUI configurations
                comfy_backend = repo.get_config("comfyui_backend", "mock")
                comfy_url = repo.get_config("comfyui_url", "http://localhost:8188")
                comfy_ckpt = repo.get_config("comfyui_ckpt", "v1-5-pruned-emaonly.safetensors")
                
                from src.comfyui import ComfyUIClient
                comfy_client = ComfyUIClient(base_url=comfy_url, backend=comfy_backend, ckpt_name=comfy_ckpt)
                img_bytes = await comfy_client.generate_image(image_prompt)
                
                if img_bytes:
                    os.makedirs("artifacts_gen", exist_ok=True)
                    artifact_path = os.path.abspath(f"artifacts_gen/comfy_{job_id}.png")
                    with open(artifact_path, "wb") as f:
                        f.write(img_bytes)
                    local_artifacts.append(artifact_path)
                    logger.info(f"[Agent Runner] [{job_id}] Saved generated image to local artifact ledger path: {artifact_path}")
            
            # Checkpoint completed state with artifacts
            repo.save_agent_job_state(
                job_id=job_id,
                status="completed",
                plan_json=json.dumps(plan_list),
                findings_json=json.dumps(findings),
                step_count=step_count,
                artifacts_json=json.dumps(local_artifacts)
            )
            
            # Retrieve both final text report AND accumulated artifacts from the database ledger
            job_state = repo.get_agent_job_state(job_id)
            artifacts = []
            if job_state and job_state.artifacts:
                try:
                    artifacts = json.loads(job_state.artifacts)
                except Exception:
                    pass
            
            # Save generated response to Database
            repo.save_message(
                channel=platform,
                user_id=user_id,
                sender_type="bot",
                text=prefix + clean_report
            )
            
            # Dispatch final text and file paths in a single combined call to Messaging Hub
            logger.info(f"[Agent Runner] [{job_id}] Dispatching compiled report and {len(artifacts)} files to Messaging Hub.")
            await self.router.send_job_result(
                platform=platform,
                user_id=user_id,
                text=prefix + clean_report,
                artifacts=artifacts
            )
            
        except Exception as e:
            logger.exception(f"[Agent Runner] [{job_id}] Failed executing agent job loop: {e}")
            repo.save_agent_job_state(
                job_id=job_id,
                status="failed",
                plan_json=json.dumps(plan_list),
                findings_json=json.dumps(findings),
                step_count=step_count
            )
            await self.router.send_message(
                platform=platform,
                user_id=user_id,
                text=f"Error: Asynchronous agent job [#{job_id[:8]}] failed due to execution crash: {e}"
            )
        finally:
            db.close()
