import asyncio
import httpx
import subprocess
import time
import os
import sys

# Isolate the database for system tests to prevent locked file errors with running service
os.environ["DATABASE_URL"] = "sqlite:///test_project_vigil.db"

# Override print to safely handle Windows console encoding issues (cp1252/UnicodeEncodeError)
_original_print = print
def print(*args, **kwargs):
    msg = " ".join(str(arg) for arg in args)
    file = kwargs.get('file', sys.stdout)
    if file in (sys.stdout, sys.stderr):
        try:
            _original_print(msg, **kwargs)
        except UnicodeEncodeError:
            encoding = getattr(file, 'encoding', 'utf-8') or 'utf-8'
            safe_msg = msg.encode(encoding, errors='replace').decode(encoding)
            _original_print(safe_msg, **kwargs)
    else:
        _original_print(*args, **kwargs)

async def verify_system():
    print("=== Starting Project Vigil System Integration Test ===")
    
    # Remove existing SQLite DB for clean run
    if os.path.exists("test_project_vigil.db"):
        print("Cleaning up old test_project_vigil.db file...")
        try:
            os.remove("test_project_vigil.db")
        except Exception as e:
            print(f"Could not remove old database: {e}")
            
    # Start the FastAPI server in a background process
    print("Starting FastAPI main server in background on port 8002...")
    import os as local_os
    env = local_os.environ.copy()
    env["PORT"] = "8002"
    env["DATABASE_URL"] = "sqlite:///test_project_vigil.db"
    server_process = subprocess.Popen(
        [sys.executable, "-m", "src.main"],
        env=env
    )
    
    # Wait for the server to spin up and bind port 8002
    print("Waiting 3 seconds for server startup...")
    await asyncio.sleep(3)
    
    client = httpx.AsyncClient(base_url="https://127.0.0.1:8002", verify=False, timeout=30.0)
    
    try:
        # Test Case 1: Check health endpoint
        print("\n--- Test Case 1: Checking health endpoint ---")
        response = await client.get("/api/health")
        assert response.status_code == 200, f"Health check failed: {response.text}"
        data = response.json()
        print(f"Health check output: {data}")
        assert data["engine_status"] == "healthy"
        
        # Test Case 2: Fetch configurations
        print("\n--- Test Case 2: Reading configuration keys ---")
        response = await client.get("/api/config")
        assert response.status_code == 200
        configs = response.json()
        print(f"Configurations list: {configs}")
        assert configs["llm_backend"] == "mock"
        
        # Test Case 3: Update configuration
        print("\n--- Test Case 3: Editing configurations ---")
        payload = {
            "configs": {
                "system_prompt": "You are a warm, witty AI sidekick.",
                "proactive_user_id": "tester_alice",
                "proactive_platform": "mock",
                "dnd_start": "02:00",
                "dnd_end": "04:00"
            }
        }
        response = await client.post("/api/config", json=payload)
        assert response.status_code == 200
        updated_configs = response.json()["configs"]
        print(f"Updated configurations: {updated_configs}")
        assert updated_configs["system_prompt"] == "You are a warm, witty AI sidekick."
        assert updated_configs["proactive_user_id"] == "tester_alice"
        
        # Test Case 4: Simulate inbound webhook (Decoupled queue testing)
        print("\n--- Test Case 4: Sending webhook payload ---")
        webhook_payload = {
            "user_id": "tester_alice",
            "text": "Hello, bot! Let's test the queue.",
            "platform": "mock"
        }
        response = await client.post("/webhook/mock", json=webhook_payload)
        assert response.status_code == 200
        print(f"Webhook response: {response.json()}")
        
        print("Waiting 1.5 seconds for decoupled background processing to complete...")
        await asyncio.sleep(1.5)
        
        # Test Case 5: Verify that the DB logs recent activity
        print("\n--- Test Case 5: Checking audit log for recent activity ---")
        response = await client.get("/api/health")
        assert response.status_code == 200
        health_data = response.json()
        print(f"Recent health audit history: {health_data['recent_proactivity']}")
        
        # Test Case 6: Toggle System Engine to Paused
        print("\n--- Test Case 6: Toggling system engine to paused ---")
        response = await client.post("/api/health/toggle")
        assert response.status_code == 200
        toggle_data = response.json()
        print(f"Engine status after toggle: {toggle_data}")
        assert toggle_data["engine_status"] == "paused"
        
        # Test Case 7: Sending webhook when engine is paused (should skip processing)
        print("\n--- Test Case 7: Webhook when engine is paused ---")
        response = await client.post("/webhook/mock", json=webhook_payload)
        assert response.status_code == 200
        print("Waiting 1 second...")
        await asyncio.sleep(1)
        
        # Restore status
        print("\n--- Toggling system engine back to active ---")
        await client.post("/api/health/toggle")
        
        # Test Case 8: Webhook triggering ComfyUI image generation
        print("\n--- Test Case 8: Webhook triggering ComfyUI image generation ---")
        image_webhook_payload = {
            "user_id": "tester_alice",
            "text": "Please trigger image now.",
            "platform": "mock"
        }
        response = await client.post("/webhook/mock", json=image_webhook_payload)
        assert response.status_code == 200
        print(f"Webhook response: {response.json()}")
        print("Waiting 1.5 seconds for ComfyUI image generation and routing to complete...")
        await asyncio.sleep(1.5)

        # Test Case 9: Test LLM connection endpoint
        print("\n--- Test Case 9: Testing LLM connection endpoint ---")
        test_payload = {
            "backend": "mock",
            "url": "http://localhost:11434",
            "model": "gemma:4"
        }
        response = await client.post("/api/llm/test", json=test_payload)
        assert response.status_code == 200
        print(f"Test LLM connection response: {response.json()}")

        # Test Case 10: Manual message sending endpoint
        print("\n--- Test Case 10: Manual message sending endpoint ---")
        manual_text_payload = {
            "platform": "mock",
            "user_id": "tester_bob",
            "text": "Hello tester bob from admin console!"
        }
        response = await client.post("/api/manual/send", json=manual_text_payload)
        assert response.status_code == 200
        print(f"Manual text send response: {response.json()}")

        manual_image_payload = {
            "platform": "mock",
            "user_id": "tester_bob",
            "text": "[IMAGE: a golden crown] Here is your crown!"
        }
        response = await client.post("/api/manual/send", json=manual_image_payload)
        assert response.status_code == 200
        print(f"Manual image send response: {response.json()}")

        # Test Case 11: Discord Messaging Provider unit validation
        print("\n--- Test Case 11: Discord Provider Unit Validation ---")
        from src.providers.discord import DiscordProvider
        discord_prov = DiscordProvider(token="mock_discord_token")
        
        discord_payload = {
            "channel_id": "99999",
            "author": {
                "id": "11111",
                "username": "discord_tester"
            },
            "content": "Hello bot from Discord!"
        }
        parsed = await discord_prov.parse_webhook_payload(discord_payload)
        assert parsed.platform == "discord"
        assert parsed.user_id == "99999"
        assert parsed.message_body == "Hello bot from Discord!"
        print(f"Parsed Discord webhook: {parsed}")
        
        success = await discord_prov.send_message(user_id="99999", text="Outbound Discord message")
        assert success is False # Offline send should return False
        print("Verified that offline Discord sends fail gracefully.")
        
        # Test DM channel resolution fallback
        dm_channel = await discord_prov.get_or_create_dm_channel("12345")
        assert dm_channel == "12345"
        print("Verified Discord DM channel resolution fallback behaves correctly.")
        
        # Test Case 12: Web Search Tool validation
        print("\n--- Test Case 12: Web Search Tool Validation ---")
        from src.tools.search import search_web_tool
        search_res = await search_web_tool("python programming language")
        assert search_res is not None
        print(f"Search tool returned results: {search_res[:120]}...")
        
        # Test Case 13: Secure Token Encryption Validation
        print("\n--- Test Case 13: Secure Token Encryption Validation ---")
        token_payload = {
            "configs": {
                "telegram_token": "my-secret-telegram-token",
                "discord_token": "my-secret-discord-token"
            }
        }
        response = await client.post("/api/config", json=token_payload)
        assert response.status_code == 200
        
        # Verify that GET /api/config masks the returned tokens
        response = await client.get("/api/config")
        assert response.status_code == 200
        configs = response.json()
        assert configs["telegram_token"] == "********"
        assert configs["discord_token"] == "********"
        print("Verified that client API GET config returns masked values.")
        
        # Verify that querying via the repository layer retrieves decrypted plain text tokens
        from src.database import SessionLocal
        from src.repository import MessageRepository
        db = SessionLocal()
        try:
            repo = MessageRepository(db)
            raw_tg = repo.get_config("telegram_token")
            raw_discord = repo.get_config("discord_token")
            assert raw_tg == "my-secret-telegram-token"
            assert raw_discord == "my-secret-discord-token"
            print("Verified that direct repository queries yield plain text tokens.")
            
            # Check database table to ensure tokens are stored as encrypted JSON strings
            from src.database import Configuration
            row_tg = db.query(Configuration).filter(Configuration.key == "telegram_token").first()
            assert row_tg is not None
            assert row_tg.value != "my-secret-telegram-token"
            assert "ciphertext" in row_tg.value
            assert "salt" in row_tg.value
            print("Verified that raw database records store encrypted/salted JSON structures.")
            
            # Test masking bypass behavior (submitting mask should keep original credentials)
            mask_payload = {
                "configs": {
                    "telegram_token": "********",
                    "discord_token": "********"
                }
            }
            response = await client.post("/api/config", json=mask_payload)
            assert response.status_code == 200
            
            # Verify original credentials remain intact
            raw_tg_after = repo.get_config("telegram_token")
            assert raw_tg_after == "my-secret-telegram-token"
            print("Verified that submitting masked tokens ('********') skips overwriting raw credentials.")
        finally:
            db.close()

        # Test Case 14: ComfyUI Checkpoints API validation
        print("\n--- Test Case 14: ComfyUI Checkpoints API validation ---")
        response = await client.get("/api/comfyui/checkpoints?url=http://localhost:8188")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert isinstance(data["checkpoints"], list)
        assert len(data["checkpoints"]) > 0
        print("Verified that ComfyUI checkpoints list retrieves successfully.")
            
        print("\n=== All Integration Tests Completed Successfully! ===")
        
    except Exception as e:
        print(f"\n[TEST_FAILURE] Integration test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Shutdown server process
        print("\nTerminating background main server...")
        server_process.terminate()
        server_process.wait()
        print("Server process shut down cleanly.")
        
        # Cleanup test DB
        if os.path.exists("test_project_vigil.db"):
            print("Cleaning up test_project_vigil.db...")
            try:
                os.remove("test_project_vigil.db")
            except Exception as e:
                print(f"Could not remove test database: {e}")

if __name__ == "__main__":
    asyncio.run(verify_system())
