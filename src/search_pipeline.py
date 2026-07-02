import logging
import httpx
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from src.llm import get_llm_client
from src.database import SessionLocal
from src.repository import MessageRepository

logger = logging.getLogger("project_vigil.search_pipeline")

async def run_search_pipeline(user_prompt: str) -> str:
    """
    Executes a 4-step autonomous web-search RAG pipeline:
    1. Query Generation (Ollama distill)
    2. Web Retrieval & Scraping (DDGS + BeautifulSoup)
    3. RAG Context Assembly
    4. Final Generation (Ollama)
    """
    logger.info("=== STARTING AUTONOMOUS SEARCH PIPELINE ===")
    
    # Initialize DB to get LLM config
    db = SessionLocal()
    try:
        repo = MessageRepository(db)
        backend = repo.get_config("llm_backend", "ollama")
        url = repo.get_config("llm_url", "http://localhost:11434")
        model = repo.get_config("llm_model", "gemma-4-26B-A-4B-it-UD-Q3_K_M:latest")
    finally:
        db.close()
        
    # ── Step 1: Query Generation Pass ──────────────────────────────────────────
    logger.info("[Step 1] Distilling user prompt into keywords...")
    step1_sys = (
        "You are an expert search engine query optimizer. Convert the user's conversational "
        "request into a brief, space-separated string of 3-4 highly specific keywords. "
        "Strip out relative timing expressions (e.g., 'later this week', 'today'), punctuation, "
        "and conversational fluff. Output ONLY the raw keywords and nothing else."
    )
    
    # Run isolated low-temp call against the Ollama API directly for precision
    search_keywords = user_prompt  # Fallback
    try:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": step1_sys},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False,
            "options": {
                "temperature": 0.0
            }
        }
        ollama_url = f"{url.rstrip('/')}/api/chat"
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.post(ollama_url, json=payload, timeout=30.0)
            if resp.status_code == 200:
                search_keywords = resp.json().get("message", {}).get("content", "").strip()
                logger.info(f"[Step 1] Distilled keywords: '{search_keywords}'")
            else:
                logger.warning(f"[Step 1] Failed to reach Ollama: {resp.status_code}. Using original prompt.")
    except Exception as e:
        logger.error(f"[Step 1] Error distilling query: {e}. Using original prompt.")
    
    # ── Step 2: Deep Web Retrieval & Scraping Engine ──────────────────────────
    logger.info(f"[Step 2] Executing search for: '{search_keywords}'")
    target_urls = []
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(search_keywords, max_results=3))
            for r in raw:
                href = r.get("href")
                if href:
                    target_urls.append(href)
    except Exception as e:
        logger.error(f"[Step 2] DDGS Search failed: {e}")
        
    scraped_texts = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    for link in target_urls:
        logger.info(f"[Step 2] Scraping URL: {link}")
        try:
            resp = requests.get(link, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "html.parser")
                
                # Strip out unwanted tags
                for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                    tag.decompose()
                
                text = soup.get_text(separator="\n")
                
                # Clean up whitespace
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                clean_text = "\n".join(chunk for chunk in chunks if chunk)
                
                if clean_text:
                    scraped_texts.append(f"Source: {link}\n{clean_text[:5000]}") # Truncate each source reasonably
            else:
                logger.warning(f"[Step 2] Failed to scrape {link}: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"[Step 2] Error scraping {link}: {e}")
            
    # ── Step 3: RAG Context Assembly ──────────────────────────────────────────
    logger.info("[Step 3] Assembling RAG context block...")
    context_block = ""
    if scraped_texts:
        combined_text = "\n\n---\n\n".join(scraped_texts)
        # Bounding the entire block to ~25k chars so we stay nicely inside the 32k token limit
        if len(combined_text) > 25000:
            combined_text = combined_text[:25000] + "\n...[TRUNCATED]"
            
        context_block = (
            "--- START RETRIEVED REAL-TIME WEB CONTEXT ---\n"
            f"{combined_text}\n"
            "--- END RETRIEVED REAL-TIME WEB CONTEXT ---\n"
        )
        logger.info(f"[Step 3] Context block assembled. Size: {len(context_block)} chars.")
    else:
        logger.warning("[Step 3] No text was successfully scraped.")
        context_block = "--- START RETRIEVED REAL-TIME WEB CONTEXT ---\n[No results could be retrieved]\n--- END RETRIEVED REAL-TIME WEB CONTEXT ---\n"
        
    # ── Step 4: Final Generation Pass ─────────────────────────────────────────
    logger.info("[Step 4] Executing final generation pass...")
    # Provide the context block as the system identity
    final_sys_prompt = (
        "You are an intelligent RAG synthesis assistant. Use the following real-time web context "
        "to accurately answer the user's prompt. Do NOT hallucinate. Only use the facts provided in the context.\n\n"
        f"{context_block}"
    )
    
    try:
        final_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": final_sys_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False,
            "options": {
                "temperature": 0.4,
                "num_ctx": 32768
            }
        }
        ollama_url = f"{url.rstrip('/')}/api/chat"
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.post(ollama_url, json=final_payload, timeout=120.0)
            if resp.status_code == 200:
                final_response = resp.json().get("message", {}).get("content", "").strip()
                logger.info("[Step 4] Final generation complete.")
                return final_response
            else:
                logger.error(f"[Step 4] Final generation failed HTTP {resp.status_code}")
                return f"Sorry, I encountered an error during final generation (HTTP {resp.status_code})."
    except Exception as e:
        logger.exception(f"[Step 4] Error during final generation: {e}")
        return "Sorry, I encountered an error while synthesizing the web context."
