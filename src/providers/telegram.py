import logging
from datetime import datetime
from typing import Any, Dict
import httpx
from src.providers.base import BaseMessagingProvider
from src.models import InboundMessage

logger = logging.getLogger("project_vigil.providers.telegram")

class TelegramProvider(BaseMessagingProvider):
    """
    Production-ready provider for Telegram Bot Integration.
    Uses httpx to perform asynchronous requests to Telegram Bot API.
    """

    def __init__(self, token: str, base_url: str = "https://api.telegram.org"):
        if not token:
            raise ValueError("Telegram Bot Token is required.")
        self.token = token
        self.base_url = f"{base_url.rstrip('/')}/bot{token}"

    async def send_message(self, user_id: str, text: str) -> bool:
        """
        Sends an outbound text message to a chat id via Telegram sendMessage API.
        """
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": user_id,
            "text": text
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=10.0)
                if response.status_code == 200:
                    result = response.json()
                    if result.get("ok"):
                        logger.info(f"[Telegram Outbound] Message successfully sent to user {user_id}")
                        return True
                    else:
                        logger.error(f"[Telegram Outbound] Error from Telegram API: {result.get('description')}")
                else:
                    logger.error(f"[Telegram Outbound] API request failed with status code {response.status_code}: {response.text}")
        except Exception as e:
            logger.exception(f"[Telegram Outbound] Exception occurred while sending message to {user_id}: {e}")
        
        return False

    async def parse_webhook_payload(self, raw_payload: dict) -> InboundMessage:
        """
        Parses Telegram webhook payload (update object) into an InboundMessage.
        Docs: https://core.telegram.org/bots/api#update
        """
        logger.debug(f"[Telegram Inbound Payload] {raw_payload}")
        import base64
        
        # Extract message content (could be a message or edited_message)
        message = raw_payload.get("message") or raw_payload.get("edited_message")
        if not message:
            raise ValueError("Payload contains no 'message' or 'edited_message' from Telegram.")
            
        chat = message.get("chat")
        if not chat or "id" not in chat:
            raise ValueError("Chat or chat ID not found in the Telegram message payload.")
            
        user_id = str(chat["id"])
        message_body = message.get("text", "")
        
        # If the user sent an image, handle photo download and base64 embedding
        photo = message.get("photo")
        if photo:
            caption = message.get("caption", "")
            message_body = caption or ""
            try:
                # Retrieve the largest available photo size
                largest_photo = photo[-1]
                file_id = largest_photo.get("file_id")
                
                logger.info(f"[Telegram Inbound Photo] Fetching file path for file_id {file_id}...")
                async with httpx.AsyncClient() as client:
                    # 1. Get file path
                    info_url = f"{self.base_url}/getFile"
                    info_resp = await client.get(info_url, params={"file_id": file_id}, timeout=10.0)
                    if info_resp.status_code == 200:
                        info_data = info_resp.json()
                        if info_data.get("ok"):
                            file_path = info_data["result"]["file_path"]
                            
                            # Determine download URL base. If custom url, adjust base
                            # e.g., if base_url is https://api.telegram.org/bot<token>, file URL is https://api.telegram.org/file/bot<token>
                            # We can derive file download endpoint from base_url structure
                            base_url_parts = self.base_url.split("/bot")
                            file_download_base = f"{base_url_parts[0]}/file/bot{self.token}"
                            
                            download_url = f"{file_download_base}/{file_path}"
                            logger.info(f"[Telegram Inbound Photo] Downloading photo bytes from {download_url}...")
                            
                            img_resp = await client.get(download_url, timeout=30.0)
                            if img_resp.status_code == 200:
                                b64_img = base64.b64encode(img_resp.content).decode("utf-8")
                                message_body = f"[IMAGE_ATTACHMENT: {b64_img}] {message_body}".strip()
                                logger.info("[Telegram Inbound Photo] Successfully downloaded and base64-encoded inbound photo.")
                            else:
                                logger.error(f"[Telegram Inbound Photo] Download failed: status {img_resp.status_code}")
                        else:
                            logger.error(f"[Telegram Inbound Photo] getFile returned ok=False: {info_data.get('description')}")
                    else:
                        logger.error(f"[Telegram Inbound Photo] getFile request failed: status {info_resp.status_code}")
            except Exception as e:
                logger.exception(f"[Telegram Inbound Photo] Failed downloading or parsing image: {e}")
        
        # Retrieve date timestamp if available
        date_epoch = message.get("date")
        if date_epoch:
            timestamp = datetime.utcfromtimestamp(date_epoch)
        else:
            timestamp = datetime.utcnow()
            
        # Standardized return
        return InboundMessage(
            user_id=user_id,
            message_body=message_body,
            platform="telegram",
            timestamp=timestamp,
            metadata={
                "message_id": message.get("message_id"),
                "from": message.get("from"),
                "chat": chat
            }
        )

    async def send_image(self, user_id: str, image_bytes: bytes, filename: str, caption: str = "") -> bool:
        """
        Sends an outbound photo to a Telegram chat using the sendPhoto multipart API.
        """
        url = f"{self.base_url}/sendPhoto"
        files = {
            "photo": (filename, image_bytes, "image/png")
        }
        data = {
            "chat_id": user_id
        }
        if caption:
            data["caption"] = caption
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, data=data, files=files, timeout=30.0)
                if response.status_code == 200:
                    result = response.json()
                    if result.get("ok"):
                        logger.info(f"[Telegram Outbound Image] Photo successfully sent to user {user_id}")
                        return True
                    else:
                        logger.error(f"[Telegram Outbound Image] API error: {result.get('description')}")
                else:
                    logger.error(f"[Telegram Outbound Image] API failed with status code {response.status_code}: {response.text}")
        except Exception as e:
            logger.exception(f"[Telegram Outbound Image] Exception occurred while sending to {user_id}: {e}")
            
        return False

    async def get_updates(self, offset: int = None, timeout: int = 10) -> list:
        """
        Polls Telegram API getUpdates endpoint to retrieve new incoming messages.
        """
        url = f"{self.base_url}/getUpdates"
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=timeout + 5.0)
                if response.status_code == 200:
                    result = response.json()
                    if result.get("ok"):
                        return result.get("result", [])
                    else:
                        logger.error(f"[Telegram Polling] API error: {result.get('description')}")
                else:
                    logger.error(f"[Telegram Polling] HTTP request failed: status {response.status_code}")
        except Exception as e:
            logger.debug(f"[Telegram Polling] Failed to fetch updates: {e}")
            
        return []
