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

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4):
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
    assert "<relationship_signals>" in prompt
    assert "lower-pressure phrasing" in prompt
    assert "spoken-style phrasing" in prompt
    assert "slow re-entry or delayed replies as failure" in prompt
    assert "continuity references are safe" in prompt
    get_settings.cache_clear()
