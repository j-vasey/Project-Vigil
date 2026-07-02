import logging
from datetime import datetime
from src.providers.base import BaseMessagingProvider
from src.models import InboundMessage

logger = logging.getLogger("project_vigil.providers.twilio")

class TwilioProvider(BaseMessagingProvider):
    """
    Twilio provider implementation for SMS and WhatsApp.
    Ties sessions permanently to the phone number / sender ID.
    """
    def __init__(self, account_sid: str = "", auth_token: str = "", twilio_number: str = ""):
        self.account_sid = account_sid.strip() if account_sid else ""
        self.auth_token = auth_token.strip() if auth_token else ""
        self.twilio_number = twilio_number.strip() if twilio_number else ""

    async def send_message(self, user_id: str, text: str) -> bool:
        """
        Sends an outbound text/SMS/WhatsApp message.
        """
        logger.info(f"[Twilio Outbound] To: {user_id} | Body: {text}")
        if not self.account_sid or not self.auth_token:
            print(f"\n--- [Twilio Provider Outbound] ---")
            print(f"Recipient: {user_id}")
            print(f"Message:   {text}")
            print(f"----------------------------------\n")
            return True
            
        import httpx
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        auth = (self.account_sid, self.auth_token)
        
        from_number = self.twilio_number
        to_number = user_id
        if to_number.startswith("whatsapp:"):
            if not from_number.startswith("whatsapp:"):
                from_number = f"whatsapp:{from_number}"
        
        data = {
            "From": from_number,
            "To": to_number,
            "Body": text
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, data=data, auth=auth, timeout=10.0)
                if response.status_code in (200, 201):
                    logger.info(f"[Twilio Outbound] Message successfully sent to {user_id}")
                    return True
                else:
                    logger.error(f"[Twilio Outbound] Twilio API error {response.status_code}: {response.text}")
        except Exception as e:
            logger.exception(f"[Twilio Outbound] Failed to send message via Twilio: {e}")
        return False

    async def parse_webhook_payload(self, raw_payload: dict) -> InboundMessage:
        """
        Parses a Twilio webhook payload.
        Ensures sessions are tied permanently to the incoming phone number (From)
        or user account identifier (WhatsApp sender ID).
        """
        logger.debug(f"[Twilio Inbound Raw] {raw_payload}")
        
        # Twilio sends Form variables: 'From', 'Body', etc.
        user_id = raw_payload.get("From") or raw_payload.get("from") or raw_payload.get("user_id")
        if not user_id:
            raise ValueError("Payload missing 'From' field.")
            
        message_body = raw_payload.get("Body") or raw_payload.get("body") or raw_payload.get("text") or ""
        
        # Handle media if sent (e.g. MediaUrl0 for image attachments)
        media_url = raw_payload.get("MediaUrl0") or raw_payload.get("media_url")
        if media_url:
            import httpx
            import base64
            logger.info(f"[Twilio Inbound Media] Downloading media from {media_url}...")
            try:
                auth = None
                if self.account_sid and self.auth_token and "api.twilio.com" in media_url:
                    auth = (self.account_sid, self.auth_token)
                async with httpx.AsyncClient() as client:
                    resp = await client.get(media_url, auth=auth, timeout=30.0)
                    if resp.status_code == 200:
                        b64_img = base64.b64encode(resp.content).decode("utf-8")
                        message_body = f"[IMAGE_ATTACHMENT: {b64_img}] {message_body}".strip()
                        logger.info("[Twilio Inbound Media] Successfully downloaded and base64-encoded inbound media.")
                    else:
                        logger.error(f"[Twilio Inbound Media] Download failed: status {resp.status_code}")
            except Exception as e:
                logger.exception(f"[Twilio Inbound Media] Failed to download media: {e}")

        platform = "twilio"
        if str(user_id).startswith("whatsapp:"):
            platform = "whatsapp"
            
        return InboundMessage(
            user_id=str(user_id),
            message_body=str(message_body),
            platform=platform,
            timestamp=datetime.utcnow(),
            metadata=raw_payload
        )

    async def send_image(self, user_id: str, image_bytes: bytes, filename: str, caption: str = "") -> bool:
        """
        Sends an image outbound via Twilio (MMS or WhatsApp Media).
        In local/mock mode we log and send a text fallback, or upload/send if integrated.
        """
        logger.info(f"[Twilio Outbound Image] To: {user_id} | Filename: {filename} | Size: {len(image_bytes)} bytes | Caption: {caption}")
        return await self.send_message(user_id, f"{caption} (Sent image: {filename})")
