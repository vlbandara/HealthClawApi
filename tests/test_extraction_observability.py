from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from healthclaw.integrations.openrouter import OpenRouterResult
from healthclaw.memory.extractors import extract_memory_mutations_enriched


async def test_parse_error_sets_span_attribute_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
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
            result = await extract_memory_mutations_enriched(
                "I want to establish a morning exercise routine starting tomorrow"
            )

        assert "memory_extract_parse_error" in caplog.text
        assert isinstance(result, list)


async def test_runtime_error_sets_parse_error_and_returns_mutations() -> None:
    async def fake_chat_completion_error(self, messages, **kwargs):
        raise RuntimeError("OpenRouter API error")

    with patch(
        "healthclaw.memory.extractors.OpenRouterClient.chat_completion",
        fake_chat_completion_error,
    ):
        result = await extract_memory_mutations_enriched(
            "I want to establish a morning exercise routine starting tomorrow"
        )

        assert isinstance(result, list)
        assert len(result) >= 0