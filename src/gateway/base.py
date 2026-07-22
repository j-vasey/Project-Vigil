from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class AgentEvent(BaseModel):
    """
    Normalized message payload across Telegram, Discord, Twilio, and WebUI platforms.
    """
    session_id: str = Field(description="Unified session identifier in platform:channel_id:user_id format")
    platform: str = Field(description="Platform name e.g. telegram, discord, webui")
    user_id: str = Field(description="Platform-specific user ID")
    channel_id: str = Field(description="Platform-specific channel/chat ID")
    text: str = Field(default="", description="Text message content")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build_session_id(cls, platform: str, channel_id: str, user_id: str) -> str:
        """Constructs standardized session isolation key."""
        clean_p = (platform or "unknown").lower().strip()
        clean_c = (channel_id or "default").strip()
        clean_u = (user_id or "anon").strip()
        return f"{clean_p}:{clean_c}:{clean_u}"


class IMessageGateway(ABC):
    """
    Abstract interface for platform-specific gateway adapters.
    """

    @abstractmethod
    async def parse_event(self, raw_payload: Dict[str, Any]) -> AgentEvent:
        """Parses a raw incoming webhook/gateway payload into a normalized AgentEvent."""
        pass

    @abstractmethod
    async def send_text(self, user_id: str, text: str) -> bool:
        """Dispatches an outbound text message."""
        pass

    @abstractmethod
    async def send_media(self, user_id: str, image_bytes: bytes, filename: str, caption: str = "") -> bool:
        """Dispatches an outbound image or media artifact."""
        pass
