from __future__ import annotations

from datetime import UTC, datetime

from healthclaw.agent.response import generate_companion_response
from healthclaw.agent.safety import SafetyDecision
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

    response, metadata = await generate_companion_response(
        user_content="I am trying to restart the routine.",
        safety=SafetyDecision(category="wellness", severity="low", action="support"),
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
    )

    prompt = captured_messages[-1]["content"]
    assert response == "Relationship-aware reply"
    assert metadata["provider"] == "openrouter"
    assert isinstance(metadata.get("streaks_surfaced"), bool)
    assert "<relationship_signals>" in prompt
    assert "lower-pressure phrasing" in prompt
    assert "spoken-style phrasing" in prompt
    assert "slow re-entry or delayed replies as failure" in prompt
    assert "continuity references are safe" in prompt
    get_settings.cache_clear()


async def test_generate_companion_response_surfaces_streak_block_when_gated(monkeypatch) -> None:
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

    response, metadata = await generate_companion_response(
        user_content="Quick check-in.",
        safety=SafetyDecision(category="wellness", severity="low", action="support"),
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
        safety_category="wellness",
    )

    system_prompt = captured_messages[0]["content"]
    assert response == "Streak-aware reply"
    assert metadata["streaks_surfaced"] is True
    assert "# Active rituals" in system_prompt
    assert "morning_check_in" in system_prompt
    assert "7-day streak" in system_prompt
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

    response, metadata = await generate_companion_response(
        user_content="Keep going.",
        safety=SafetyDecision(category="wellness", severity="low", action="support"),
        time_context=make_time_context(),
        memories=[],
        recent_messages=[{"role": "user", "content": "Latest short turn."}],
        thread_summary="Earlier summary: sleep slipped after late-night scrolling.",
        user_context={"id": "u-digest", "timezone": "UTC", "trust_level": 0.5},
    )

    prompt = captured_messages[-1]["content"]
    assert response == "Digest-aware reply"
    assert metadata["conversation_digest_used"] is True
    assert "# Conversation Digest" in prompt
    assert "sleep slipped after late-night scrolling" in prompt
    get_settings.cache_clear()
