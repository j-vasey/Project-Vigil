import asyncio
import json
import logging
import websockets
from src.router import MessagingRouter

logger = logging.getLogger("project_vigil.discord_gateway")


async def heartbeat_loop(ws, interval_ms: int, last_seq_ref: list):
    """
    Sends heartbeats periodically to keep the Discord Gateway connection alive.
    """
    interval_sec = interval_ms / 1000.0
    logger.debug(f"[Discord Gateway] Heartbeat loop started (interval: {interval_sec}s)")
    try:
        while True:
            await asyncio.sleep(interval_sec)
            payload = {
                "op": 1,
                "d": last_seq_ref[0]
            }
            logger.debug("[Discord Gateway] Sending heartbeat...")
            await ws.send(json.dumps(payload))
    except asyncio.CancelledError:
        logger.debug("[Discord Gateway] Heartbeat loop cancelled.")
    except Exception as e:
        logger.error(f"[Discord Gateway] Heartbeat loop error: {e}")


async def token_watcher_loop(ws, router, current_token: str):
    """
    Checks periodically if the Discord provider's token has changed.
    If it has, closes the websocket connection to force a reconnect with the new token.
    """
    logger.debug("[Discord Gateway] Token watcher loop started.")
    try:
        while True:
            await asyncio.sleep(2.0)
            provider = router.providers.get("discord")
            new_token = provider.token.strip() if (provider and getattr(provider, "token", None)) else ""
            if new_token != current_token:
                logger.info("[Discord Gateway] Token change detected. Closing Gateway connection to trigger reconnect.")
                await ws.close()
                break
    except asyncio.CancelledError:
        logger.debug("[Discord Gateway] Token watcher loop cancelled.")
    except Exception as e:
        logger.error(f"[Discord Gateway] Token watcher loop error: {e}")


async def start_discord_gateway(router: MessagingRouter) -> None:
    """
    Background worker that connects to the Discord Real-time Gateway WebSocket.
    Allows local instances to receive DM / server messages in real-time.
    """
    logger.info("[Discord Gateway] Starting background Gateway worker...")
    
    # We will track sequence number and bot user ID to filter self-messages
    last_seq_ref = [None]
    bot_id = [None]
    
    gateway_url = "wss://gateway.discord.gg/?v=10&encoding=json"
    
    while True:
        try:
            # 1. Fetch active discord provider dynamically
            provider = router.providers.get("discord")
            if not provider or not getattr(provider, "token", None):
                # No active token configured yet, sleep and wait
                await asyncio.sleep(5.0)
                continue
                
            token = provider.token.strip()
            if not token:
                await asyncio.sleep(5.0)
                continue
                
            logger.info("[Discord Gateway] Attempting connection to Discord Gateway...")
            async with websockets.connect(gateway_url) as ws:
                heartbeat_task = None
                watcher_task = None
                try:
                    watcher_task = asyncio.create_task(
                        token_watcher_loop(ws, router, token)
                    )
                    async for message in ws:
                        payload = json.loads(message)
                        op = payload.get("op")
                        data = payload.get("d")
                        seq = payload.get("s")
                        event_type = payload.get("t")
                        
                        if seq is not None:
                            last_seq_ref[0] = seq
                            
                        # Op 10: Hello - Start heartbeats and identify
                        if op == 10:
                            heartbeat_interval = data.get("heartbeat_interval", 45000)
                            heartbeat_task = asyncio.create_task(
                                heartbeat_loop(ws, heartbeat_interval, last_seq_ref)
                            )
                            
                            # Identify
                            identify_payload = {
                                "op": 2,
                                "d": {
                                    "token": token,
                                    "intents": 37376,  # GUILD_MESSAGES (512) + DIRECT_MESSAGES (4096) + MESSAGE_CONTENT (32768)
                                    "properties": {
                                        "os": "windows",
                                        "browser": "project_vigil",
                                        "device": "project_vigil"
                                    }
                                }
                            }
                            await ws.send(json.dumps(identify_payload))
                            logger.info("[Discord Gateway] Dispatched Identify payload to gateway.")
                            
                        # Op 11: Heartbeat ACK
                        elif op == 11:
                            logger.debug("[Discord Gateway] Heartbeat acknowledged by server.")
                            
                        # Op 0: Dispatch events
                        elif op == 0:
                            if event_type == "READY":
                                bot_user = data.get("user", {})
                                bot_id[0] = str(bot_user.get("id"))
                                logger.info(f"[Discord Gateway] Connected as bot user '{bot_user.get('username')}' (ID: {bot_id[0]})")
                                
                            elif event_type == "MESSAGE_CREATE":
                                # Exclude self-messages
                                author_id = data.get("author", {}).get("id")
                                if author_id and str(author_id) == bot_id[0]:
                                    continue
                                    
                                logger.info(f"[Discord Gateway] Inbound message created by user {author_id} in channel {data.get('channel_id')}")
                                try:
                                    await router.handle_webhook("discord", data)
                                except Exception as he:
                                    logger.error(f"[Discord Gateway] Webhook routing failed: {he}")
                                    
                finally:
                    if heartbeat_task:
                        heartbeat_task.cancel()
                    if watcher_task:
                        watcher_task.cancel()
                    tasks = [t for t in (heartbeat_task, watcher_task) if t]
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                        
            logger.warning("[Discord Gateway] WebSocket disconnected. Retrying in 10 seconds...")
            await asyncio.sleep(10.0)
            
        except asyncio.CancelledError:
            logger.info("[Discord Gateway] Gateway loop shut down.")
            break
        except Exception as e:
            logger.exception(f"[Discord Gateway] Error in Gateway loop: {e}. Reconnecting in 10s...")
            await asyncio.sleep(10.0)
