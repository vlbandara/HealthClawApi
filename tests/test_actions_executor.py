from __future__ import annotations

from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import select

from healthclaw.core.config import get_settings
from healthclaw.db.models import (
    AgentCheckpoint,
    ConversationThread,
    HeartbeatJob,
    Memory,
    OpenLoop,
    Reminder,
    User,
)
from healthclaw.db.session import SessionLocal
from healthclaw.integrations.openrouter import OpenRouterResult


async def test_action_executor_creates_reminder_and_commitment_memory(
    client: AsyncClient, monkeypatch
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content=(
                '{"message":"Set for water at 8pm.",'
                '"actions":[{"type":"create_reminder","text":"drink water",'
                '"due_at_iso":"2026-04-28T20:00:00+05:30"}],'
                '"memory_proposals":[]}'
            ),
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 25},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )
    response = await client.post(
        "/v1/conversations/u-action-reminder/messages",
        json={"content": "remind me to drink water at 8pm", "timezone": "Asia/Colombo"},
    )
    assert response.status_code == 200

    async with SessionLocal() as session:
        reminders = list(
            (
                await session.execute(
                    select(Reminder).where(Reminder.user_id == "u-action-reminder")
                )
            ).scalars()
        )
        memories = list(
            (
                await session.execute(
                    select(Memory).where(
                        Memory.user_id == "u-action-reminder",
                        Memory.kind == "commitment",
                    )
                )
            ).scalars()
        )
    assert len(reminders) == 1
    assert "water" in reminders[0].text.lower()
    assert reminders[0].due_at is not None
    assert any(
        "reminder: drink water" in str(memory.value.get("text", "")).lower()
        for memory in memories
    )
    get_settings.cache_clear()


