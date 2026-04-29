from __future__ import annotations

from datetime import UTC, datetime

from healthclaw.agent.response import generate_companion_response
from healthclaw.core.config import get_settings
from healthclaw.integrations.openrouter import OpenRouterResult
from tests.factories import make_time_context


async def test_generate_companion_response_injects_relationship_signals(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured_messages: list[dict[str, object]] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        captured_messages.extend(messages)
        return OpenRouterResult(
            content="Relationship-aware reply",
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 9},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    generation, metadata = await generate_companion_response(
        user_content="I am trying to restart the routine.",
        time_context=make_time_context(),
        memories=[],
        recent_messages=[],
        streaks=[],
        user_context={
            "id": "u-relationship-prompt",
            "timezone": "UTC",
            "trust_level": 0.3,
            "sentiment_ema": -0.6,
            "voice_text_ratio": 0.8,
            "reply_latency_seconds_ema": 50_000.0,
            "last_meaningful_exchange_at": datetime(2026, 4, 21, 1, 0, tzinfo=UTC),
        },
        observable_signals={"message_length": 36, "content_type": "text"},
    )

    system_content = str(captured_messages[0]["content"])
    prompt = str(captured_messages[-1]["content"])
    assert generation.message == "Relationship-aware reply"
    assert metadata["provider"] == "openrouter"
    assert isinstance(metadata.get("streaks_surfaced"), bool)
    assert "# Observable Context" in system_content
    assert "trust_level: 0.30" in system_content
    assert "sentiment_ema: -0.6" in system_content
    assert "voice_text_ratio: 0.8" in system_content
    assert "reply_latency_seconds_ema: 50000.0" in system_content
    assert "<observable_signals>" in prompt
    assert "sentiment_ema=-0.6" in prompt
    assert "voice_text_ratio=0.8" in prompt
    assert "reply_latency_hours=13.89" in prompt
    assert "last_meaningful_exchange_hours_ago=1.5" in prompt
    assert "message_length=36" in prompt
    get_settings.cache_clear()


async def test_generate_companion_response_surfaces_streak_facts_in_observable_context(
    monkeypatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured_messages: list[dict[str, object]] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        captured_messages.extend(messages)
        return OpenRouterResult(
            content="Streak-aware reply",
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 9},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    generation, metadata = await generate_companion_response(
        user_content="Quick check-in.",
        time_context=make_time_context(),
        memories=[],
        recent_messages=[],
        streaks=[
            {
                "kind": "morning_check_in",
                "title": "Morning check-in",
                "streak_count": 7,
                "streak_last_date": "2026-04-23",
            }
        ],
        user_context={"id": "u-streak-prompt", "timezone": "UTC", "trust_level": 0.9},
    )

    system_prompt = captured_messages[0]["content"]
    assert generation.message == "Streak-aware reply"
    assert metadata["streaks_surfaced"] is True
    assert "# Observable Context" in system_prompt
    assert "morning_check_in" in system_prompt
    assert "count=7" in system_prompt
    assert "# Active rituals" not in system_prompt
    get_settings.cache_clear()


async def test_generate_companion_response_includes_conversation_digest(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured_messages: list[dict[str, object]] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        captured_messages.extend(messages)
        return OpenRouterResult(
            content="Digest-aware reply",
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 9},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    generation, metadata = await generate_companion_response(
        user_content="Keep going.",
        time_context=make_time_context(),
        memories=[],
        recent_messages=[{"role": "user", "content": "Latest short turn."}],
        thread_summary="Earlier summary: sleep slipped after late-night scrolling.",
        user_context={"id": "u-digest", "timezone": "UTC", "trust_level": 0.5},
    )

    prompt = captured_messages[-1]["content"]
    assert generation.message == "Digest-aware reply"
    assert metadata["conversation_digest_used"] is True
    assert "# Conversation Digest" in prompt
    assert "sleep slipped after late-night scrolling" in prompt
    get_settings.cache_clear()


async def test_generate_companion_response_includes_open_loop_ids(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured_messages: list[dict[str, object]] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        captured_messages.extend(messages)
        return OpenRouterResult(
            content="Loop-aware reply",
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 9},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    generation, _metadata = await generate_companion_response(
        user_content="I actually finished that.",
        time_context=make_time_context(),
        memories=[],
        open_loops=[
            {
                "id": "loop-1",
                "title": "go for a walk tonight",
                "kind": "commitment",
                "age_hours": 20.0,
            }
        ],
        user_context={"id": "u-open-loop-prompt", "timezone": "UTC", "trust_level": 0.5},
    )

    system_prompt = str(captured_messages[0]["content"])
    user_prompt = str(captured_messages[-1]["content"])
    assert generation.message == "Loop-aware reply"
    assert 'close_open_loop' in system_prompt
    assert 'exact id' in system_prompt
    assert '"completed"' in system_prompt
    assert "id=loop-1" in user_prompt
    assert "title=go for a walk tonight" in user_prompt
    assert "kind=commitment" in user_prompt
    assert "age_hours=20.0" in user_prompt
    get_settings.cache_clear()
