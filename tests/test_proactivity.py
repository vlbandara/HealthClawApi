from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from healthclaw.db.models import HeartbeatEvent, HeartbeatJob, ProactiveEvent, Reminder, User
from healthclaw.db.session import SessionLocal
from healthclaw.workers.app import process_due_heartbeats, process_due_reminders


async def test_process_due_reminders_sends_telegram(monkeypatch) -> None:
    sent_messages: list[tuple[str, str]] = []

    async def fake_send_message(self, external_id: str, text: str) -> None:
        sent_messages.append((external_id, text))

    monkeypatch.setattr(
        "healthclaw.channels.telegram.TelegramAdapter.send_message",
        fake_send_message,
    )
    async with SessionLocal() as session:
        session.add(
            User(
                id="telegram:123",
                timezone="UTC",
                quiet_start="23:59",
                quiet_end="00:00",
                proactive_enabled=True,
            )
        )
        session.add(
            Reminder(
                user_id="telegram:123",
                text="Time for your wind-down routine.",
                due_at=datetime.now(UTC) - timedelta(minutes=1),
                channel="telegram",
                status="scheduled",
                idempotency_key="reminder-send",
            )
        )
        await session.commit()

    result = await process_due_reminders()

    async with SessionLocal() as session:
        reminder = (
            await session.execute(
                select(Reminder).where(Reminder.idempotency_key == "reminder-send")
            )
        ).scalar_one()
        event = (
            await session.execute(
                select(ProactiveEvent).where(ProactiveEvent.reminder_id == reminder.id)
            )
        ).scalar_one()
    assert result["due"] == 1
    assert result["sent"] == 1
    assert result["suppressed"] == 0
    assert result["deferred"] == 0
    assert result["failed"] == 0
    assert sent_messages == [("123", "Time for your wind-down routine.")]
    assert reminder.status == "sent"
    assert event.decision == "sent"


async def test_process_due_reminders_suppresses_quiet_hours(monkeypatch) -> None:
    async def fake_send_message(self, external_id: str, text: str) -> None:
        raise AssertionError("quiet-hour reminders should not be delivered")

    monkeypatch.setattr(
        "healthclaw.channels.telegram.TelegramAdapter.send_message",
        fake_send_message,
    )
    async with SessionLocal() as session:
        session.add(
            User(
                id="telegram:456",
                timezone="UTC",
                quiet_start="00:00",
                quiet_end="23:59",
                proactive_enabled=True,
            )
        )
        session.add(
            Reminder(
                user_id="telegram:456",
                text="Quiet reminder",
                due_at=datetime.now(UTC) - timedelta(minutes=1),
                channel="telegram",
                status="scheduled",
                idempotency_key="reminder-suppress",
            )
        )
        await session.commit()

    result = await process_due_reminders()

    async with SessionLocal() as session:
        reminder = (
            await session.execute(
                select(Reminder).where(Reminder.idempotency_key == "reminder-suppress")
            )
        ).scalar_one()
        event = (
            await session.execute(
                select(ProactiveEvent).where(ProactiveEvent.reminder_id == reminder.id)
            )
        ).scalar_one()
    assert result["due"] == 1
    assert result["sent"] == 0
    assert result["suppressed"] == 0
    assert result["deferred"] == 1
    assert result["failed"] == 0
    assert reminder.status == "scheduled"
    assert event.decision == "deferred"


async def test_process_due_reminders_respects_cooldown(monkeypatch) -> None:
    async def fake_send_message(self, external_id: str, text: str) -> None:
        raise AssertionError("cooldown reminders should not be delivered")

    monkeypatch.setattr(
        "healthclaw.channels.telegram.TelegramAdapter.send_message",
        fake_send_message,
    )
    async with SessionLocal() as session:
        session.add(
            User(
                id="telegram:789",
                timezone="UTC",
                quiet_start="23:59",
                quiet_end="00:00",
                proactive_enabled=True,
                proactive_cooldown_minutes=180,
            )
        )
        reminder = Reminder(
            user_id="telegram:789",
            text="Cooldown reminder",
            due_at=datetime.now(UTC) - timedelta(minutes=1),
            channel="telegram",
            status="scheduled",
            idempotency_key="reminder-cooldown",
        )
        session.add(reminder)
        await session.flush()
        session.add(
            ProactiveEvent(
                user_id="telegram:789",
                reminder_id=reminder.id,
                decision="sent",
                reason="eligible",
                channel="telegram",
                created_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        await session.commit()

    result = await process_due_reminders()

    async with SessionLocal() as session:
        reminder = (
            await session.execute(
                select(Reminder).where(Reminder.idempotency_key == "reminder-cooldown")
            )
        ).scalar_one()
    assert result["due"] == 1
    assert result["sent"] == 0
    assert result["suppressed"] == 1
    assert result["deferred"] == 0
    assert result["failed"] == 0
    assert reminder.status == "suppressed"


async def test_process_due_heartbeats_sends_open_loop_followup(monkeypatch) -> None:
    sent_messages: list[tuple[str, str]] = []

    async def fake_send_message(self, external_id: str, text: str) -> None:
        sent_messages.append((external_id, text))

    monkeypatch.setattr(
        "healthclaw.channels.telegram.TelegramAdapter.send_message",
        fake_send_message,
    )
    async with SessionLocal() as session:
        session.add(
            User(
                id="telegram:heartbeat",
                timezone="UTC",
                quiet_start="23:59",
                quiet_end="00:00",
                proactive_enabled=True,
                monthly_llm_token_budget=100,
                monthly_llm_tokens_used=0,
            )
        )
        session.add(
            HeartbeatJob(
                user_id="telegram:heartbeat",
                kind="open_loop_followup",
                due_at=datetime.now(UTC) - timedelta(minutes=1),
                channel="telegram",
                payload={"title": "stretch for 10 minutes"},
                idempotency_key="heartbeat-send",
                status="scheduled",
            )
        )
        await session.commit()

    result = await process_due_heartbeats()

    async with SessionLocal() as session:
        job = (
            await session.execute(
                select(HeartbeatJob).where(HeartbeatJob.idempotency_key == "heartbeat-send")
            )
        ).scalar_one()
        event = (
            await session.execute(select(HeartbeatEvent).where(HeartbeatEvent.job_id == job.id))
        ).scalar_one()
    assert result["due"] == 1
    assert result["sent"] == 1
    assert result["suppressed"] == 0
    assert result["deferred"] == 0
    assert result["soft_skipped"] == 0
    assert result["failed"] == 0
    assert sent_messages[0][0] == "heartbeat"
    assert "stretch for 10 minutes" in sent_messages[0][1]
    assert job.status == "sent"
    assert event.decision == "sent"
