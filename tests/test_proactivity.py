from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from healthclaw.agent.wellbeing import WellbeingDecision
from healthclaw.db.models import HeartbeatEvent, HeartbeatJob, ProactiveEvent, Reminder, User
from healthclaw.db.session import SessionLocal
from healthclaw.workers.app import process_due_heartbeats, process_due_reminders


async def test_process_due_reminders_sends_telegram(monkeypatch) -> None:
    sent_messages: list[tuple[str, str]] = []

    async def fake_send_message(self, external_id: str, text: str, **_kwargs) -> None:
        sent_messages.append((external_id, text))

    async def fake_reflect_on_wellbeing(*, settings, user_id, decision_input, metadata):
        return WellbeingDecision(
            reach_out=True,
            when="now",
            message_seed="Ease into your wind-down routine when you have a minute.",
            rationale="a calm nudge fits the current gap",
            model="test-model",
            decision_input=decision_input,
        )

    monkeypatch.setattr(
        "healthclaw.channels.telegram.TelegramAdapter.send_message",
        fake_send_message,
    )
    monkeypatch.setattr(
        "healthclaw.proactivity.service.reflect_on_wellbeing",
        fake_reflect_on_wellbeing,
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
    assert sent_messages == [("123", "Ease into your wind-down routine when you have a minute.")]
    assert reminder.status == "sent"
    assert event.decision == "sent"
    assert event.reason == "a calm nudge fits the current gap"


async def test_process_due_reminders_defers_on_reflection_delay(monkeypatch) -> None:
    async def fake_send_message(self, external_id: str, text: str, **_kwargs) -> None:
        raise AssertionError("deferred reminders should not be delivered")

    async def fake_reflect_on_wellbeing(*, settings, user_id, decision_input, metadata):
        return WellbeingDecision(
            reach_out=True,
            when="in_30m",
            message_seed="",
            rationale="wait until the quiet window passes",
            model="test-model",
            decision_input=decision_input,
        )

    monkeypatch.setattr(
        "healthclaw.channels.telegram.TelegramAdapter.send_message",
        fake_send_message,
    )
    monkeypatch.setattr(
        "healthclaw.proactivity.service.reflect_on_wellbeing",
        fake_reflect_on_wellbeing,
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
                idempotency_key="reminder-defer",
            )
        )
        await session.commit()

    result = await process_due_reminders()

    async with SessionLocal() as session:
        reminder = (
            await session.execute(
                select(Reminder).where(Reminder.idempotency_key == "reminder-defer")
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
    assert event.reason == "wait until the quiet window passes"


async def test_process_due_reminders_applies_daily_cap_floor(monkeypatch) -> None:
    async def fake_send_message(self, external_id: str, text: str, **_kwargs) -> None:
        raise AssertionError("daily-cap reminders should not be delivered")

    async def fake_reflect_on_wellbeing(*, settings, user_id, decision_input, metadata):
        return WellbeingDecision(
            reach_out=True,
            when="now",
            message_seed="Check in now.",
            rationale="a nudge would make sense without the cap",
            model="test-model",
            decision_input=decision_input,
        )

    monkeypatch.setattr(
        "healthclaw.channels.telegram.TelegramAdapter.send_message",
        fake_send_message,
    )
    monkeypatch.setattr(
        "healthclaw.proactivity.service.reflect_on_wellbeing",
        fake_reflect_on_wellbeing,
    )
    async with SessionLocal() as session:
        session.add(
            User(
                id="telegram:789",
                timezone="UTC",
                quiet_start="23:59",
                quiet_end="00:00",
                proactive_enabled=True,
                proactive_max_per_day=1,
            )
        )
        reminder = Reminder(
            user_id="telegram:789",
            text="Cap reminder",
            due_at=datetime.now(UTC) - timedelta(minutes=1),
            channel="telegram",
            status="scheduled",
            idempotency_key="reminder-cap",
        )
        session.add(reminder)
        await session.flush()
        session.add(
            HeartbeatEvent(
                user_id="telegram:789",
                decision="sent",
                reason="already sent",
                channel="telegram",
                created_at=datetime.now(UTC) - timedelta(minutes=10),
            )
        )
        await session.commit()

    result = await process_due_reminders()

    async with SessionLocal() as session:
        reminder = (
            await session.execute(
                select(Reminder).where(Reminder.idempotency_key == "reminder-cap")
            )
        ).scalar_one()
        event = (
            await session.execute(
                select(ProactiveEvent).where(ProactiveEvent.reminder_id == reminder.id)
            )
        ).scalar_one()
    assert result["due"] == 1
    assert result["sent"] == 0
    assert result["suppressed"] == 1
    assert result["deferred"] == 0
    assert result["failed"] == 0
    assert reminder.status == "suppressed"
    assert event.decision == "suppressed"
    assert event.reason == "daily delivery cap reached"


async def test_process_due_heartbeats_sends_open_loop_followup(monkeypatch) -> None:
    sent_messages: list[tuple[str, str]] = []

    async def fake_send_message(self, external_id: str, text: str, **_kwargs) -> None:
        sent_messages.append((external_id, text))

    async def fake_reflect_on_wellbeing(*, settings, user_id, decision_input, metadata):
        return WellbeingDecision(
            reach_out=True,
            when="now",
            message_seed="Small follow-up: how did the stretch go?",
            rationale="the open loop has waited long enough",
            model="test-model",
            decision_input=decision_input,
        )

    monkeypatch.setattr(
        "healthclaw.channels.telegram.TelegramAdapter.send_message",
        fake_send_message,
    )
    monkeypatch.setattr(
        "healthclaw.heartbeat.decision.reflect_on_wellbeing",
        fake_reflect_on_wellbeing,
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
    assert sent_messages == [("heartbeat", "Small follow-up: how did the stretch go?")]
    assert job.status == "sent"
    assert event.decision == "sent"
    assert event.reason == "the open loop has waited long enough"
