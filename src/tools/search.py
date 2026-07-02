import logging
import re
import html as html_lib
import urllib.parse
import httpx

logger = logging.getLogger("project_vigil.tools.search")


async def search_web_tool(query: str) -> str:
    """
    Performs a real web search using DuckDuckGo's HTML search interface and returns
    a summary of the top organic results (titles, links, and snippets).
    Includes an automatic fallback to DuckDuckGo's Instant Answer API on error.
    """
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    params = {
        "q": query.strip()
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers, timeout=12.0)
            if response.status_code == 200:
                html = response.text
                
                # Extract titles/links and snippets
                titles_and_links = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.DOTALL)
                snippets = re.findall(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
                
                results = []
                count = min(len(titles_and_links), len(snippets))
                for i in range(count):
                    raw_href, raw_title = titles_and_links[i]
                    raw_snippet = snippets[i]
                    
                    # Skip sponsored advertisements and tracking redirects
                    if "duckduckgo.com/y.js" in raw_href or "ad_domain" in raw_href:
                        continue
                        
                    title = re.sub(r'<[^>]*>', '', raw_title).strip()
                    snippet = re.sub(r'<[^>]*>', '', raw_snippet).strip()
                    
                    title = html_lib.unescape(title)
                    snippet = html_lib.unescape(snippet)
                    
                    # Resolve DuckDuckGo query redirection URLs
                    parsed_url = raw_href
                    if "uddg=" in raw_href:
                        query_params = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
                        if "uddg" in query_params:
                            parsed_url = query_params["uddg"][0]
                    elif raw_href.startswith("//"):
                        parsed_url = "https:" + raw_href
                        
                    results.append(f"Title: {title}\nURL: {parsed_url}\nSnippet: {snippet}")
                    if len(results) >= 5:
                        break
                        
                if results:
                    summary = "\n\n".join(results)
                    logger.info(f"[Search Tool] Found {len(results)} organic search results for '{query}'")
                    return summary
    except Exception as e:
        logger.warning(f"[Search Tool] HTML search scrape failed: {e}")

    # Fallback to legacy Instant Answer API on scraping error or block
    logger.info(f"[Search Tool] Falling back to DuckDuckGo Instant Answer API for '{query}'")
    api_url = "https://api.duckduckgo.com/"
    api_params = {
        "q": query.strip(),
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(api_url, params=api_params, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                abstract = data.get("AbstractText", "")
                if abstract:
                    logger.info(f"[Search Tool Abstract] Found Abstract for '{query}': {abstract[:60]}")
                    return f"Abstract: {abstract}"
                
                topics = data.get("RelatedTopics", [])
                results = []
                for topic in topics:
                    if "Text" in topic:
                        results.append(topic["Text"])
                    if len(results) >= 3:
                        break
                if results:
                    summary = "\n- ".join(results)
                    logger.info(f"[Search Tool Topics] Found Related Topics for '{query}': {len(results)} items")
                    return f"Related Results:\n- {summary}"
    except Exception as e:
        logger.warning(f"[Search Tool] Fallback DuckDuckGo Instant Answer API failed: {e}")
        
    return "Web search returned no immediate direct answers."
