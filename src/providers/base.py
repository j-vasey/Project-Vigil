from abc import ABC, abstractmethod
from src.models import InboundMessage

class BaseMessagingProvider(ABC):
    """
    Abstract base class for messaging providers in Project Vigil.
    All integration channels (Telegram, SMS, WhatsApp) must implement this interface.
    """

    @abstractmethod
    async def send_message(self, user_id: str, text: str) -> bool:
        """
        Sends an outbound message to a specific user on the platform.

        Args:
            user_id: The platform-specific unique identifier for the user.
            text: The message body to send.

        Returns:
            bool: True if the message was sent successfully, False otherwise.
        """
        pass

    @abstractmethod
    async def parse_webhook_payload(self, raw_payload: dict) -> InboundMessage:
        """
        Parses a raw webhook HTTP request payload from the platform provider 
        into a standardized InboundMessage model.

        Args:
            raw_payload: The raw dictionary received from the provider's webhook.

        Returns:
            InboundMessage: The normalized inbound message model.
        """
        pass

    @abstractmethod
    async def send_image(self, user_id: str, image_bytes: bytes, filename: str, caption: str = "") -> bool:
        """
        Sends an outbound image with an optional text caption to a specific user.

        Args:
            user_id: The platform-specific unique identifier for the user.
            image_bytes: The raw binary data of the image.
            filename: The filename for the sent file.
            caption: An optional caption text to accompany the image.

        Returns:
            bool: True if sent successfully, False otherwise.
        """
        pass
