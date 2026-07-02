import logging
import httpx
from datetime import datetime
from src.providers.base import BaseMessagingProvider
from src.models import InboundMessage

logger = logging.getLogger("project_vigil.providers.discord")


class DiscordProvider(BaseMessagingProvider):
    """
    Discord Messaging Provider implementation for Project Vigil.
    Uses Discord Bot REST API to send text and upload image attachments to Discord channels.
    """

    def __init__(self, token: str, base_url: str = "https://discord.com/api/v10"):
        self.token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bot {self.token}"
        }
        self.known_channels = set()  # Cache of IDs known to be channel IDs
        self.dm_channels_cache = {}  # User ID -> Channel ID mapping

    async def get_or_create_dm_channel(self, target_id: str) -> str:
        """
        Attempts to open a direct message (DM) channel with the given recipient user ID.
        If it succeeds, returns the DM channel ID.
        If it fails (e.g. target_id is already a channel ID, or API error), falls back to target_id.
        """
        if target_id in self.known_channels:
            return target_id
        if target_id in self.dm_channels_cache:
            return self.dm_channels_cache[target_id]

        url = f"{self.base_url}/users/@me/channels"
        payload = {
            "recipient_id": target_id
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=self.headers, timeout=10.0)
                if response.status_code in (200, 201):
                    channel_data = response.json()
                    dm_channel_id = channel_data.get("id")
                    if dm_channel_id:
                        logger.info(f"[Discord] Successfully opened DM channel {dm_channel_id} with user {target_id}")
                        self.dm_channels_cache[target_id] = dm_channel_id
                        self.known_channels.add(dm_channel_id)
                        return dm_channel_id
                else:
                    logger.warning(f"[Discord] DM channel creation failed for target {target_id} with status {response.status_code}: {response.text}")
        except Exception as e:
            logger.exception(f"[Discord] DM channel creation exception for target {target_id}: {e}")
        return target_id

    async def send_message(self, user_id: str, text: str) -> bool:
        """
        Sends an outbound text message to a specific Discord channel ID or user DM channel.
        """
        channel_id = await self.get_or_create_dm_channel(user_id)
        url = f"{self.base_url}/channels/{channel_id}/messages"
        payload = {
            "content": text
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=self.headers, timeout=15.0)
                if response.status_code == 200:
                    logger.info(f"[Discord Outbound] Message successfully sent to channel {channel_id}")
                    return True
                else:
                    logger.error(f"[Discord Outbound] API failed (status code {response.status_code}): {response.text}")
        except Exception as e:
            logger.exception(f"[Discord Outbound] Exception occurred while sending to channel {channel_id}: {e}")
            
        return False

    async def send_image(self, user_id: str, image_bytes: bytes, filename: str, caption: str = "") -> bool:
        """
        Uploads a generated photo attachment to a Discord channel or user DM channel via multipart upload.
        """
        channel_id = await self.get_or_create_dm_channel(user_id)
        url = f"{self.base_url}/channels/{channel_id}/messages"
        
        # In Discord API v10, attachments are uploaded using multipart form-data.
        # The file parameter key is "files[0]". Caption content is passed via the "content" payload.
        import json
        files = {
            "files[0]": (filename, image_bytes, "image/png")
        }
        
        payload = {}
        if caption:
            payload["content"] = caption
            
        data = {
            "payload_json": json.dumps(payload)
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, data=data, files=files, headers=self.headers, timeout=30.0)
                if response.status_code == 200 or response.status_code == 201:
                    logger.info(f"[Discord Outbound Image] Image successfully sent to channel {channel_id}")
                    return True
                else:
                    logger.error(f"[Discord Outbound Image] API failed (status code {response.status_code}): {response.text}")
        except Exception as e:
            logger.exception(f"[Discord Outbound Image] Exception occurred while sending to channel {channel_id}: {e}")

        return False

    async def parse_webhook_payload(self, raw_payload: dict) -> InboundMessage:
        """
        Parses an incoming webhook payload relayed from Discord channel events.
        """
        logger.debug(f"[Discord Inbound Raw] {raw_payload}")
        import base64
        
        channel_id = str(raw_payload.get("channel_id", "unknown_channel"))
        author = raw_payload.get("author", {})
        author_id = str(author.get("id", "unknown_author"))
        message_body = str(raw_payload.get("content", ""))
        
        # If the user uploaded attachments, look for image formats to download & base64 encode
        attachments = raw_payload.get("attachments", [])
        if attachments:
            image_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif")
            image_attachment = None
            for att in attachments:
                fname = att.get("filename", "").lower()
                if fname.endswith(image_extensions):
                    image_attachment = att
                    break
            
            if image_attachment:
                url = image_attachment.get("url")
                logger.info(f"[Discord Inbound Photo] Downloading photo attachment from {url}...")
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(url, headers=self.headers, timeout=30.0)
                        if resp.status_code == 200:
                            b64_img = base64.b64encode(resp.content).decode("utf-8")
                            message_body = f"[IMAGE_ATTACHMENT: {b64_img}] {message_body}".strip()
                            logger.info("[Discord Inbound Photo] Successfully downloaded and base64-encoded inbound photo.")
                        else:
                            logger.error(f"[Discord Inbound Photo] Download failed: status {resp.status_code}")
                except Exception as e:
                    logger.exception(f"[Discord Inbound Photo] Failed downloading attachment: {e}")
        
        # Populate routing cache
        if channel_id != "unknown_channel":
            self.known_channels.add(channel_id)
            if author_id != "unknown_author":
                self.dm_channels_cache[author_id] = channel_id
        
        return InboundMessage(
            user_id=channel_id,
            message_body=message_body,
            platform="discord",
            timestamp=datetime.utcnow(),
            metadata={
                "author_id": author_id,
                "username": author.get("username", "unknown")
            }
        )
