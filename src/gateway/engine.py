import logging
from typing import Optional, List
from src.gateway.base import AgentEvent
from src.models import InboundMessage
from src.router import MessagingRouter

logger = logging.getLogger("project_vigil.gateway")


class GatewayEngine:
    """
    Unified Multi-Channel Gateway Engine for Project Vigil.
    Normalizes events across channels and manages unified response dispatches.
    """

    def __init__(self, router: MessagingRouter):
        self.router = router

    def normalize_inbound(self, msg: InboundMessage) -> AgentEvent:
        """
        Converts a legacy InboundMessage model into a normalized AgentEvent.
        """
        channel = getattr(msg, "user_id", "default_channel")
        session_id = AgentEvent.build_session_id(
            platform=msg.platform,
            channel_id=channel,
            user_id=msg.user_id
        )
        return AgentEvent(
            session_id=session_id,
            platform=msg.platform,
            user_id=msg.user_id,
            channel_id=channel,
            text=msg.message_body,
            timestamp=msg.timestamp,
            metadata=msg.metadata or {}
        )

    async def send_progress_notice(self, platform: str, user_id: str, notice: str) -> bool:
        """
        Dispatches a friendly, natural progress update to the user before starting long tasks.
        """
        logger.info(f"[Gateway] Dispatching progress notice to user {user_id} on '{platform}': '{notice}'")
        return await self.router.send_message(platform, user_id, notice)

    async def send_response(self, platform: str, user_id: str, text: str) -> bool:
        """
        Routes text responses to the target platform provider.
        """
        return await self.router.send_message(platform, user_id, text)

    async def send_media(self, platform: str, user_id: str, image_bytes: bytes, filename: str, caption: str = "") -> bool:
        """
        Routes media artifacts (e.g. ComfyUI images) to the target platform provider.
        """
        return await self.router.send_image(platform, user_id, image_bytes, filename, caption)
