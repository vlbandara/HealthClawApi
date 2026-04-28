from __future__ import annotations

from unittest.mock import patch

import pytest

from healthclaw.agent.nodes import update_memory
from healthclaw.agent.state import AgentState
from healthclaw.integrations.openrouter import OpenRouterResult


def _make_state(
    user_content: str,
    safety_category: str = "wellness",
    memories: list[dict] | None = None,
    content_type: str = "text",
    is_command: bool = False,
) -> AgentState:
    return {
        "user": {"id": "u-episode-test", "timezone": "UTC"},
        "user_content": user_content,
        "channel": "telegram",
        "user_message": {"role": "user", "is_command": is_command},
        "safety": {"category": safety_category, "severity": "low", "action": "support"},
        "memories": memories or [],
        "soul_preferences": {},
        "memory_mutations": [],
        "time_context": {
            "local_datetime": "2026-04-28T10:00:00+05:30",
            "local_date": "2026-04-28",
            "weekday": "Tuesday",
            "part_of_day": "morning",
            "quiet_hours": False,
            "interaction_gap_days": None,
            "long_lapse": False,
        },
        "response": "",
        "trace_metadata": {
            "trace_id": "trace-episode",
            "content_type": content_type,
        },
        "open_loops": [],
        "streaks": [],
        "bridges": [],
        "recent_messages": [],
        "memory_documents": {},
        "thread_summary": "",
        "relationship_signals": [],
    }


async def _mocked_chat_completion(self, messages, **kwargs):
    return OpenRouterResult(
        content="[]",
        model="test",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


@pytest.mark.asyncio
async def test_hi_does_not_create_episode():
    with patch(
        "healthclaw.memory.extractors.OpenRouterClient.chat_completion",
        _mocked_chat_completion,
    ):
        state = _make_state("hi")
        result = await update_memory(state)
        episode_mutations = [
            m for m in result["memory_mutations"]
            if m.get("kind") == "episode" and m.get("key") == "latest_check_in"
        ]
        assert len(episode_mutations) == 0


@pytest.mark.asyncio
async def test_short_non_meaningful_does_not_create_episode():
    with patch(
        "healthclaw.memory.extractors.OpenRouterClient.chat_completion",
        _mocked_chat_completion,
    ):
        state = _make_state("feeling ok")
        result = await update_memory(state)
        episode_mutations = [
            m for m in result["memory_mutations"]
            if m.get("kind") == "episode" and m.get("key") == "latest_check_in"
        ]
        assert len(episode_mutations) == 0


@pytest.mark.asyncio
async def test_meaningful_long_content_creates_episode():
    with patch(
        "healthclaw.memory.extractors.OpenRouterClient.chat_completion",
        _mocked_chat_completion,
    ):
        state = _make_state(
            "feeling stressed about work today, can't focus on anything"
        )
        result = await update_memory(state)
        episode_mutations = [
            m for m in result["memory_mutations"]
            if m.get("kind") == "episode" and m.get("key") == "latest_check_in"
        ]
        assert len(episode_mutations) == 1
        assert "stressed about work" in episode_mutations[0]["value"]["summary"]


@pytest.mark.asyncio
async def test_duplicate_episode_prefix_skipped():
    with patch(
        "healthclaw.memory.extractors.OpenRouterClient.chat_completion",
        _mocked_chat_completion,
    ):
        shared_prefix = (
            "feeling stressed about work today, can't focus on anything "
            "and need to take a break because it's been overwhelming lately "
            "and I feel like I'm losing control of everything that needs "
            "to get done before the end of this week"
        )
        prior_content = shared_prefix + " and I need to rest more"
        state = _make_state(
            shared_prefix,
            memories=[{
                "kind": "episode",
                "key": "latest_check_in",
                "value": {"summary": prior_content},
            }],
        )
        result = await update_memory(state)
        episode_mutations = [
            m for m in result["memory_mutations"]
            if m.get("kind") == "episode" and m.get("key") == "latest_check_in"
        ]
        assert len(episode_mutations) == 0


@pytest.mark.asyncio
async def test_different_episode_prefix_creates_episode():
    with patch(
        "healthclaw.memory.extractors.OpenRouterClient.chat_completion",
        _mocked_chat_completion,
    ):
        state = _make_state(
            "feeling stressed about work today, can't focus",
            memories=[{
                "kind": "episode",
                "key": "latest_check_in",
                "value": {"summary": "feeling great about the weekend"},
            }],
        )
        result = await update_memory(state)
        episode_mutations = [
            m for m in result["memory_mutations"]
            if m.get("kind") == "episode" and m.get("key") == "latest_check_in"
        ]
        assert len(episode_mutations) == 1


@pytest.mark.asyncio
async def test_command_does_not_create_episode():
    with patch(
        "healthclaw.memory.extractors.OpenRouterClient.chat_completion",
        _mocked_chat_completion,
    ):
        state = _make_state(
            "feeling stressed about work today, can't focus on anything",
            is_command=True,
        )
        result = await update_memory(state)
        episode_mutations = [
            m for m in result["memory_mutations"]
            if m.get("kind") == "episode" and m.get("key") == "latest_check_in"
        ]
        assert len(episode_mutations) == 0


@pytest.mark.asyncio
async def test_non_wellness_category_does_not_create_episode():
    with patch(
        "healthclaw.memory.extractors.OpenRouterClient.chat_completion",
        _mocked_chat_completion,
    ):
        state = _make_state(
            "feeling stressed about work today, can't focus on anything",
            safety_category="medical",
        )
        result = await update_memory(state)
        episode_mutations = [
            m for m in result["memory_mutations"]
            if m.get("kind") == "episode" and m.get("key") == "latest_check_in"
        ]
        assert len(episode_mutations) == 0