"""Tavily web search client — async, cost-tracked, health-domain-aware.

Used as a *tool* the inner synthesizer and per-message response generator
can call when fresh factual information is needed.

Wellness-only boundary:
  When health_clinical=True, restrict domains to trusted health sources.
  Never return results to diagnose, treat, or substitute for clinical care.
  Sources are presented as references; the companion frames them appropriately.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

HEALTH_DOMAINS = [
    "pubmed.ncbi.nlm.nih.gov",
    "cdc.gov",
    "nih.gov",
    "mayoclinic.org",
    "who.int",
    "medlineplus.gov",
    "cochrane.org",
    "nhs.uk",
    "healthline.com",
]

_SEARCH_URL = "https://api.tavily.com/search"


class TavilyClient:
    """Async Tavily search client with cost tracking and Redis/LRU cache."""

    def __init__(self, settings: Any) -> None:
        self.api_key: str | None = getattr(settings, "tavily_api_key", None)
        self.timeout_s: float = getattr(settings, "tavily_search_timeout_ms", 4000) / 1000.0
        self.cache_ttl: int = getattr(settings, "web_search_cache_ttl_seconds", 900)
        self.health_domains_only: bool = getattr(settings, "web_search_health_domains_only", True)
        self._lru: dict[str, tuple[list[dict[str, Any]], datetime]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        health_clinical: bool = False,
    ) -> list[dict[str, Any]]:
        """Return a list of {title, url, snippet, domain} results."""
        if not self.enabled:
            return []

        include_domains = HEALTH_DOMAINS if (health_clinical or self.health_domains_only) else []
        cache_key = self._cache_key(query, include_domains)

        # Check LRU cache
        cached = self._lru.get(cache_key)
        if cached is not None:
            results, cached_at = cached
            age_s = (datetime.now(UTC) - cached_at.replace(tzinfo=UTC)).total_seconds()
            if age_s < self.cache_ttl:
                logger.debug("Tavily cache hit for query=%r", query[:60])
                return results

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                payload: dict[str, Any] = {
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                }
                if include_domains:
                    payload["include_domains"] = include_domains

                response = await client.post(_SEARCH_URL, json=payload)
                response.raise_for_status()
                data = response.json()

            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:300],
                    "domain": _extract_domain(r.get("url", "")),
                }
                for r in data.get("results", [])
            ]
            # Cache result
            self._lru[cache_key] = (results, datetime.now(UTC))
            # Trim LRU to 100 entries
            if len(self._lru) > 100:
                oldest = min(self._lru, key=lambda k: self._lru[k][1])
                del self._lru[oldest]

            logger.debug("Tavily: query=%r results=%d", query[:60], len(results))
            return results

        except httpx.TimeoutException:
            logger.warning("Tavily search timed out for query=%r", query[:60])
            return []
        except Exception as exc:
            logger.warning("Tavily search failed: %s", exc)
            return []

    async def qna_search(self, query: str) -> str:
        """Return a short answer string from Tavily's QnA endpoint."""
        if not self.enabled:
            return ""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                payload = {
                    "api_key": self.api_key,
                    "query": query,
                    "include_answer": True,
                    "max_results": 3,
                }
                if self.health_domains_only:
                    payload["include_domains"] = HEALTH_DOMAINS
                response = await client.post(_SEARCH_URL, json=payload)
                response.raise_for_status()
                data = response.json()
                return str(data.get("answer") or "").strip()
        except Exception as exc:
            logger.warning("Tavily QnA failed: %s", exc)
            return ""

    @staticmethod
    def _cache_key(query: str, domains: list[str]) -> str:
        payload = json.dumps({"q": query.lower().strip(), "d": sorted(domains)}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _extract_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc or url[:50]
    except Exception:
        return url[:50]
