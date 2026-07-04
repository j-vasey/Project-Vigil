import asyncio
import logging
import json
from src.repository import MessageRepository
from src.llm import get_llm_client

logger = logging.getLogger("project_vigil.memory_extractor")

async def extract_and_store_memories(user_message: str, repo: MessageRepository):
    """
    Lightweight background pass to silently extract persistent facts 
    from the user's latest message and store them in ActiveMemory.
    """
    try:
        backend = repo.get_config("llm_backend", "mock")
        url = repo.get_config("llm_url", "http://localhost:11434")
        # You could optionally configure a smaller model for this task in the DB
        model = repo.get_config("llm_model", "gemma:4")
        
        system_prompt = (
            "You are a background fact-extraction agent for Project Vigil. "
            "Your task is to analyze the following user message and extract any persistent, long-term facts "
            "that the AI companion should remember about the user. "
            "Categories are: 'person', 'preference', 'schedule_habit', 'health', 'goal', 'birthday', 'location'.\n"
            "If there are no meaningful facts to remember (e.g. casual greeting, short question), output an empty JSON list: []\n"
            "If there are facts, output ONLY a JSON list of objects, each containing 'fact' and 'category'. "
            "Example: [{\"fact\": \"User's sister is named Jasmine\", \"category\": \"person\"}]"
        )
        
        client = get_llm_client(backend=backend, url=url, model=model)
        
        # We do not want this to block or hold up anything, so we use a very short context
        response = await client.generate_response(prompt=user_message, system_prompt=system_prompt)
        
        # Attempt to parse JSON from the response
        try:
            import re
            # Strip <think> blocks
            clean_resp = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL | re.IGNORECASE)
            clean_resp = re.sub(r"</think>", "", clean_resp, flags=re.IGNORECASE).strip()

            # Attempt to extract JSON array using regex
            json_match = re.search(r"\[\s*\{.*?\}\s*\]", clean_resp, flags=re.DOTALL)
            if json_match:
                clean_resp = json_match.group(0)
            else:
                # Fallback to markdown code block stripping
                if clean_resp.startswith("```json"):
                    clean_resp = clean_resp[7:]
                if clean_resp.startswith("```"):
                    clean_resp = clean_resp[3:]
                if clean_resp.endswith("```"):
                    clean_resp = clean_resp[:-3]
                
            clean_resp = clean_resp.strip()
            if not clean_resp or clean_resp == "[]":
                return
                
            facts = json.loads(clean_resp)
            if isinstance(facts, list):
                for item in facts:
                    fact_str = item.get("fact")
                    category = item.get("category", "preference")
                    if fact_str:
                        repo.save_memory(fact=fact_str, category=category)
                        logger.info(f"[MemoryExtractor] Auto-extracted fact: [{category}] {fact_str}")
        except json.JSONDecodeError:
            # Model failed to output valid JSON, ignore silently
            pass
            
    except Exception as e:
        logger.error(f"[MemoryExtractor] Extraction failed: {e}")
