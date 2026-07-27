"""
Shared Serper.dev web-search call. Extracted out of agent_tools.py (Phase 8)
so tools/facility_search/search.py can call the same logic internally for its
own web fallback, without creating a circular import -- agent_tools.py
already imports search_facilities via tools/explore_mode.py, so search.py
importing back from agent_tools.py would cycle. This module has no
dependency on either, so both can import it.
"""
import os
import time

import httpx

from logger import log_error, log_search


async def web_search(query: str) -> tuple[str, list[dict]]:
    log_search(f"Query             │ \"{query}\"")
    t = time.time()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": os.getenv("SERPER_API_KEY"), "Content-Type": "application/json"},
                json={"q": query, "num": 5},
                timeout=10.0
            )
            organic = response.json().get("organic", [])[:5]
            ms = int((time.time() - t) * 1000)
            if not organic:
                log_search(f"No results        │ took={ms}ms")
                return "No results found.", []
            log_search(f"Results returned  │ count={len(organic)} │ took={ms}ms")
            text = "\n".join(
                f"• {r.get('title')}: {r.get('snippet')} ({r.get('link')})"
                for r in organic
            )
            return text, organic
    except Exception as e:
        log_error(f"Search failed     │ query=\"{query}\" │ {e}")
        return f"Search failed: {e}", []
