import asyncio
import logging
import hashlib
import base64
import json
from io import BytesIO
from datetime import datetime, timezone
from PIL import ImageGrab
from src.repository import MessageRepository
from src.llm import get_llm_client
from src.database import SessionLocal

logger = logging.getLogger("project_vigil.screen_memory")

class ScreenMemoryService:
    def __init__(self):
        self.last_hash = None

    async def run_loop(self):
        logger.info("[ScreenMemory] Starting background screen capture service...")
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            while True:
                try:
                    # 1. Check if enabled
                    enabled = repo.get_config("screen_memory_enabled", "False").lower() == "true"
                    if not enabled:
                        await asyncio.sleep(60)
                        continue
                    
                    interval = int(repo.get_config("screen_memory_interval", "60"))
                    
                    # 2. Capture Engine
                    img = ImageGrab.grab()
                    img = img.convert("RGB") # Ensure consistent mode
                    
                    # Resize to reduce payload and processing time (e.g., max 1024x1024)
                    img.thumbnail((1024, 1024))
                    
                    # 3. Hash Check
                    img_bytes = img.tobytes()
                    current_hash = hashlib.md5(img_bytes).hexdigest()
                    if self.last_hash == current_hash:
                        # User is idle / screen hasn't changed
                        await asyncio.sleep(interval)
                        continue
                    
                    self.last_hash = current_hash
                    
                    # 4. Vision LLM Analysis
                    buffer = BytesIO()
                    img.save(buffer, format="JPEG", quality=85)
                    b64_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
                    
                    backend = repo.get_config("llm_backend", "mock")
                    url = repo.get_config("llm_url", "http://localhost:11434")
                    model = repo.get_config("screen_memory_model", "llama3.2-vision")
                    
                    client = get_llm_client(backend=backend, url=url, model=model)
                    
                    system_prompt = "Analyze this screen capture of the user's desktop. Write a concise, one-sentence description of the exact task, application, or file they are currently working on. Do not include UI fluff."
                    prompt = f"[IMAGE_ATTACHMENT: {b64_data}]\nWhat is the user currently doing on their screen?"
                    
                    response_text = await client.generate_response(prompt=prompt, system_prompt=system_prompt)
                    
                    if not response_text or "Error" in response_text:
                        pass
                    else:
                        # 5. Commit to DB Work Stream
                        platform = repo.get_config("proactive_platform", "mock")
                        user_id = repo.get_config("proactive_user_id", "mock_user_1")
                        
                        screen_context = {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "source": "screen_recorder",
                            "captured_activity": response_text.strip()
                        }
                        
                        repo.save_message(
                            channel=platform,
                            user_id=user_id,
                            sender_type="system",
                            text=json.dumps(screen_context)
                        )
                        logger.info(f"[ScreenMemory] Logged screen context: {response_text.strip()}")
                    
                    await asyncio.sleep(interval)
                
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"[ScreenMemory] Error in background loop: {e}")
                    await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass
        finally:
            db.close()
