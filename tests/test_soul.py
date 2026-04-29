from __future__ import annotations

from healthclaw.agent.soul import system_prompt


def test_system_prompt_uses_companion_brief_and_examples() -> None:
    prompt = system_prompt()

    assert "# Companion Brief" in prompt
    assert "# Example Exchanges" in prompt
    assert "private wellbeing companion" in prompt
    assert "continuity first" in prompt.lower()
    assert "Put the phone down for ten minutes" in prompt
    assert "call or text 988 now" in prompt
    assert "Chest pain is not something to guess through over chat" in prompt


def test_system_prompt_surfaces_observable_context_without_tone_bands() -> None:
    prompt = system_prompt(
        soul_preferences={
            "tone_preferences": {"directness": "plain"},
            "response_preferences": {"length": "short"},
        },
        user_id="u-observable",
        timezone="Asia/Colombo",
        local_time={"part_of_day": "night", "quiet_hours": True},
        lifecycle_stage="early",
        recent_message_count=12,
        trust_level=0.68,
        sentiment_ema=-0.42,
        voice_text_ratio=0.71,
        reply_latency_seconds_ema=50_400.0,
        streaks=[
            {
                "kind": "morning_check_in",
                "title": "Morning check-in",
                "streak_count": 7,
                "streak_last_date": "2026-04-23",
            }
        ],
        open_loops=[
            {"id": "loop-1", "title": "go for a walk", "kind": "commitment", "age_hours": 20.0}
        ],
    )

    assert "# Observable Context" in prompt
    assert "lifecycle_hint: early" in prompt
    assert "recent_message_count: 12" in prompt
    assert "trust_level: 0.68" in prompt
    assert "sentiment_ema: -0.42" in prompt
    assert "voice_text_ratio: 0.71" in prompt
    assert "reply_latency_seconds_ema: 50400.0" in prompt
    assert "tone.directness=plain" in prompt
    assert "response.length=short" in prompt
    assert "kind=morning_check_in" in prompt
    assert "id=loop-1" in prompt
    assert "Trust Tone Band" not in prompt
    assert "Runtime Voice Contract" not in prompt


def test_system_prompt_includes_markdown_memory_documents() -> None:
    prompt = system_prompt(
        user_id="u-doc",
        timezone="Asia/Colombo",
        memory_documents={
            "USER": "## Stable Profile\n- Preferred name: Vinodh",
            "MEMORY": "## Durable Memory\n- Likes concise replies",
        },
    )

    assert "# User.md" in prompt
    assert "Preferred name: Vinodh" in prompt
    assert "# Memory.md" in prompt
    assert "Likes concise replies" in prompt
