from __future__ import annotations

from unittest.mock import patch

from healthclaw.integrations.openrouter import OpenRouterResult
from healthclaw.memory.extractors import extract_memory_mutations_enriched


async def test_llm_extraction_returns_goal_mutation(monkeypatch) -> None:
    """LLM extraction produces a goal mutation from a clear goal statement."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from healthclaw.core.config import get_settings

    get_settings.cache_clear()

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content=(
                '[{"kind":"goal","key":"current_goal",'
                '"value":{"text":"sleep by 10pm"},'
                '"confidence":0.85,"reason":"User stated goal","layer":"goal"}]'
            ),
            model="test",
            usage={"total_tokens": 20},
        )

    with patch(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    ):
        mutations = await extract_memory_mutations_enriched(
            "I am trying to sleep by 10pm but I keep drifting to midnight."
        )

    kinds = {m.kind for m in mutations}
    assert "goal" in kinds
    goal = next(m for m in mutations if m.kind == "goal")
    assert "10pm" in goal.value.get("text", "")
    get_settings.cache_clear()


async def test_llm_extraction_returns_empty_on_disabled_client(monkeypatch) -> None:
    """Returns empty list when LLM client is not configured."""
    from healthclaw.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    get_settings.cache_clear()

    mutations = await extract_memory_mutations_enriched("I want to sleep by 10pm.")
    assert mutations == []
    get_settings.cache_clear()


async def test_llm_extraction_deduplicates_by_kind_key(monkeypatch) -> None:
    """Duplicate (kind, key) pairs from LLM are deduplicated."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from healthclaw.core.config import get_settings

    get_settings.cache_clear()

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content=(
                '[{"kind":"goal","key":"current_goal","value":{"text":"run daily"},'
                '"confidence":0.8,"reason":"first","layer":"goal"},'
                '{"kind":"goal","key":"current_goal","value":{"text":"run every day"},'
                '"confidence":0.7,"reason":"duplicate","layer":"goal"}]'
            ),
            model="test",
            usage={"total_tokens": 25},
        )

    with patch(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    ):
        # Content must be >= 24 chars to pass the length guard
        mutations = await extract_memory_mutations_enriched(
            "I want to run every single day."
        )

    goal_mutations = [m for m in mutations if m.kind == "goal" and m.key == "current_goal"]
    assert len(goal_mutations) == 1
    get_settings.cache_clear()
