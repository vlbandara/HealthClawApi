"""Tests for Tavily web search client (Workstream E)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from healthclaw.integrations.tavily import HEALTH_DOMAINS, TavilyClient, _extract_domain


def _settings(**kwargs):
    s = MagicMock()
    s.tavily_api_key = kwargs.get("tavily_api_key", "test-key")
    s.tavily_search_timeout_ms = 4000
    s.web_search_cache_ttl_seconds = 900
    s.web_search_health_domains_only = kwargs.get("web_search_health_domains_only", True)
    return s


def test_client_enabled_with_key() -> None:
    client = TavilyClient(_settings())
    assert client.enabled is True


def test_client_disabled_without_key() -> None:
    client = TavilyClient(_settings(tavily_api_key=None))
    assert client.enabled is False


def test_health_domains_not_empty() -> None:
    assert "pubmed.ncbi.nlm.nih.gov" in HEALTH_DOMAINS
    assert "who.int" in HEALTH_DOMAINS
    assert len(HEALTH_DOMAINS) >= 6


def test_extract_domain() -> None:
    assert _extract_domain("https://pubmed.ncbi.nlm.nih.gov/123") == "pubmed.ncbi.nlm.nih.gov"
    assert _extract_domain("not-a-url") == "not-a-url"


@pytest.mark.asyncio
async def test_search_returns_empty_when_disabled() -> None:
    client = TavilyClient(_settings(tavily_api_key=None))
    results = await client.search("magnesium sleep")
    assert results == []


@pytest.mark.asyncio
async def test_search_uses_health_domains_when_clinical() -> None:
    """Verify health_clinical=True passes domain whitelist to Tavily."""
    captured_payload = {}

    async def mock_post(url, json=None, **kwargs):  # noqa: ARG001
        captured_payload.update(json or {})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"results": [
            {"title": "Study", "url": "https://pubmed.ncbi.nlm.nih.gov/1", "content": "Magnesium helps sleep."}
        ]})
        return resp

    client = TavilyClient(_settings(web_search_health_domains_only=False))
    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.post = mock_post
        mock_cls.return_value = mock_instance
        results = await client.search("magnesium and sleep", health_clinical=True)

    assert "include_domains" in captured_payload
    assert "pubmed.ncbi.nlm.nih.gov" in captured_payload["include_domains"]
    assert len(results) == 1
    assert results[0]["domain"] == "pubmed.ncbi.nlm.nih.gov"


@pytest.mark.asyncio
async def test_search_caches_result() -> None:
    async def mock_post(url, json=None, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"results": [
            {"title": "T", "url": "https://cdc.gov/1", "content": "cached content"}
        ]})
        return resp

    client = TavilyClient(_settings())
    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.post = mock_post
        mock_cls.return_value = mock_instance
        r1 = await client.search("test query")
        r2 = await client.search("test query")

    assert r1 == r2
    # Second call should have used cache (only one real HTTP call)
    assert mock_cls.call_count == 1
