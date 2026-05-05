from __future__ import annotations

from datetime import UTC, datetime

from healthclaw.agent.response import generate_companion_response
from healthclaw.core.config import get_settings
from healthclaw.integrations.openrouter import OpenRouterResult
from tests.factories import make_time_context


async def test_generate_companion_response_calls_model_once(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    call_count = 0
    seen_metadata: list[dict[str, object]] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        nonlocal call_count
        call_count += 1
        seen_metadata.append(dict(kwargs.get("metadata") or {}))
        return OpenRouterResult(
            content='{"message":"Okay, keep it simple.","actions":[],"memory_proposals":[]}',
            model="google/gemini-2.5-flash-lite",
            usage={"total_tokens": 8},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    generation, metadata = await generate_companion_response(
        user_content="hi",
        time_context=make_time_context(),
        memories=[],
        recent_messages=[],
    )

    assert generation.message == "Okay, keep it simple."
    assert metadata["provider"] == "openrouter"
    assert call_count == 1
    assert seen_metadata == [
        {
            "model_role": "chat",
            "node": "companion_response",
            "user_id": "unknown",
        }
    ]
    get_settings.cache_clear()


async def test_generate_companion_response_does_not_emit_regen_metadata(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured_metadata: list[dict[str, object]] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        captured_metadata.append(dict(kwargs.get("metadata") or {}))
        return OpenRouterResult(
            content=(
                '{"message":"Good morning. Keep water close.",'
                '"actions":[],"memory_proposals":[]}'
            ),
            model="google/gemini-2.5-flash-lite",
            usage={"total_tokens": 8},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    _generation, metadata = await generate_companion_response(
        user_content="ok",
        time_context=make_time_context(),
        memories=[],
        recent_messages=[],
    )

    assert captured_metadata == [
        {
            "model_role": "chat",
            "node": "companion_response",
            "user_id": "unknown",
        }
    ]
    assert "style_violations" not in metadata
    assert "regen" not in captured_metadata[0]
    get_settings.cache_clear()


async def test_generate_companion_response_includes_onboarding_cue_only_when_active(
    monkeypatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured_system_prompts: list[str] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        captured_system_prompts.append(str(messages[0]["content"]))
        return OpenRouterResult(
            content='{"message":"Hi.","actions":[],"memory_proposals":[]}',
            model="google/gemini-2.5-flash-lite",
            usage={"total_tokens": 8},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    _generation, first_metadata = await generate_companion_response(
        user_content="/start",
        time_context=make_time_context(),
        memories=[],
        recent_messages=[],
        onboarding_context={
            "status": "active",
            "missing_fields": ["timezone", "quiet_hours", "support_focus"],
            "meaningful_user_turns": 0,
            "days_since_signup": 0,
            "recently_asked": False,
            "current_turn_has_space": True,
            "should_prompt": True,
        },
    )
    _generation, second_metadata = await generate_companion_response(
        user_content="hello again",
        time_context=make_time_context(),
        memories=[],
        recent_messages=[],
        onboarding_context={
            "status": "passive",
            "missing_fields": ["timezone"],
            "meaningful_user_turns": 5,
            "days_since_signup": 3,
            "recently_asked": False,
            "current_turn_has_space": True,
            "should_prompt": False,
        },
    )

    assert "# Onboarding Context" in captured_system_prompts[0]
    assert "missing_fields: timezone, quiet_hours, support_focus" in captured_system_prompts[0]
    assert "emit set_quiet_hours" in captured_system_prompts[0]
    assert "# Onboarding Context" not in captured_system_prompts[1]
    assert first_metadata["onboarding"]["status"] == "active"
    assert first_metadata["onboarding"]["should_prompt"] is True
    assert second_metadata["onboarding"]["status"] == "passive"
    get_settings.cache_clear()


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


async def test_generate_companion_response_answers_confirmed_time_directly(
    monkeypatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured_messages: list[dict[str, object]] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        captured_messages.extend(messages)
        return OpenRouterResult(
            content=(
                '{"message":"It is 3:37 PM in Asia/Colombo.",'
                '"actions":[],"memory_proposals":[]}'
            ),
            model="google/gemini-2.5-flash-lite",
            usage={"total_tokens": 12},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    generation, metadata = await generate_companion_response(
        user_content="what is the time now",
        time_context=make_time_context(
            human_phrasing={
                "now_user_local": "Monday, 4 May 2026, 3:37 PM (Asia/Colombo, UTC+05:30)",
                "time_user": "3:37 PM",
            },
            timezone_confidence=0.9,
        ),
        memories=[],
        recent_messages=[],
    )

    system_prompt = str(captured_messages[0]["content"])
    user_prompt = str(captured_messages[-1]["content"])
    assert generation.message == "It is 3:37 PM in Asia/Colombo."
    assert metadata["provider"] == "openrouter"
    assert "If they ask for the time" in system_prompt
    assert "give the time plainly and stop" in system_prompt
    assert "# Turn Priority" in user_prompt
    assert "direct utility question" in user_prompt
    assert "biggest drain" not in generation.message
    get_settings.cache_clear()


async def test_generate_companion_response_reports_memory_without_examples(
    monkeypatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured_messages: list[dict[str, object]] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        captured_messages.extend(messages)
        return OpenRouterResult(
            content=(
                '{"message":"No durable memory yet. From this recent chat, '
                'you said Sri Lanka.","actions":[],"memory_proposals":[]}'
            ),
            model="google/gemini-2.5-flash-lite",
            usage={"total_tokens": 18},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    generation, metadata = await generate_companion_response(
        user_content="what you remember about me",
        time_context=make_time_context(),
        memories=[],
        recent_messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello."},
            {"role": "user", "content": "sri lanka"},
        ],
    )

    system_prompt = str(captured_messages[0]["content"])
    user_prompt = str(captured_messages[-1]["content"])
    assert metadata["provider"] == "openrouter"
    assert generation.message.startswith("No durable memory yet")
    assert "Sri Lanka" in generation.message
    assert "Never treat examples" in system_prompt
    assert "- user: sri lanka" in user_prompt
    assert "scattered" not in generation.message
    assert "leaky" not in generation.message
    get_settings.cache_clear()


async def test_generate_companion_response_accepts_short_decline_without_recycling_prompt(
    monkeypatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured_messages: list[dict[str, object]] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        captured_messages.extend(messages)
        return OpenRouterResult(
            content=(
                '{"message":"Fair. We can leave that thread alone.",'
                '"actions":[],"memory_proposals":[]}'
            ),
            model="google/gemini-2.5-flash-lite",
            usage={"total_tokens": 9},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    generation, metadata = await generate_companion_response(
        user_content="nothing",
        time_context=make_time_context(),
        memories=[],
        recent_messages=[],
    )

    system_prompt = str(captured_messages[0]["content"])
    user_prompt = str(captured_messages[-1]["content"])
    assert metadata["provider"] == "openrouter"
    assert generation.message == "Fair. We can leave that thread alone."
    assert "do not recycle a prior suggestion" in system_prompt
    assert "do not re-open an earlier wellness prompt" in user_prompt
    assert "whole life overhaul" not in generation.message
    get_settings.cache_clear()


async def test_generate_companion_response_greeting_does_not_use_cold_start_example(
    monkeypatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured_messages: list[dict[str, object]] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        captured_messages.extend(messages)
        return OpenRouterResult(
            content='{"message":"Hi. I am here.","actions":[],"memory_proposals":[]}',
            model="google/gemini-2.5-flash-lite",
            usage={"total_tokens": 8},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    generation, metadata = await generate_companion_response(
        user_content="hi",
        time_context=make_time_context(),
        memories=[],
        recent_messages=[],
    )

    system_prompt = str(captured_messages[0]["content"])
    assert metadata["provider"] == "openrouter"
    assert generation.message == "Hi. I am here."
    assert "Never copy an example exchange" in system_prompt
    assert "whole life overhaul" not in generation.message
    get_settings.cache_clear()


async def test_generate_companion_response_explains_internal_state_directly(
    monkeypatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured_messages: list[dict[str, object]] = []

    async def fake_chat_completion(self, messages, max_tokens=180, temperature=0.4, **kwargs):
        captured_messages.extend(messages)
        return OpenRouterResult(
            content=(
                '{"message":"I am using the recent chat, durable memory, and current '
                'time context. I see one saved memory item and the latest two turns.",'
                '"actions":[],"memory_proposals":[]}'
            ),
            model="google/gemini-2.5-flash-lite",
            usage={"total_tokens": 20},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )

    generation, metadata = await generate_companion_response(
        user_content="what is going inside",
        time_context=make_time_context(),
        memories=[{"kind": "profile", "key": "timezone", "value": {"text": "Sri Lanka"}}],
        recent_messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hi. I'm here."},
        ],
    )

    system_prompt = str(captured_messages[0]["content"])
    user_prompt = str(captured_messages[-1]["content"])
    assert metadata["provider"] == "openrouter"
    assert "recent chat" in generation.message
    assert "saved memory item" in generation.message
    assert "explain the system state plainly" in system_prompt
    assert "Retrieved Memory" in user_prompt
    get_settings.cache_clear()
