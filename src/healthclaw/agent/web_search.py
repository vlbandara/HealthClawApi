"""Web search helper for the per-message response pipeline.

When the LLM emits a web_search action, this module:
  1. Calls Tavily
  2. Re-injects results into the prompt as a Web Sources block
  3. Calls the LLM again (one-shot tool-use loop)

Also used by the inner synthesizer when InnerIntent.needs_web_search=True.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def run_web_search(
    query: str,
    *,
    health_clinical: bool = False,
    settings: Any,
) -> list[dict[str, Any]]:
    """Execute a Tavily search and return structured results."""
    if not getattr(settings, "web_search_enabled", False):
        return []
    from healthclaw.integrations.tavily import TavilyClient
    client = TavilyClient(settings)
    return await client.search(query, max_results=5, health_clinical=health_clinical)


def parse_cited_indices(message: str, result_count: int) -> list[int]:
    """Extract citation indices [1], [2], … from the assistant message."""
    import re
    found = re.findall(r"\[(\d+)\]", message)
    indices = []
    for s in found:
        n = int(s)
        if 1 <= n <= result_count and n not in indices:
            indices.append(n)
    return indices