async def test_action_executor_patches_unbacked_reminder_claim(
    client: AsyncClient, monkeypatch
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content=(
                '{"message":"Set for gym reminder this evening.",'
                '"actions":[],"memory_proposals":[]}'
            ),
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 12},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )
    response = await client.post(
        "/v1/conversations/u-action-patch/messages",
        json={"content": "set a gym reminder"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "Want me to actually set a reminder for that?" in body["response"]

    async with SessionLocal() as session:
        reminders = list(
            (
                await session.execute(
                    select(Reminder).where(Reminder.user_id == "u-action-patch")
                )
            ).scalars()
        )
        checkpoint = (
            await session.execute(
                select(AgentCheckpoint).where(AgentCheckpoint.trace_id == body["trace_id"])
            )
        ).scalar_one()
    assert reminders == []
    assert (
        checkpoint.state["trace_metadata"]["action_execution"]["action.consistency"] == "patched"
    )
    get_settings.cache_clear()


async def test_action_executor_invalid_json_falls_back_without_crash(
    client: AsyncClient, monkeypatch
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content="this is not json but should still return",
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 10},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )
    response = await client.post(
        "/v1/conversations/u-action-badjson/messages",
        json={"content": "hello"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "this is not json" in body["response"]

    async with SessionLocal() as session:
        reminders = list(
            (
                await session.execute(
                    select(Reminder).where(Reminder.user_id == "u-action-badjson")
                )
            ).scalars()
        )
    assert reminders == []
    get_settings.cache_clear()


async def test_action_executor_drops_invalid_due_at(
    client: AsyncClient, monkeypatch
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content=(
                '{"message":"I set that reminder.",'
                '"actions":[{"type":"create_reminder","text":"gym","due_at_iso":"not-a-date"}],'
                '"memory_proposals":[]}'
            ),
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 18},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )
    response = await client.post(
        "/v1/conversations/u-action-invalid-due/messages",
        json={"content": "remind me for gym"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "Want me to actually set a reminder for that?" in body["response"]

    async with SessionLocal() as session:
        reminders = list(
            (
                await session.execute(
                    select(Reminder).where(Reminder.user_id == "u-action-invalid-due")
                )
            ).scalars()
        )
        checkpoint = (
            await session.execute(
                select(AgentCheckpoint).where(AgentCheckpoint.trace_id == body["trace_id"])
            )
        ).scalar_one()
    assert reminders == []
    dropped = checkpoint.state["trace_metadata"]["action_execution"]["dropped"]
    assert any(item.get("reason") == "due_at_invalid" for item in dropped)
    get_settings.cache_clear()


async def test_action_executor_create_open_loop_action(client: AsyncClient, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content=(
                '{"message":"Noted.","actions":[{"type":"create_open_loop",'
                '"title":"go for a walk tonight","kind":"commitment"}],'
                '"memory_proposals":[]}'
            ),
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 16},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )
    response = await client.post(
        "/v1/conversations/u-action-loop/messages",
        json={"content": "I'll go for a walk tonight"},
    )
    assert response.status_code == 200

    async with SessionLocal() as session:
        loops = list(
            (
                await session.execute(
                    select(OpenLoop).where(OpenLoop.user_id == "u-action-loop")
                )
            ).scalars()
        )
    assert len(loops) == 1
    assert loops[0].kind == "commitment"
    assert "walk" in loops[0].title
    get_settings.cache_clear()


async def test_action_executor_closes_open_loop_and_suppresses_jobs(
    client: AsyncClient, monkeypatch
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async with SessionLocal() as session:
        user = User(
            id="u-close-loop",
            timezone="UTC",
            quiet_start="23:00",
            quiet_end="07:00",
        )
        thread = ConversationThread(
            id="thread-close-loop",
            user_id=user.id,
            channel="web",
            is_primary=True,
            open_loop_count=1,
        )
        loop = OpenLoop(
            id="loop-close-1",
            user_id=user.id,
            thread_id=thread.id,
            title="stretch for 10 minutes",
            kind="commitment",
            status="open",
        )
        session.add_all([user, thread, loop])
        session.add(
            HeartbeatJob(
                user_id=user.id,
                open_loop_id=loop.id,
                kind="open_loop_followup",
                due_at=datetime.now(UTC),
                channel="telegram",
                payload={"title": loop.title},
                idempotency_key="close-loop-job",
                status="scheduled",
            )
        )
        await session.commit()

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content=(
                '{"message":"Nice, that closes it.",'
                '"actions":[{"type":"close_open_loop","id":"loop-close-1",'
                '"summary":"finished the stretch","outcome":"completed"}],'
                '"memory_proposals":[]}'
            ),
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 18},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )
    response = await client.post(
        "/v1/conversations/u-close-loop/messages",
        json={"content": "I finished that stretch."},
    )
    assert response.status_code == 200

    async with SessionLocal() as session:
        loop = await session.get(OpenLoop, "loop-close-1")
        job = (
            await session.execute(
                select(HeartbeatJob).where(HeartbeatJob.idempotency_key == "close-loop-job")
            )
        ).scalar_one()
        thread = await session.get(ConversationThread, "thread-close-loop")
        memory = (
            await session.execute(
                select(Memory).where(
                    Memory.user_id == "u-close-loop",
                    Memory.key == "closed_loop:loop-close-1",
                )
            )
        ).scalar_one()

    assert loop is not None
    assert loop.status == "closed"
    assert loop.metadata_["closed_outcome"] == "completed"
    assert loop.metadata_["closed_summary"] == "finished the stretch"
    assert job.status == "suppressed"
    assert job.last_error == "open_loop_closed"
    assert thread is not None
    assert thread.open_loop_count == 0
    assert memory.value["outcome"] == "completed"
    assert memory.value["summary"] == "finished the stretch"
    get_settings.cache_clear()


async def test_action_executor_closes_open_loop_with_dropped_outcome(
    client: AsyncClient, monkeypatch
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async with SessionLocal() as session:
        user = User(
            id="u-drop-loop",
            timezone="UTC",
            quiet_start="23:00",
            quiet_end="07:00",
        )
        thread = ConversationThread(
            id="thread-drop-loop",
            user_id=user.id,
            channel="web",
            is_primary=True,
            open_loop_count=1,
        )
        loop = OpenLoop(
            id="loop-drop-1",
            user_id=user.id,
            thread_id=thread.id,
            title="journal before bed",
            kind="commitment",
            status="open",
        )
        session.add_all([user, thread, loop])
        await session.commit()

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content=(
                '{"message":"That is okay to let go.",'
                '"actions":[{"type":"close_open_loop","id":"loop-drop-1",'
                '"summary":"decided to drop it","outcome":"dropped"}],'
                '"memory_proposals":[]}'
            ),
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 16},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )
    response = await client.post(
        "/v1/conversations/u-drop-loop/messages",
        json={"content": "I am not doing that one anymore."},
    )
    assert response.status_code == 200

    async with SessionLocal() as session:
        loop = await session.get(OpenLoop, "loop-drop-1")
        memory = (
            await session.execute(
                select(Memory).where(
                    Memory.user_id == "u-drop-loop",
                    Memory.key == "closed_loop:loop-drop-1",
                )
            )
        ).scalar_one()

    assert loop is not None
    assert loop.status == "closed"
    assert loop.metadata_["closed_outcome"] == "dropped"
    assert memory.value["outcome"] == "dropped"
    get_settings.cache_clear()


async def test_action_executor_supports_reframed_close_and_replacement_loop(
    client: AsyncClient, monkeypatch
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async with SessionLocal() as session:
        user = User(
            id="u-reframed-loop",
            timezone="UTC",
            quiet_start="23:00",
            quiet_end="07:00",
        )
        thread = ConversationThread(
            id="thread-reframed-loop",
            user_id=user.id,
            channel="web",
            is_primary=True,
            open_loop_count=1,
        )
        loop = OpenLoop(
            id="loop-reframed-1",
            user_id=user.id,
            thread_id=thread.id,
            title="run 5k tonight",
            kind="commitment",
            status="open",
        )
        session.add_all([user, thread, loop])
        session.add(
            HeartbeatJob(
                user_id=user.id,
                open_loop_id=loop.id,
                kind="open_loop_followup",
                due_at=datetime.now(UTC),
                channel="telegram",
                payload={"title": loop.title},
                idempotency_key="reframed-loop-job",
                status="scheduled",
            )
        )
        await session.commit()

    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content=(
                '{"message":"Let us make that smaller.",'
                '"actions":['
                '{"type":"close_open_loop","id":"loop-reframed-1","summary":"scaled it down",'
                '"outcome":"reframed"},'
                '{"type":"create_open_loop","title":"walk for 10 minutes tonight",'
                '"kind":"commitment"}],'
                '"memory_proposals":[]}'
            ),
            model="moonshotai/kimi-k2.6",
            usage={"total_tokens": 24},
        )

    monkeypatch.setattr(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    )
    response = await client.post(
        "/v1/conversations/u-reframed-loop/messages",
        json={"content": "That run is too much tonight."},
    )
    assert response.status_code == 200

    async with SessionLocal() as session:
        loops = list(
            (
                await session.execute(
                    select(OpenLoop)
                    .where(OpenLoop.user_id == "u-reframed-loop")
                    .order_by(OpenLoop.created_at.asc())
                )
            ).scalars()
        )
        old_job = (
            await session.execute(
                select(HeartbeatJob).where(HeartbeatJob.idempotency_key == "reframed-loop-job")
            )
        ).scalar_one()

    assert len(loops) == 2
    assert loops[0].id == "loop-reframed-1"
    assert loops[0].status == "closed"
    assert loops[0].metadata_["closed_outcome"] == "reframed"
    assert loops[1].status == "open"
    assert loops[1].title == "walk for 10 minutes tonight"
    assert old_job.status == "suppressed"
    get_settings.cache_clear()
