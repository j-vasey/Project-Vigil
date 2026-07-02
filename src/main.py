import asyncio
import logging
import os
import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager

from src.database import init_db, SessionLocal
from src.repository import MessageRepository
from src.router import MessagingRouter
from src.providers.mock import MockTestProvider
from src.providers.telegram import TelegramProvider
from src.orchestrator import start_queue_worker, enqueue_inbound_message
from src.proactivity import start_proactivity_engine
from src.api import create_app

# Root level configurations
import sys
# Establish a writable user-scoped AppData directory for all runtime writes
base_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ProjectVigil')
os.makedirs(base_data_dir, exist_ok=True)
LOG_FILE = os.path.join(base_data_dir, "project_vigil.log")

handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
if sys.stderr is not None:
    handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=handlers
)
logger = logging.getLogger("project_vigil.main")
logger.info(f"Logging initialized. Log file at: {LOG_FILE}")

# Messaging router instances
router = MessagingRouter()

# Store tasks globally to prevent Python's garbage collection from reaping active loops
background_tasks = set()

async def start_telegram_polling(router: MessagingRouter) -> None:
    """
    Background long-polling task for receiving Telegram updates in local/offline environments.
    Translates fetched messages to webhooks routed internally.
    """
    logger.info("[Telegram Polling] Starting background getUpdates worker...")
    offset = None
    while True:
        try:
            # Safely fetch active telegram provider from router
            provider = router.providers.get("telegram")
            if provider and hasattr(provider, "get_updates"):
                # Poll with short 5s timeouts
                updates = await provider.get_updates(offset=offset, timeout=5)
                for update in updates:
                    update_id = update.get("update_id")
                    offset = update_id + 1
                    
                    # Push directly into the webhook handler to trigger decoupled enqueuing
                    logger.info(f"[Telegram Polling] Received update id {update_id}")
                    try:
                        await router.handle_webhook("telegram", update)
                    except Exception as he:
                        logger.error(f"[Telegram Polling] Failed handling webhook payload: {he}")
            
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("[Telegram Polling] Worker loop shut down.")
            break
        except Exception as e:
            logger.exception(f"[Telegram Polling] Error in background loop: {e}")
            await asyncio.sleep(5.0)

