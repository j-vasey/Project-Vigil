import asyncio
import logging
import os
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.database import SessionLocal
from src.repository import MessageRepository
from src.router import MessagingRouter

logger = logging.getLogger("project_vigil.api")

# --- Log Streaming Handler ---
class SSELogHandler(logging.Handler):
    """
    Custom logging handler that broadcasts log lines to subscribed asyncio queues.
    """
    def __init__(self):
        super().__init__()
        self.subscribers = []

    def emit(self, record: logging.LogRecord):
        try:
            log_entry = self.format(record)
            for queue in list(self.subscribers):
                try:
                    queue.put_nowait(log_entry)
                except asyncio.QueueFull:
                    # Drop line if queue is saturated
                    pass
                except Exception:
                    self.subscribers.remove(queue)
        except Exception:
            self.handleError(record)

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self.subscribers:
            self.subscribers.remove(q)

# Global logging handler for SSE
sse_log_handler = SSELogHandler()
sse_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger("project_vigil").addHandler(sse_log_handler)
logging.getLogger("test_messaging").addHandler(sse_log_handler)


# --- API Models ---
class ConfigUpdate(BaseModel):
    configs: Dict[str, str]


class LLMTestPayload(BaseModel):
    backend: str
    url: str
    model: str


class ManualMessagePayload(BaseModel):
    platform: str
    user_id: str
    text: str


class TempPausePayload(BaseModel):
    duration_minutes: int


class SchedulerRulesPayload(BaseModel):
    interval_seconds: int = None
    proactive_probability: float = None
    proactive_jitter_percentage: float = None
    dnd_start: str = None
    dnd_end: str = None


class M365ConfigPayload(BaseModel):
    client_id: str
    tenant_id: str = "common"
    client_secret: Optional[str] = ""


class M365CallbackPayload(BaseModel):
    code: str
    redirect_uri: str


class M365PollPayload(BaseModel):
    device_code: str


class MemoryPayload(BaseModel):
    fact: str
    category: str


class MemoryUpdatePayload(BaseModel):
    id: int
    fact: str
    category: str


import socket

def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_m365_redirect_uri(repo) -> str:
    url_root = repo.get_config("url_root", "")
    if url_root:
        return f"{url_root.rstrip('/')}/api/auth/m365/callback"
    custom_uri = repo.get_config("m365_redirect_uri", "")
    if custom_uri:
        return custom_uri
    base_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ProjectVigil')
    cert_path = os.path.join(base_data_dir, "cert.pem")
    key_path = os.path.join(base_data_dir, "key.pem")
    cert_exists = os.path.exists(cert_path) and os.path.exists(key_path)
    scheme = "https" if cert_exists else "http"
    host_ip = get_lan_ip()
    return f"{scheme}://{host_ip}:8001/api/auth/m365/callback"


