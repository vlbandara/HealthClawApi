from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select

from healthclaw.core.config import get_settings
from healthclaw.db.models import AgentCheckpoint, Memory, OpenLoop, Reminder
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
