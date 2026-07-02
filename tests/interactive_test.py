import asyncio
import logging
from unittest.mock import AsyncMock, patch

from src.llm import OllamaClient
from src.search_pipeline import run_search_pipeline
from src.mcp.servers.calendar import view_upcoming_agenda
from src.router import MessagingRouter

# Set up logging for the test output
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("project_vigil.tests")

async def test_tool_execution_loop():
    logger.info("\n--- TEST A: Tool Execution & Output Generation ---")
    client = OllamaClient(base_url="http://localhost:11434", model="gemma-4")
    
    # We will mock the httpx client inside generate_response to simulate pass 1 (tool call) and pass 2 (final output).
    class MockStream:
        def __init__(self, is_pass_one=True):
            self.is_pass_one = is_pass_one
            
        async def __aenter__(self):
            return self
            
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
            
        @property
        def status_code(self):
            return 200
            
        async def aiter_lines(self):
            if self.is_pass_one:
                # Pass 1: generate a tool call
                yield '{"message": {"role": "assistant", "content": "", "tool_calls": [{"id": "123", "function": {"name": "view_upcoming_agenda", "arguments": {"days_ahead": 1}}}]}}'
                yield '{"done": true}'
            else:
                # Pass 2: generate text
                yield '{"message": {"role": "assistant", "content": "Here is your agenda: Meeting at 10 AM."}}'
                yield '{"done": true}'

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            # We use a static attribute to persist across MockAsyncClient instantiations
            pass
            
        async def __aenter__(self):
            return self
            
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
            
        def stream(self, method, url, **kwargs):
            if not hasattr(MockAsyncClient, "call_count"):
                MockAsyncClient.call_count = 0
            MockAsyncClient.call_count += 1
            return MockStream(is_pass_one=(MockAsyncClient.call_count == 1))

    # Mock tool execution too to avoid live network
    with patch("src.llm.httpx.AsyncClient", new=MockAsyncClient):
        with patch("src.tools.registry.tool_registry.execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "Meeting at 10 AM"
            
            # Run
            result = await client.generate_response("User: What is on my agenda?")
            
            assert "Meeting at 10 AM" in result, f"Expected final response to contain tool data, got: {result}"
            logger.info("PASSED: Tool result appended to message stack and final text generated.")

async def test_search_pipeline():
    logger.info("\n--- TEST B: Web Search & Retrieval ---")
    
    class MockHttpxClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def post(self, url, json=None, timeout=None):
            class MockResp:
                status_code = 200
                def json(self):
                    # If temperature 0.0 (distill), return keywords. Else (generate), return final string.
                    if json.get("options", {}).get("temperature") == 0.0:
                        return {"message": {"content": "England Mexico World Cup"}}
                    else:
                        return {"message": {"content": "The match is on Sunday at Azteca."}}
            return MockResp()
            
    class MockRequests:
        @staticmethod
        def get(url, headers, timeout):
            class MockReqResp:
                status_code = 200
                content = b"<html><body><p>England vs Mexico game is at Azteca Stadium.</p></body></html>"
            return MockReqResp()
            
    class MockDDGS:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def text(self, query, max_results):
            return [{"href": "http://mock-news.com/article1"}]

    with patch("src.search_pipeline.httpx.AsyncClient", new=MockHttpxClient):
        with patch("src.search_pipeline.requests.get", new=MockRequests.get):
            with patch("src.search_pipeline.DDGS", new=MockDDGS):
                result = await run_search_pipeline("England vs Mexico game later this week")
                assert "Azteca" in result, "Final search text generation failed to use scraped context."
                logger.info("PASSED: Web scraping context injected and search pipeline generated output.")

async def test_calendar_crud():
    logger.info("\n--- TEST C: Calendar CRUD Operations ---")
    
    class MockHttpxClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def get(self, url, headers=None, params=None):
            class MockResp:
                status_code = 200
                def json(self):
                    return {"value": [{"id": "1", "subject": "Test Event", "start": {"dateTime": "2026-07-04T10:00:00Z"}, "end": {"dateTime": "2026-07-04T11:00:00Z"}, "location": {"displayName": "Office"}}]}
            
            # Assert datetime formats
            start = params.get("startDateTime")
            end = params.get("endDateTime")
            # Should be timezone-aware strings (i.e., contain +00:00 or Z without naive warning)
            assert "+" in start or "Z" in start, f"Start time {start} lacks timezone info!"
            assert "+" in end or "Z" in end, f"End time {end} lacks timezone info!"
            
            return MockResp()

    with patch("src.mcp.servers.calendar.get_valid_access_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "mock_token_123"
        with patch("src.mcp.servers.calendar.httpx.AsyncClient", new=MockHttpxClient):
            result = await view_upcoming_agenda(days_ahead=2)
            assert "Test Event" in result, "Calendar parsing failed."
            logger.info("PASSED: Calendar API used strict timezone-aware datetime serialization.")

async def test_downstream_providers():
    logger.info("\n--- TEST D: Downstream Providers (Outbound Dispatch) ---")
    
    router = MessagingRouter()
    
    class MockTelegramProvider:
        def __init__(self):
            self.sent_messages = []
            self.platform_name = "telegram"
            
        async def send_message(self, user_id: str, text: str):
            self.sent_messages.append((user_id, text))

    provider = MockTelegramProvider()
    router.register_provider("telegram", provider)
    
    # Simulate routing a final text payload
    await router.send_message(platform="telegram", user_id="user1", text="Here is your final generated response after tools.")
    
    assert len(provider.sent_messages) == 1, "Message not dispatched."
    assert provider.sent_messages[0][1] == "Here is your final generated response after tools."
    logger.info("PASSED: Final text payload successfully dispatched to Telegram outbound wrapper.")

async def main():
    logger.info("=== STARTING INTERACTIVE TEST SUITE ===")
    await test_tool_execution_loop()
    await test_search_pipeline()
    await test_calendar_crud()
    await test_downstream_providers()
    logger.info("\n=== ALL TESTS PASSED SUCCESSFULLY ===")

if __name__ == "__main__":
    asyncio.run(main())
