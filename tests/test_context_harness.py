from __future__ import annotations

from datetime import UTC, datetime, timedelta

from healthclaw.agent.context_harness import ContextHarness
from healthclaw.core.config import Settings
from tests.factories import make_time_context


def _memory(
    *,
    kind: str,
    key: str,
    text: str,
    confidence: float = 0.8,
    freshness_score: float = 0.9,
    updated_days_ago: int = 0,
) -> dict[str, object]:
    updated_at = datetime.now(UTC) - timedelta(days=updated_days_ago)
    return {
        "id": f"{kind}-{key}",
        "kind": kind,
        "key": key,
        "value": {"text": text},
        "semantic_text": text,
        "confidence": confidence,
        "freshness_score": freshness_score,
        "last_confirmed_at": updated_at,
        "last_accessed_at": updated_at,
        "created_at": updated_at,
        "updated_at": updated_at,
    }


def test_context_harness_prioritizes_current_goal_over_episode() -> None:
    settings = Settings(
        context_harness_mode="active",
        memory_retrieval_limit=1,
        context_harness_memory_chars=140,
    )
    harness = ContextHarness(settings)

    context = harness.build(
        user_content="Help me sleep by 10 tonight",
        time_context=make_time_context(),
        memories=[
            _memory(
                kind="episode",
                key="weekend_chat",
                text="We talked about random weekend plans and lunch.",
                confidence=0.55,
                freshness_score=0.7,
                updated_days_ago=2,
            ),
            _memory(
                kind="goal",
                key="current_goal",
                text="Sleep by 10pm on weeknights.",
                confidence=0.92,
                freshness_score=1.0,
                updated_days_ago=0,
            ),
        ],
        recent_messages=[],
        open_loops=[],
        memory_documents={},
        user_context={"id": "u-harness", "timezone": "UTC"},
        thread_summary="",
        mode="active",
    )

    assert [memory["key"] for memory in context.memories] == ["current_goal"]
    assert all(memory["key"] != "weekend_chat" for memory in context.memories)


def test_context_harness_downgrades_stale_irrelevant_routine() -> None:
    settings = Settings(
        context_harness_mode="active",
        memory_retrieval_limit=1,
        context_harness_memory_chars=130,
    )
    harness = ContextHarness(settings)

    context = harness.build(
        user_content="I need to restart sleep tonight",
        time_context=make_time_context(),
        memories=[
            _memory(
                kind="routine",
                key="old_routine",
                text="Gym at 5am and coffee at 10pm every night.",
                confidence=0.55,
                freshness_score=0.2,
                updated_days_ago=120,
            ),
            _memory(
                kind="goal",
                key="current_goal",
                text="Sleep earlier and cut late-night scrolling.",
                confidence=0.9,
                freshness_score=0.95,
                updated_days_ago=1,
            ),
        ],
        recent_messages=[],
        open_loops=[],
        memory_documents={},
        user_context={"id": "u-harness", "timezone": "UTC"},
        thread_summary="",
        mode="active",
    )

    assert [memory["key"] for memory in context.memories] == ["current_goal"]
    assert all(memory["key"] != "old_routine" for memory in context.memories)


def test_context_harness_packs_recent_messages_and_uses_thread_digest() -> None:
    settings = Settings(
        context_harness_mode="active",
        context_harness_recent_raw_turn_limit=3,
        context_harness_recent_chars=120,
        context_harness_thread_summary_chars=60,
    )
    harness = ContextHarness(settings)

    recent_messages = [
        {"role": "user", "content": f"Message {index} with enough text to matter."}
        for index in range(8)
    ]
    context = harness.build(
        user_content="Pick up where we left off",
        time_context=make_time_context(),
        memories=[],
        recent_messages=recent_messages,
        open_loops=[],
        memory_documents={},
        user_context={"id": "u-harness", "timezone": "UTC"},
        thread_summary="Earlier: sleep routine drifted, then recovery improved a bit.",
        mode="active",
    )

    assert len(context.recent_messages) <= 3
    assert context.thread_summary.startswith("Earlier:")
    assert context.metadata["recent_messages_dropped"] > 0


def test_context_harness_selects_document_sections_instead_of_full_docs() -> None:
    settings = Settings(
        context_harness_mode="active",
        context_harness_document_chars=500,
        context_harness_doc_section_chars=180,
    )
    harness = ContextHarness(settings)

    context = harness.build(
        user_content="Help me with my sleep goal tonight",
        time_context=make_time_context(),
        memories=[
            _memory(
                kind="goal",
                key="current_goal",
                text="Sleep by 10pm.",
            )
        ],
        recent_messages=[],
        open_loops=[],
        memory_documents={
            "MEMORY": (
                "## Goals\n\n- Sleep by 10pm.\n\n"
                "## Recent Episodes\n\n- Talked about lunch.\n\n"
                "## Routines\n\n- Put phone away at 9:30pm."
            )
        },
        user_context={"id": "u-harness", "timezone": "UTC"},
        thread_summary="",
        mode="active",
    )

    assert "MEMORY" in context.memory_documents
    assert "## Goals" in context.memory_documents["MEMORY"]
    assert "## Recent Episodes" not in context.memory_documents["MEMORY"]
