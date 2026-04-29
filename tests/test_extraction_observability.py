from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from healthclaw.core.config import get_settings
from healthclaw.integrations.openrouter import OpenRouterResult
from healthclaw.memory.extractors import extract_memory_mutations_enriched

_CONTENT = "I want to establish a morning exercise routine starting tomorrow"


async def test_parse_error_sets_span_attribute_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content="not json at all",
            model="test",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    with patch(
        "healthclaw.memory.extractors.OpenRouterClient.chat_completion",
        fake_chat_completion,
    ):
        with caplog.at_level(logging.WARNING):
            result = await extract_memory_mutations_enriched(_CONTENT)

    assert "memory_extract_parse_error" in caplog.text
    assert isinstance(result, list)
    get_settings.cache_clear()


async def test_runtime_error_sets_parse_error_and_returns_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def fake_chat_completion_error(self, messages, **kwargs):
        raise RuntimeError("OpenRouter API error")

    with patch(
        "healthclaw.memory.extractors.OpenRouterClient.chat_completion",
        fake_chat_completion_error,
    ):
        result = await extract_memory_mutations_enriched(_CONTENT)

    assert isinstance(result, list)
    get_settings.cache_clear()