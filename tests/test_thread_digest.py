from __future__ import annotations

from unittest.mock import patch

import pytest

from healthclaw.agent.thread_digest import compact_thread_summary
from healthclaw.db.models import ConversationThread, Message, User
from healthclaw.db.session import SessionLocal
from healthclaw.integrations.openrouter import OpenRouterResult
from healthclaw.services.conversation import ConversationService

LONG_CONTENT = "Turn content here that is definitely longer than forty chars"


def _make_thread(
    user_id: str,
    thread_id: str,
    summary: str = "",
) -> ConversationThread:
    return ConversationThread(
        id=thread_id,
        user_id=user_id,
        channel="web",
        summary=summary,
    )


def _make_user(user_id: str) -> User:
    return User(
        id=user_id,
        timezone="UTC",
        quiet_start="22:00",
        quiet_end="07:00",
        proactive_enabled=True,
    )


def _make_message(
    message_id: str,
    thread_id: str,
    user_id: str,
    role: str,
    content: str,
) -> Message:
    return Message(
        id=message_id,
        thread_id=thread_id,
        user_id=user_id,
        role=role,
        content=content,
        channel="web",
    )


async def test_turns_1_to_5_do_not_trigger_llm_compaction() -> None:
    async with SessionLocal() as session:
        user = _make_user("u-thread-test-1")
        thread = _make_thread("u-thread-test-1", "thread-1")
        session.add(user)
        session.add(thread)

        async def fake_chat_completion(self, messages, **kwargs):
            pytest.fail("LLM should not be called for turns 1-5")

        with patch(
            "healthclaw.agent.thread_digest.OpenRouterClient.chat_completion",
            fake_chat_completion,
        ):
            svc = ConversationService(session)
            for i in range(1, 6):
                msg_user = _make_message(
                    f"m{i}",
                    "thread-1",
                    "u-thread-test-1",
                    "user",
                    f"{LONG_CONTENT} {i}",
                )
                msg_asst = _make_message(
                    f"a{i}",
                    "thread-1",
                    "u-thread-test-1",
                    "assistant",
                    f"Response to turn {i}",
                )
                session.add(msg_user)
                session.add(msg_asst)
                await session.flush()
                await svc._update_thread_summary(
                    thread,
                    f"{LONG_CONTENT} {i}",
                    f"Response to turn {i}",
                    "u-thread-test-1",
                )
                await session.flush()

        assert thread.summary != ""
        assert "Turn" in thread.summary


async def test_turn_6_triggers_llm_compaction() -> None:
    async with SessionLocal() as session:
        user = _make_user("u-thread-test-2")
        thread = _make_thread("u-thread-test-2", "thread-2")
        session.add(user)
        session.add(thread)

        llm_called = False
        captured_metadata = None

        async def fake_chat_completion(self, messages, **kwargs):
            nonlocal llm_called, captured_metadata
            llm_called = True
            captured_metadata = kwargs.get("metadata", {})
            return OpenRouterResult(
                content=(
                    "This is a compacted summary of the conversation "
                    "about sleep and wellness."
                ),
                model="google/gemini-2.5-flash-lite",
                usage={
                    "prompt_tokens": 50,
                    "completion_tokens": 30,
                    "total_tokens": 80,
                },
            )

        with patch(
            "healthclaw.agent.thread_digest.OpenRouterClient.chat_completion",
            fake_chat_completion,
        ):
            svc = ConversationService(session)
            for i in range(1, 7):
                msg_user = _make_message(
                    f"m{i}",
                    "thread-2",
                    "u-thread-test-2",
                    "user",
                    f"{LONG_CONTENT} {i}",
                )
                msg_asst = _make_message(
                    f"a{i}",
                    "thread-2",
                    "u-thread-test-2",
                    "assistant",
                    f"Response to turn {i}",
                )
                session.add(msg_user)
                session.add(msg_asst)
                await session.flush()
                await svc._update_thread_summary(
                    thread,
                    f"{LONG_CONTENT} {i}",
                    f"Response to turn {i}",
                    "u-thread-test-2",
                )
                await session.flush()

        assert llm_called, "LLM should be called on turn 6"
        assert captured_metadata is not None
        assert captured_metadata.get("model_role") == "thread_digest"
        assert "sleep" in thread.summary or "wellness" in thread.summary


async def test_compaction_llm_failure_keeps_prior_summary() -> None:
    prior_summary = "Prior summary that should remain"
    async with SessionLocal() as session:
        user = _make_user("u-thread-test-3")
        thread = _make_thread("u-thread-test-3", "thread-3", summary=prior_summary)
        session.add(user)
        session.add(thread)

        async def fake_chat_completion_error(self, messages, **kwargs):
            raise RuntimeError("OpenRouter API failure")

        with patch(
            "healthclaw.agent.thread_digest.OpenRouterClient.chat_completion",
            fake_chat_completion_error,
        ):
            svc = ConversationService(session)
            for i in range(1, 7):
                msg_user = _make_message(
                    f"m{i}",
                    "thread-3",
                    "u-thread-test-3",
                    "user",
                    f"{LONG_CONTENT} {i}",
                )
                msg_asst = _make_message(
                    f"a{i}",
                    "thread-3",
                    "u-thread-test-3",
                    "assistant",
                    f"Response to turn {i}",
                )
                session.add(msg_user)
                session.add(msg_asst)
                await session.flush()
                await svc._update_thread_summary(
                    thread,
                    f"{LONG_CONTENT} {i}",
                    f"Response to turn {i}",
                    "u-thread-test-3",
                )
                await session.flush()

        assert "Prior summary that should remain" in thread.summary
        assert "Response to turn 5" in thread.summary
        assert "Response to turn 6" not in thread.summary, (
            "Turn 6 summary should not include turn 6 content after LLM failure"
        )


async def test_compact_thread_summary_respects_max_chars() -> None:
    long_digest = "A" * 2000

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content=long_digest,
            model="google/gemini-2.5-flash-lite",
            usage={
                "prompt_tokens": 50,
                "completion_tokens": 1000,
                "total_tokens": 1050,
            },
        )

    with patch(
        "healthclaw.agent.thread_digest.OpenRouterClient.chat_completion",
        fake_chat_completion,
    ):
        result = await compact_thread_summary(
            prior_summary="old summary",
            recent_turns=[
                {"role": "user", "content": "Message 1"},
                {"role": "assistant", "content": "Response 1"},
            ],
            user_id="u-test",
            thread_id="thread-test",
        )

    assert len(result) <= 1200, "Result should be truncated to max_chars"
