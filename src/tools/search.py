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

    # ── 1. duckduckgo-search (primary) ─────────────────────────────────────
    ddg_results: list[str] = []
    try:
        from ddgs import DDGS
        is_news = _is_news_query(query)

        with DDGS() as ddgs:
            if is_news:
                logger.info(f"[Search Tool] DDG news search: '{query}'")
                raw = list(ddgs.news(query, max_results=max_results))
                for r in raw:
                    title = r.get("title", "")
                    url = r.get("url", "")
                    body = r.get("body", "")
                    ddg_results.append(f"Title: {title}\nURL: {url}\nSnippet: {body}")
            else:
                logger.info(f"[Search Tool] DDG text search: '{query}'")
                raw = list(ddgs.text(query, max_results=max_results))
                for r in raw:
                    title = r.get("title", "")
                    url = r.get("href", "")
                    body = r.get("body", "")
                    ddg_results.append(f"Title: {title}\nURL: {url}\nSnippet: {body}")
    except Exception as e:
        logger.warning(f"[Search Tool] DDGS failed: {e}")

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
        logger.info(f"[Search Tool] Returning {len(combined)} results for '{query}'")
        return "\n\n".join(combined)

    logger.warning(f"[Search Tool] All search providers returned no results for '{query}'")
    return (
        f"Web search for '{query}' returned no results from any provider. "
        "Please inform the user the search service is temporarily unavailable and "
        "answer to the best of your knowledge."
    )