def seed_defaults():
    """
    Seeds initial system settings into SQLite if they aren't already declared.
    """
    db = SessionLocal()
    try:
        repo = MessageRepository(db)
        defaults = {
            "llm_backend": "mock",
            "llm_url": "http://localhost:11434",
            "llm_model": "gemma:4",
            "system_prompt": "You are a warm, helpful local AI companion named Project Vigil. If you need to look up current events, weather, facts, or information, output [SEARCH: search query]. If you want to generate and send a picture to the user, output [IMAGE: image description]. Keep responses brief, direct, and conversational. You must speak directly to the user in your established persona. Never break character. If you need to think or plan, keep it internal. When generating your final output string, do not include meta-labels like 'thought:', 'Plan:', or 'Response:'. Simply say the dialogue. You are provided with tool data behind the scenes. Never repeat the raw tool output or copy bracketed headers like '[Recalled Memories]:' into your direct speech. Speak only in your natural character voice.",
            "proactive_platform": "mock",
            "proactive_user_id": "mock_user_1",
            "proactive_interval_seconds": "30",      # Check every 30s to allow quick local tests
            "proactive_probability": "0.25",          # 25% chance of starting a conversation per window check
            "proactive_jitter_percentage": "0.30",    # up to 30% random jitter in sleep times to break predictable periodicity
            "dnd_start": "22:00",
            "dnd_end": "08:00",
            "system_health": "healthy",
            "telegram_token": "",                     # Populated via frontend credentials UI
            "telegram_user_id": "",                   # Populated via frontend credentials UI
            "comfyui_backend": "mock",                # 'mock' or 'comfyui'
            "comfyui_url": "http://localhost:8188",
            "comfyui_ckpt": "v1-5-pruned-emaonly.safetensors",
            "discord_token": "",                      # Populated via frontend credentials UI
            "discord_user_id": "",                    # Populated via frontend credentials UI
            "twilio_account_sid": "",                 # Populated via frontend credentials UI
            "twilio_auth_token": "",                  # Populated via frontend credentials UI
            "twilio_number": "",                      # Populated via frontend credentials UI
            "llm_num_ctx": "8192",                     # Ollama context size configuration
            "m365_client_id": "",              # Set via the WebUI Settings panel
            "m365_tenant_id": "common",
            "m365_client_secret": "",           # Set via the WebUI Settings panel
            "m365_redirect_uri": "",             # Auto-derived from url_root; override in WebUI if needed
            "m365_access_token": "",
            "m365_refresh_token": "",
            "m365_token_expiry": "",
            "url_root": "https://127.0.0.1:8003"
        }
        for key, val in defaults.items():
            db_val = repo.get_config(key)
            if db_val is None:
                repo.set_config(key, val)
                logger.info(f"[Main] Seeded configuration defaults: {key} = '{val}'")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles FastAPI context startup and clean shutdown of database sessions & worker tasks.
    """
    # 1. Initialize DB tables & seed initial configuration state
    logger.info("[Main] Initializing database tables...")
    init_db()
    # Build default configurations seed
    seed_defaults()

    # 1.8 Start all local MCP servers
    from src.mcp.client import mcp_manager
    await mcp_manager.start_all()

    # 2. Extract configuration params
    db = SessionLocal()
    try:
        repo = MessageRepository(db)
        tg_token = repo.get_config("telegram_token", "")
        discord_token = repo.get_config("discord_token", "")
        twilio_sid = repo.get_config("twilio_account_sid", "")
        twilio_token = repo.get_config("twilio_auth_token", "")
        twilio_number = repo.get_config("twilio_number", "")
    finally:
        db.close()

    # 3. Register Providers
    router.register_provider("mock", MockTestProvider())
    
    if tg_token and tg_token.strip():
        try:
            logger.info("[Main] Setting up Telegram bot provider connection...")
            router.register_provider("telegram", TelegramProvider(token=tg_token))
        except Exception as err:
            logger.error(f"[Main] Failed setting up Telegram provider: {err}")
    else:
        logger.warning("[Main] Telegram Bot Token is empty. Telegram provider skipped (update key in UI).")

    if discord_token and discord_token.strip():
        try:
            logger.info("[Main] Setting up Discord bot provider connection...")
            from src.providers.discord import DiscordProvider
            router.register_provider("discord", DiscordProvider(token=discord_token))
        except Exception as err:
            logger.error(f"[Main] Failed setting up Discord provider: {err}")
    else:
        logger.warning("[Main] Discord Bot Token is empty. Discord provider skipped (update key in UI).")

    # 3.5 Setup Twilio/WhatsApp Provider
    try:
        logger.info("[Main] Setting up Twilio/WhatsApp provider connection...")
        from src.providers.twilio import TwilioProvider
        twilio_provider = TwilioProvider(
            account_sid=twilio_sid,
            auth_token=twilio_token,
            twilio_number=twilio_number
        )
        router.register_provider("twilio", twilio_provider)
        router.register_provider("whatsapp", twilio_provider)
    except Exception as err:
        logger.error(f"[Main] Failed setting up Twilio/WhatsApp provider: {err}")

    # 4. Attach inbound handler queueing mechanism
    router.register_inbound_handler(enqueue_inbound_message)

    # 5. Start background worker loops
    queue_task = asyncio.create_task(start_queue_worker(router))
    proactive_task = asyncio.create_task(start_proactivity_engine(router))
    polling_task = asyncio.create_task(start_telegram_polling(router))
    
    from src.discord_gateway import start_discord_gateway
    discord_task = asyncio.create_task(start_discord_gateway(router))

    background_tasks.add(queue_task)
    background_tasks.add(proactive_task)
    background_tasks.add(polling_task)
    background_tasks.add(discord_task)

    queue_task.add_done_callback(background_tasks.discard)
    proactive_task.add_done_callback(background_tasks.discard)
    polling_task.add_done_callback(background_tasks.discard)
    discord_task.add_done_callback(background_tasks.discard)

    logger.info("[Main] Background services and loops successfully booted.")

    yield  # Let the server run API endpoints

    # 6. Shut down background processes safely
    logger.info("[Main] Initiating background task cancellations...")
    queue_task.cancel()
    proactive_task.cancel()
    polling_task.cancel()
    discord_task.cancel()
    await asyncio.gather(queue_task, proactive_task, polling_task, discord_task, return_exceptions=True)
    
    # 7. Stop all local MCP servers
    from src.mcp.client import mcp_manager
    await mcp_manager.stop_all()
    logger.info("[Main] Background services and MCP servers terminated cleanly.")


# Build app with lifespan context hooks
app = create_app(router)
app.router.lifespan_context = lifespan

def generate_self_signed_cert(cert_path: str = "cert.pem", key_path: str = "key.pem"):
    """
    Generates a local self-signed SSL certificate and private key with SAN extensions
    to avoid SSL protocol errors when accessing localhost or the private network IP.
    """
    import os
    import ipaddress
    from datetime import datetime, timezone, timedelta
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return

    logger.info("Generating local self-signed SSL certificates (cert.pem / key.pem)...")
    
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Project Vigil"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.now(timezone.utc)
    ).not_valid_after(
        datetime.now(timezone.utc) + timedelta(days=365)
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            x509.DNSName("localhost")
        ]),
        critical=False,
    ).sign(private_key, hashes.SHA256())
    
    with open(key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
        
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    logger.info("Successfully generated local self-signed SSL certificates.")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    base_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ProjectVigil')
    os.makedirs(base_data_dir, exist_ok=True)
    cert_path = os.path.join(base_data_dir, "cert.pem")
    key_path = os.path.join(base_data_dir, "key.pem")
    
    # Auto-generate certs if not present to enable secure local HTTPS out-of-the-box
    try:
        generate_self_signed_cert(cert_path, key_path)
    except Exception as e:
        logger.exception(f"[Main] Error auto-generating self-signed certificates: {e}")
    
    if os.path.exists(cert_path) and os.path.exists(key_path):
        logger.info(f"[Main] Booting secure HTTPS Project Vigil server on port {port} using certs '{cert_path}'...")
        uvicorn.run("src.main:app", host="0.0.0.0", port=port, ssl_keyfile=key_path, ssl_certfile=cert_path, reload=False)
    else:
        logger.info(f"[Main] Booting standard HTTP Project Vigil server on port {port}...")
        uvicorn.run("src.main:app", host="0.0.0.0", port=port, reload=False)
