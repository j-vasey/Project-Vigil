import logging
import xml.etree.ElementTree as ET
import httpx

logger = logging.getLogger("project_vigil.tools.search")

# News RSS feeds indexed by topic keyword
_NEWS_RSS_FEEDS = {
    "tech": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "science": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "sport": "https://feeds.bbci.co.uk/news/sport/rss.xml",
    "health": "https://feeds.bbci.co.uk/news/health/rss.xml",
    "default": "https://feeds.bbci.co.uk/news/rss.xml",
}

_NEWS_KEYWORDS = {"news", "headline", "breaking", "latest", "current event", "world event", "today"}


def _is_news_query(query: str) -> bool:
    q = query.lower()
    return any(w in q for w in _NEWS_KEYWORDS)


def _pick_rss_feed(query: str) -> str:
    q = query.lower()
    for keyword, url in _NEWS_RSS_FEEDS.items():
        if keyword != "default" and keyword in q:
            return url
    return _NEWS_RSS_FEEDS["default"]


async def _fetch_rss(feed_url: str, max_items: int = 5) -> list[str]:
    """Fetch and parse an RSS feed, returning formatted result strings."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(feed_url, headers={"User-Agent": "ProjectVigil/1.0"})
        if resp.status_code != 200:
            return []
    root = ET.fromstring(resp.content)
    channel = root.find("channel")
    if channel is None:
        return []
    results = []
    for item in channel.findall("item")[:max_items]:
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")
        title = title_el.text if title_el is not None else "No title"
        link = link_el.text if link_el is not None else ""
        desc = desc_el.text if desc_el is not None else ""
        # Strip CDATA wrappers and basic HTML tags
        import re
        desc = re.sub(r"<[^>]+>", "", desc or "").strip()
        results.append(f"Title: {title}\nURL: {link}\nSnippet: {desc}")
    return results


async def search_web_tool(query: str, max_results: int = 5) -> str:
    """
    Perform a web search using duckduckgo-search (DDGS).

    For news/headline queries, enriches results with a live BBC RSS feed.
    Falls back gracefully if all providers fail.
    """
    query = query.strip()
    logger.info(f"[Search Tool DEBUG] Raw Query requested by model: '{query}'")

    # ── 1. duckduckgo-search (primary) ─────────────────────────────────────
    ddg_results: list[str] = []
    
    def run_ddgs_search(q: str):
        results = []
        try:
            from ddgs import DDGS
            is_news = _is_news_query(q)
            with DDGS() as ddgs:
                if is_news:
                    logger.info(f"[Search Tool] DDG news search: '{q}'")
                    raw = list(ddgs.news(q, max_results=max_results))
                    for r in raw:
                        title = r.get("title", "")
                        url = r.get("url", "")
                        body = r.get("body", "")
                        results.append(f"Title: {title}\nURL: {url}\nSnippet: {body}")
                else:
                    logger.info(f"[Search Tool] DDG text search: '{q}'")
                    raw = list(ddgs.text(q, max_results=max_results))
                    for r in raw:
                        title = r.get("title", "")
                        url = r.get("href", "")
                        body = r.get("body", "")
                        results.append(f"Title: {title}\nURL: {url}\nSnippet: {body}")
        except Exception as e:
            logger.warning(f"[Search Tool] DDGS failed for query '{q}': {e}")
        return results

    ddg_results = run_ddgs_search(query)
    
    # ── 1.5 Fallback Search Query Generator ────────────────────────────────
    if not ddg_results:
        import re
        # Remove conversational noise and time indicators
        stop_words = r"\b(today|tomorrow|yesterday|later|this|week|month|year|game|match|fixture|show|me|tell|find|search|about|what|when|where|who|why|how|is|are|the|a|an|in|on|at|to|for|with)\b"
        cleaned_query = re.sub(stop_words, "", query, flags=re.IGNORECASE)
        # Clean up multiple spaces
        cleaned_query = re.sub(r"\s+", " ", cleaned_query).strip()
        
        if cleaned_query and cleaned_query.lower() != query.lower():
            logger.info(f"[Search Tool DEBUG] Primary search returned 0 results. Fallback triggered with cleaned query: '{cleaned_query}'")
            ddg_results = run_ddgs_search(cleaned_query)

    # ── 2. BBC RSS enrichment for news queries ──────────────────────────────
    rss_results: list[str] = []
    if _is_news_query(query):
        try:
            feed_url = _pick_rss_feed(query)
            logger.info(f"[Search Tool] Fetching RSS feed: {feed_url}")
            rss_results = await _fetch_rss(feed_url, max_items=5)
        except Exception as e:
            logger.warning(f"[Search Tool] RSS fetch failed: {e}")

    # ── 3. Merge and deduplicate results ────────────────────────────────────
    combined: list[str] = []
    seen_titles: set[str] = set()

    for block in ddg_results + rss_results:
        # Extract title for dedup
        first_line = block.split("\n")[0].replace("Title: ", "").strip().lower()
        if first_line and first_line not in seen_titles:
            seen_titles.add(first_line)
            combined.append(block)
        if len(combined) >= max_results:
            break

    if combined:
        final_payload = "\n\n".join(combined)
        # Truncate to roughly 1,500 words
        words = final_payload.split()
        if len(words) > 1500:
            final_payload = " ".join(words[:1500]) + "\n...[TRUNCATED TO 1500 WORDS]"
            
        logger.info(f"[Search Tool DEBUG] Returning {len(combined)} results. Exact Text Payload length: {len(final_payload)} chars.")
        logger.info(f"[Search Tool DEBUG] EXACT PAYLOAD RETURNED:\n{final_payload}")
        return final_payload

    logger.warning(f"[Search Tool] All search providers returned no results for '{query}'")
    return (
        f"Web search for '{query}' returned no results from any provider. "
        "Please inform the user the search service is temporarily unavailable and "
        "answer to the best of your knowledge."
    )
