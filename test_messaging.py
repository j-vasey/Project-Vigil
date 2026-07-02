import asyncio
import logging
from src.router import MessagingRouter
from src.providers.mock import MockTestProvider
from src.providers.telegram import TelegramProvider
from src.models import InboundMessage

# Set up logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("test_messaging")

async def mock_queue_processor_handler(message: InboundMessage):
    """
    Simulates a decoupled queue processor. It receives the parsed inbound message,
    logs it, and represents how messages would be handed off to an asynchronous queue
    to prevent blocking external webhooks.
    """
    print(f"\n=== [Queue Processor Handler Received Message] ===")
    print(f"Platform:  {message.platform}")
    print(f"User ID:   {message.user_id}")
    print(f"Body:      {message.message_body}")
    print(f"Timestamp: {message.timestamp} UTC")
    print(f"Metadata:  {message.metadata}")
    print(f"===================================================\n")

async def main():
    logger.info("Initializing Messaging Router...")
    router = MessagingRouter()

    # 1. Register Mock Provider
    mock_provider = MockTestProvider()
    router.register_provider("mock", mock_provider)

    # 2. Register Telegram Provider with a dummy token for parsing test
    tg_provider = TelegramProvider(token="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ")
    router.register_provider("telegram", tg_provider)

    # 3. Register our Inbound Message Handler callback
    router.register_inbound_handler(mock_queue_processor_handler)

    print("\n--- Test Case 1: Send Outbound Message via Mock Provider ---")
    success = await router.send_message(
        platform="mock",
        user_id="user_123",
        text="Hello! This is a proactive ping from Project Vigil."
    )
    logger.info(f"Outbound send status: {success}")

    print("\n--- Test Case 2: Handle Inbound Webhook via Mock Provider ---")
    mock_webhook_payload = {
        "user_id": "user_456",
        "text": "Hello bot, this is mock user replying!",
        "platform": "mock",
        "metadata": {"session_id": "abc-987"}
    }
    # Pass to the router (representing a webhook receiving an inbound payload)
    await router.handle_webhook("mock", mock_webhook_payload)

    print("\n--- Test Case 3: Parse Inbound Telegram Webhook (No network request) ---")
    # Simulated Telegram update structure (typical JSON post from Telegram webhook)
    tg_webhook_payload = {
        "update_id": 999999,
        "message": {
            "message_id": 55,
            "from": {
                "id": 888888,
                "is_bot": False,
                "first_name": "Jane",
                "username": "janedoe"
            },
            "chat": {
                "id": 888888,
                "first_name": "Jane",
                "type": "private"
            },
            "date": 1782637200, # Unix epoch timestamp
            "text": "Hey Project Vigil! How are you doing today?"
        }
    }
    # Pass Telegram update payload to router
    await router.handle_webhook("telegram", tg_webhook_payload)

    print("\n--- Test Case 4: Routing to unregistered platform (Expected fallback/failure) ---")
    success_fail = await router.send_message(
        platform="twilio",
        user_id="+1234567890",
        text="This should fail to send."
    )
    logger.info(f"Outbound send status (expected False): {success_fail}")

if __name__ == "__main__":
    asyncio.run(main())