# --- FastAPI App Setup ---
def create_app(router: MessagingRouter) -> FastAPI:
    app = FastAPI(title="Project Vigil Control Plane Gateway", version="1.0.0")

    # Enable CORS for local development of WebUI
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Database dependency helper
    def get_repo():
        db = SessionLocal()
        try:
            yield MessageRepository(db)
        finally:
            db.close()

    # --- Endpoints ---
    @app.get("/api/health")
    async def get_health():
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            health_status = repo.get_config("system_health", "healthy")
            proactivity_logs = repo.get_recent_proactivity_logs(limit=10)
            
            # Formulate list response
            logs_list = [{
                "id": log.id,
                "execution_time": log.execution_time.isoformat(),
                "reason_code": log.reason_code,
                "message_dispatched": log.message_dispatched
            } for log in proactivity_logs]
            
            return {
                "status": "healthy",
                "engine_status": health_status,
                "recent_proactivity": logs_list
            }
        finally:
            db.close()

    @app.post("/api/health/toggle")
    async def toggle_health():
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            current_status = repo.get_config("system_health", "healthy")
            new_status = "paused" if current_status == "healthy" else "healthy"
            repo.set_config("system_health", new_status)
            logger.info(f"[API] Toggled system_health status to '{new_status}'")
            return {"status": "success", "engine_status": new_status}
        finally:
            db.close()

    @app.get("/api/config")
    async def get_configs():
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            return repo.get_all_configs()
        finally:
            db.close()

    @app.post("/api/config")
    async def update_configs(update: ConfigUpdate):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            for key, val in update.configs.items():
                repo.set_config(key, val)
            logger.info("[API] System configurations updated successfully")
            
            # Dynamically update messaging providers with new tokens in real-time
            tg_token = repo.get_config("telegram_token", "")
            if tg_token and tg_token.strip():
                try:
                    from src.providers.telegram import TelegramProvider
                    router.register_provider("telegram", TelegramProvider(token=tg_token))
                    logger.info("[API] Dynamically updated Telegram bot provider connection.")
                except Exception as err:
                    logger.error(f"[API] Failed to update Telegram provider: {err}")
            
            discord_token = repo.get_config("discord_token", "")
            if discord_token and discord_token.strip():
                try:
                    from src.providers.discord import DiscordProvider
                    router.register_provider("discord", DiscordProvider(token=discord_token))
                    logger.info("[API] Dynamically updated Discord bot provider connection.")
                except Exception as err:
                    logger.error(f"[API] Failed to update Discord provider: {err}")

            twilio_sid = repo.get_config("twilio_account_sid", "")
            twilio_token = repo.get_config("twilio_auth_token", "")
            twilio_number = repo.get_config("twilio_number", "")
            if (twilio_sid and twilio_sid.strip()) or (twilio_token and twilio_token.strip()) or (twilio_number and twilio_number.strip()):
                try:
                    from src.providers.twilio import TwilioProvider
                    twilio_prov = TwilioProvider(
                        account_sid=twilio_sid,
                        auth_token=twilio_token,
                        twilio_number=twilio_number
                    )
                    router.register_provider("twilio", twilio_prov)
                    router.register_provider("whatsapp", twilio_prov)
                    logger.info("[API] Dynamically updated Twilio/WhatsApp provider connection.")
                except Exception as err:
                    logger.error(f"[API] Failed to update Twilio/WhatsApp provider: {err}")
                    
            return {"status": "success", "configs": repo.get_all_configs()}
        finally:
            db.close()

    @app.get("/api/logs/stream")
    async def stream_logs(request: Request):
        """
        Server-Sent Events endpoint streaming backend log entries in real-time.
        """
        async def log_generator():
            q = sse_log_handler.subscribe()
            try:
                # Send an initial ping to establish connection
                yield "data: [SYSTEM] Connected to Project Vigil Real-time Log Stream\n\n"
                while True:
                    # Check connection alive
                    if await request.is_disconnected():
                        break
                    try:
                        # Non-blocking check with timeout to periodically verify client connection
                        log_line = await asyncio.wait_for(q.get(), timeout=1.0)
                        yield f"data: {log_line}\n\n"
                    except asyncio.TimeoutError:
                        continue
            except asyncio.CancelledError:
                pass
            finally:
                sse_log_handler.unsubscribe(q)

        return StreamingResponse(log_generator(), media_type="text/event-stream")

    @app.post("/webhook/{platform}")
    async def receive_webhook(platform: str, payload: dict):
        """
        General webhook endpoint. Passes raw payload to router which enqueues 
        it out-of-band to safeguard against slow processing times.
        """
        logger.info(f"[API] Received webhook callback for platform '{platform}'")
        try:
            # Router parses, triggers inbound callback, enqueues instantly, and returns
            await router.handle_webhook(platform, payload)
            return {"status": "success", "message": "Payload queued"}
        except KeyError:
            logger.error(f"[API] No messaging provider registered for platform '{platform}'")
            raise HTTPException(status_code=400, detail=f"Unsupported platform: '{platform}'")
        except Exception as e:
            logger.exception(f"[API] Error handling webhook for '{platform}': {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/ollama/models")
    async def get_ollama_models(url: str = None):
        """
        Connects to Ollama tags endpoint and retrieves available models.
        """
        import httpx
        if not url:
            db = SessionLocal()
            try:
                repo = MessageRepository(db)
                url = repo.get_config("llm_url", "http://localhost:11434")
            finally:
                db.close()
                
        url = url.rstrip("/")
        tags_url = f"{url}/api/tags"
        logger.info(f"[API] Fetching Ollama models from: {tags_url}")
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(tags_url, timeout=5.0)
                if resp.status_code == 200:
                    models_data = resp.json()
                    models_list = [m.get("name") for m in models_data.get("models", []) if m.get("name")]
                    return {"status": "success", "models": models_list}
                else:
                    raise HTTPException(status_code=400, detail=f"Ollama returned status code {resp.status_code}")
        except httpx.ConnectError:
            raise HTTPException(status_code=400, detail="Could not connect to Ollama. Verify URL and make sure Ollama is running.")
        except Exception as e:
            logger.error(f"[API] Failed to fetch Ollama models: {e}")
            raise HTTPException(status_code=400, detail=f"Error connecting to Ollama: {str(e)}")

    @app.get("/api/comfyui/checkpoints")
    async def get_comfyui_checkpoints(url: str = None):
        """
        Connects to ComfyUI's object_info endpoint and retrieves list of available checkpoints.
        """
        import httpx
        if not url:
            db = SessionLocal()
            try:
                repo = MessageRepository(db)
                url = repo.get_config("comfyui_url", "http://localhost:8188")
            finally:
                db.close()
                
        # If url indicates a mock, return mock checkpoints list
        if "mock" in url.lower() or url.strip() == "":
            return {
                "status": "success",
                "checkpoints": [
                    "v1-5-pruned-emaonly.safetensors",
                    "sd_xl_base_1.0.safetensors",
                    "protogen_x5.8.safetensors"
                ]
            }

        url = url.rstrip("/")
        object_info_url = f"{url}/object_info"
        logger.info(f"[API] Fetching ComfyUI checkpoints from: {object_info_url}")
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(object_info_url, timeout=5.0)
                if resp.status_code == 200:
                    info = resp.json()
                    loader = info.get("CheckpointLoaderSimple") or info.get("CheckpointLoader")
                    if loader:
                        ckpt_names = loader.get("input", {}).get("required", {}).get("ckpt_name", [[]])[0]
                        return {"status": "success", "checkpoints": ckpt_names}
                    else:
                        return {"status": "success", "checkpoints": []}
                else:
                    raise HTTPException(status_code=400, detail=f"ComfyUI returned status code {resp.status_code}")
        except httpx.ConnectError:
            # Fallback to mock list for offline/mock testing to keep UI useful
            logger.warning("[API] Could not connect to ComfyUI. Returning mock checkpoint options.")
            return {
                "status": "success",
                "checkpoints": [
                    "v1-5-pruned-emaonly.safetensors",
                    "sd_xl_base_1.0.safetensors",
                    "protogen_x5.8.safetensors"
                ]
            }
        except Exception as e:
            logger.error(f"[API] Failed to fetch ComfyUI checkpoints: {e}")
            raise HTTPException(status_code=400, detail=f"Error connecting to ComfyUI: {str(e)}")

    @app.post("/api/llm/test")
    async def test_llm_connection(payload: LLMTestPayload):
        """
        Tests connection to the specified LLM configuration.
        """
        from src.llm import get_llm_client
        logger.info(f"[API] Testing LLM connection: backend={payload.backend}, url={payload.url}, model={payload.model}")
        
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            num_ctx_str = repo.get_config("llm_num_ctx", "8192")
            try:
                num_ctx = int(num_ctx_str)
            except ValueError:
                num_ctx = 8192
        finally:
            db.close()
            
        try:
            client = get_llm_client(backend=payload.backend, url=payload.url, model=payload.model, num_ctx=num_ctx)
            test_prompt = "Say hello! Respond with a brief greeting."
            test_system = "You are a connection testing helper."
            
            response = await asyncio.wait_for(
                client.generate_response(prompt=test_prompt, system_prompt=test_system),
                timeout=15.0
            )
            return {"status": "success", "response": response}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=408, detail="LLM request timed out. Make sure the backend is responding quickly.")
        except Exception as e:
            logger.error(f"[API] LLM connection test failed: {e}")
            raise HTTPException(status_code=400, detail=f"Connection test failed: {str(e)}")

    @app.post("/api/manual/send")
    async def manual_send_message(payload: ManualMessagePayload):
        """
        Manually dispatches a message (text or image trigger) through the gateway.
        """
        logger.info(f"[API] Request for manual message send to {payload.user_id} on '{payload.platform}'")
        
        import re
        image_match = re.search(r"\[IMAGE:\s*(.*?)\]", payload.text)
        
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            if image_match:
                image_prompt = image_match.group(1).strip()
                clean_text = re.sub(r"\[IMAGE:\s*(.*?)\]", "", payload.text).strip()
                
                logger.info(f"[API Manual Send] Found image trigger. Prompt: '{image_prompt}'")
                
                comfy_backend = repo.get_config("comfyui_backend", "mock")
                comfy_url = repo.get_config("comfyui_url", "http://localhost:8188")
                comfy_ckpt = repo.get_config("comfyui_ckpt", "v1-5-pruned-emaonly.safetensors")
                
                from src.comfyui import ComfyUIClient
                comfy_client = ComfyUIClient(base_url=comfy_url, backend=comfy_backend, ckpt_name=comfy_ckpt)
                img_bytes = await comfy_client.generate_image(image_prompt)
                
                # Save manual message to history
                repo.save_message(
                    channel=payload.platform,
                    user_id=payload.user_id,
                    sender_type="bot",
                    text=f"[IMAGE: {image_prompt}] {clean_text}"
                )
                
                if img_bytes:
                    sent = await router.send_image(
                        platform=payload.platform,
                        user_id=payload.user_id,
                        image_bytes=img_bytes,
                        filename="manual_generation.png",
                        caption=clean_text
                    )
                else:
                    logger.error("[API Manual Send] ComfyUI returned empty bytes. Falling back to text send.")
                    sent = await router.send_message(
                        platform=payload.platform,
                        user_id=payload.user_id,
                        text=clean_text or f"[Image prompt: '{image_prompt}']"
                    )
            else:
                # Save manual message to history
                repo.save_message(
                    channel=payload.platform,
                    user_id=payload.user_id,
                    sender_type="bot",
                    text=payload.text
                )
                sent = await router.send_message(
                    platform=payload.platform,
                    user_id=payload.user_id,
                    text=payload.text
                )
                
            if sent:
                return {"status": "success", "message": "Message sent successfully"}
            else:
                raise HTTPException(status_code=500, detail="Failed to route manual message via gateway provider.")
        except Exception as e:
            logger.exception(f"[API] Manual message sending failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            db.close()

    # --- MCP Status Endpoint ---
    @app.get("/api/mcp/status")
    async def get_mcp_status():
        from src.mcp.client import mcp_manager
        return mcp_manager.get_status()

    def get_workspace_paths_list(repo: MessageRepository) -> list:
        workspace_path = repo.get_config("workspace_path", "")
        if workspace_path:
            raw_paths = workspace_path.split(",")
            paths = []
            for p in raw_paths:
                p_str = p.strip().strip("'\"")
                if p_str:
                    paths.append(os.path.abspath(p_str))
            if paths:
                return paths
        return [os.path.abspath(os.getcwd())]

    def get_safe_workspace_path(repo: MessageRepository, rel_path: str = ".") -> str:
        base_dirs = get_workspace_paths_list(repo)
        primary_base = base_dirs[0]
        
        if os.path.isabs(rel_path) or (len(rel_path) > 1 and rel_path[1] == ":"):
            abs_target = os.path.abspath(rel_path)
        else:
            abs_target = os.path.abspath(os.path.join(primary_base, rel_path.lstrip("/\\")))
            
        allowed = False
        for base_dir in base_dirs:
            try:
                if os.path.commonpath([base_dir, abs_target]) == base_dir:
                    allowed = True
                    break
            except ValueError:
                pass
                
        if not allowed:
            raise HTTPException(status_code=403, detail="Directory Traversal Blocked: Path does not lie inside allowed workspace paths.")
        return abs_target

    @app.get("/api/workspace/files")
    async def list_workspace_files(path: str = "."):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            base_dirs = get_workspace_paths_list(repo)
            
            # Virtual root: if multiple directories are configured and user queries '.', list workspaces
            if path == "." and len(base_dirs) > 1:
                entries = []
                for b in base_dirs:
                    entries.append({
                        "name": b,
                        "type": "directory",
                        "size_bytes": 0
                    })
                return entries
                
            target = get_safe_workspace_path(repo, path)
            if not os.path.isdir(target):
                raise HTTPException(status_code=400, detail=f"Directory '{path}' does not exist.")
            
            entries = []
            for item in os.listdir(target):
                full = os.path.join(target, item)
                is_dir = os.path.isdir(full)
                entries.append({
                    "name": item,
                    "type": "directory" if is_dir else "file",
                    "size_bytes": os.path.getsize(full) if not is_dir else 0
                })
            return entries
        finally:
            db.close()

    @app.post("/api/workspace/files")
    async def upload_workspace_file(path: str = Form("."), file: UploadFile = File(...)):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            target_dir = get_safe_workspace_path(repo, path)
            os.makedirs(target_dir, exist_ok=True)
            
            target_file = os.path.join(target_dir, file.filename)
            content = await file.read()
            with open(target_file, "wb") as f:
                f.write(content)
            return {"status": "success", "message": f"File '{file.filename}' uploaded successfully."}
        finally:
            db.close()

    @app.delete("/api/workspace/files")
    async def delete_workspace_file(path: str):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            target = get_safe_workspace_path(repo, path)
            if not os.path.exists(target):
                raise HTTPException(status_code=404, detail="File or directory not found.")
            if os.path.isdir(target):
                import shutil
                shutil.rmtree(target)
            else:
                os.remove(target)
            return {"status": "success", "message": f"Deleted successfully."}
        finally:
            db.close()

    # --- Scheduler Rules Endpoints ---
    @app.get("/api/scheduler/rules")
    async def get_scheduler_rules():
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            configs = repo.get_all_configs()
            logs = repo.get_recent_proactivity_logs(limit=50)
            log_entries = []
            for log in logs:
                log_entries.append({
                    "id": log.id,
                    "execution_time": log.execution_time.isoformat(),
                    "reason_code": log.reason_code,
                    "message_dispatched": log.message_dispatched
                })
            return {"configs": configs, "history": log_entries}
        finally:
            db.close()

    @app.post("/api/scheduler/rules")
    async def pause_outreach_engine(payload: TempPausePayload):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            from datetime import datetime, timedelta
            paused_until = datetime.utcnow() + timedelta(minutes=payload.duration_minutes)
            paused_until_iso = paused_until.isoformat()
            repo.set_config("proactivity_paused_until", paused_until_iso)
            return {"status": "success", "message": f"Outreach paused until {paused_until_iso} UTC."}
        finally:
            db.close()

    @app.put("/api/scheduler/rules")
    async def update_scheduler_rules(payload: SchedulerRulesPayload):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            if payload.interval_seconds is not None:
                repo.set_config("proactive_interval_seconds", str(payload.interval_seconds))
            if payload.proactive_probability is not None:
                repo.set_config("proactive_probability", str(payload.proactive_probability))
            if payload.proactive_jitter_percentage is not None:
                repo.set_config("proactive_jitter_percentage", str(payload.proactive_jitter_percentage))
            if payload.dnd_start is not None:
                repo.set_config("dnd_start", payload.dnd_start)
            if payload.dnd_end is not None:
                repo.set_config("dnd_end", payload.dnd_end)
            return {"status": "success", "message": "Scheduler configurations updated successfully."}
        finally:
            db.close()

    # --- M365 Authentication Endpoints ---
    @app.get("/api/auth/m365/config")
    async def get_m365_config():
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            return {
                "client_id": repo.get_config("m365_client_id", ""),
                "tenant_id": repo.get_config("m365_tenant_id", "common"),
                "client_secret": repo.get_config("m365_client_secret", ""),
                "is_authorized": bool(repo.get_config("m365_access_token", "")),
                "redirect_uri": get_m365_redirect_uri(repo)
            }
        finally:
            db.close()

    @app.post("/api/auth/m365/config")
    async def save_m365_config(payload: M365ConfigPayload):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            repo.set_config("m365_client_id", payload.client_id)
            repo.set_config("m365_tenant_id", payload.tenant_id)
            repo.set_config("m365_client_secret", payload.client_secret or "")
            return {"status": "success", "message": "M365 configurations saved successfully."}
        finally:
            db.close()

    @app.get("/api/auth/m365/authorize")
    async def m365_authorize(redirect_uri: Optional[str] = None):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            client_id = repo.get_config("m365_client_id", "")
            tenant_id = repo.get_config("m365_tenant_id", "common")
            
            r_uri = redirect_uri or get_m365_redirect_uri(repo)
            
            url = (
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
                f"?client_id={client_id}"
                f"&response_type=code"
                f"&redirect_uri={r_uri}"
                f"&response_mode=query"
                f"&scope=https://graph.microsoft.com/Calendars.ReadWrite%20offline_access"
            )
            return {"authorize_url": url}
        finally:
            db.close()

    @app.get("/api/auth/m365/callback")
    async def m365_callback(
        code: Optional[str] = None,
        error: Optional[str] = None,
        error_description: Optional[str] = None,
        state: Optional[str] = None
    ):
        from fastapi.responses import HTMLResponse
        if not code:
            err_msg = error_description or error or "No authorization code was supplied by Microsoft."
            error_html = f"""
            <html>
                <head>
                    <title>Authentication Failed</title>
                    <style>
                        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #f8fafc; text-align: center; padding-top: 50px; }}
                        h1 {{ color: #f87171; }}
                        .container {{ background-color: #1e293b; border: 1px solid #334155; display: inline-block; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>Vigil Link Authorization Failed</h1>
                        <p>{err_msg}</p>
                        <p style="color: #94a3b8;">You may close this browser tab and try linking again from settings.</p>
                    </div>
                </body>
            </html>
            """
            return HTMLResponse(content=error_html, status_code=400)

        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            client_id = repo.get_config("m365_client_id", "")
            client_secret = repo.get_config("m365_client_secret", "")
            tenant_id = repo.get_config("m365_tenant_id", "common")
            
            import httpx
            from fastapi.responses import HTMLResponse
            
            r_uri = get_m365_redirect_uri(repo)
            
            url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
            data = {
                "client_id": client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": r_uri,
                "scope": "https://graph.microsoft.com/Calendars.ReadWrite offline_access"
            }
            if client_secret:
                data["client_secret"] = client_secret
                
            response = httpx.post(url, data=data)
            resp_data = response.json()
            if response.status_code != 200:
                error_msg = resp_data.get("error_description", "OAuth exchange failed.")
                return HTMLResponse(
                    content=f"<h3>Authentication Failed</h3><p>{error_msg}</p>",
                    status_code=400
                )
                
            new_access = resp_data["access_token"]
            new_refresh = resp_data.get("refresh_token", "")
            expires_in = resp_data["expires_in"]
            
            from datetime import datetime, timedelta
            new_expiry = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()
            
            repo.set_config("m365_access_token", new_access)
            if new_refresh:
                repo.set_config("m365_refresh_token", new_refresh)
            repo.set_config("m365_token_expiry", new_expiry)
            
            success_html = """
            <html>
                <head>
                    <title>Authentication Successful</title>
                    <style>
                        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #f8fafc; text-align: center; padding-top: 50px; }
                        h1 { color: #a78bfa; }
                        .container { background-color: #1e293b; border: 1px solid #334155; display: inline-block; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>Vigil Authentication Successful</h1>
                        <p>Your Microsoft M365 Outlook Account has been successfully linked to Project Vigil.</p>
                        <p style="color: #94a3b8;">You may now close this browser tab.</p>
                    </div>
                </body>
            </html>
            """
            return HTMLResponse(content=success_html, status_code=200)
        finally:
            db.close()

    # --- Searchable Memory Editor Endpoints ---
    @app.get("/api/memory")
    async def get_memories(query: str = ""):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            rows = repo.search_memories(query)
            return [{"id": r.id, "fact": r.fact, "category": r.category, "timestamp": r.timestamp.isoformat()} for r in rows]
        finally:
            db.close()

    @app.post("/api/memory")
    async def inject_memory(payload: MemoryPayload):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            memory = repo.save_memory(fact=payload.fact, category=payload.category)
            return {"status": "success", "id": memory.id, "message": "Memory fact successfully saved."}
        finally:
            db.close()

    @app.put("/api/memory")
    async def edit_memory(payload: MemoryUpdatePayload):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            memory = repo.save_memory(fact=payload.fact, category=payload.category, memory_id=payload.id)
            return {"status": "success", "id": memory.id, "message": "Memory fact successfully updated."}
        finally:
            db.close()

    @app.delete("/api/memory")
    async def delete_memory(id: int):
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            deleted = repo.delete_memory(id)
            if deleted:
                return {"status": "success", "message": "Memory deleted successfully."}
            raise HTTPException(status_code=404, detail="Memory not found.")
        finally:
            db.close()

    # --- Serving WebUI Frontend ---
    # Mount Vite production build static assets if compile directory exists
    import sys
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.getcwd()
    dist_path = os.path.join(base_path, "webui", "dist")
    if os.path.exists(dist_path):
        logger.info(f"Serving WebUI frontend files from: {dist_path}")
        app.mount("/", StaticFiles(directory=dist_path, html=True), name="static")
    else:
        logger.warning(f"Frontend dist folder not found at {dist_path}. Run frontend build first.")

    return app
