import logging
from datetime import datetime
from src.providers.base import BaseMessagingProvider
from src.models import InboundMessage

logger = logging.getLogger("project_vigil.providers.mock")

class MockTestProvider(BaseMessagingProvider):
    """
    A mock provider for testing and local development.
    Logs outbound messages to the console and simulates parsing inbound webhook payloads.
    """

    async def send_message(self, user_id: str, text: str) -> bool:
        logger.info(f"[Mock Outbound] To: {user_id} | Body: {text}")
        print(f"\n--- [Mock Provider Outbound] ---")
        print(f"Recipient: {user_id}")
        print(f"Message:   {text}")
        print(f"--------------------------------\n")
        return True

    async def parse_webhook_payload(self, raw_payload: dict) -> InboundMessage:
        logger.debug(f"[Mock Webhook Raw] {raw_payload}")
        user_id = str(raw_payload.get("user_id", "mock_user_id"))
        message_body = str(raw_payload.get("text", ""))
        platform = str(raw_payload.get("platform", "mock"))
        metadata = raw_payload.get("metadata", {})

        return InboundMessage(
            user_id=user_id,
            message_body=message_body,
            platform=platform,
            timestamp=datetime.utcnow(),
            metadata=metadata
        )

    async def send_image(self, user_id: str, image_bytes: bytes, filename: str, caption: str = "") -> bool:
        logger.info(f"[Mock Outbound Image] To: {user_id} | Filename: {filename} | Size: {len(image_bytes)} bytes | Caption: {caption}")
        print(f"\n--- [Mock Provider Outbound IMAGE] ---")
        print(f"Recipient: {user_id}")
        print(f"Filename:  {filename}")
        print(f"Size:      {len(image_bytes)} bytes")
        print(f"Caption:   {caption}")
        print(f"--------------------------------------\n")
        return True
