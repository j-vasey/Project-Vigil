from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

class InboundMessage(BaseModel):
    """
    Represents an incoming message received from a messaging provider.
    """
    user_id: str = Field(..., description="Unique identifier of the user on the messaging platform.")
    message_body: str = Field(..., description="The content/text of the message.")
    platform: str = Field(..., description="The messaging platform (e.g., 'mock', 'telegram', 'twilio').")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="The UTC timestamp of when the message was received.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional provider-specific metadata.")


class OutboundMessage(BaseModel):
    """
    Represents an outgoing message to be sent to a messaging provider.
    """
    user_id: str = Field(..., description="Unique identifier of the recipient user on the messaging platform.")
    message_body: str = Field(..., description="The content/text to send.")
    platform: str = Field(..., description="The messaging platform (e.g., 'mock', 'telegram', 'twilio').")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="The UTC timestamp of when the message was dispatched.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional provider-specific metadata/options.")
