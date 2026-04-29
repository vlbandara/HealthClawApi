from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from healthclaw.db.models import HeartbeatJob, User
from healthclaw.db.session import SessionLocal
from healthclaw.heartbeat.profile import (
    canonicalize_heartbeat_md,
    merge_dream_heartbeat_md,
    parse_heartbeat_md,
)
from healthclaw.heartbeat.service import HeartbeatService
from healthclaw.memory.dream import DreamService


def test_canonicalize_heartbeat_md_normalizes_directives() -> None:
    raw = "  Keep it low pressure. \n wake: bedtime reset \n allow_long_silence: TRUE "

    normalized = canonicalize_heartbeat_md(raw)
    parsed = parse_heartbeat_md(normalized)

    assert normalized == (
        "Keep it low pressure.\n\n"
        "wake: bedtime reset\n"
        "allow_long_silence: true"
    )
    assert parsed.standing_intent == "Keep it low pressure."
    assert parsed.wake_text == "bedtime reset"
    assert parsed.allow_long_silence is True


def test_merge_dream_heartbeat_md_preserves_existing_directives() -> None:
    existing = canonicalize_heartbeat_md(
        "Keep it gentle.\n\nwake: evening wind-down\nallow_long_silence: true"
    )

    merged = merge_dream_heartbeat_md(existing, "wake: sleep consistency")
    parsed = parse_heartbeat_md(merged)

    assert parsed.standing_intent == "Keep it gentle."
    assert parsed.wake_text == "sleep consistency"
    assert parsed.allow_long_silence is True


async def test_dream_service_apply_heartbeat_md_preserves_existing_directives() -> None:
    user = User(
        id="u-dream-heartbeat",
        timezone="UTC",
        quiet_start="23:00",
        quiet_end="07:00",
        heartbeat_md=canonicalize_heartbeat_md(
            "Keep it gentle.\n\nwake: evening wind-down\nallow_long_silence: true"
        ),
    )

    previous, applied = await DreamService._apply_heartbeat_md(
        user, {"text": "wake: sleep consistency"}
    )
    parsed = parse_heartbeat_md(user.heartbeat_md)

    assert applied is True
    assert previous == {
        "heartbeat_md": "Keep it gentle.\n\nwake: evening wind-down\nallow_long_silence: true"
    }
    assert parsed.standing_intent == "Keep it gentle."
    assert parsed.wake_text == "sleep consistency"
    assert parsed.allow_long_silence is True


async def test_schedule_autonomous_wake_uses_parsed_wake_directive() -> None:
    async with SessionLocal() as session:
        session.add(
            User(
                id="u-heartbeat-wake",
                timezone="UTC",
                quiet_start="23:00",
                quiet_end="07:00",
                proactive_enabled=True,
                heartbeat_md=canonicalize_heartbeat_md(
                    "Keep it low pressure.\nallow_long_silence: true\nwake: bedtime drift"
                ),
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        heartbeat = HeartbeatService(session)
        result = await heartbeat.schedule_autonomous_wake(datetime(2026, 4, 29, 12, 0, tzinfo=UTC))
        await session.commit()

    async with SessionLocal() as session:
        job = (
            await session.execute(
                select(HeartbeatJob).where(HeartbeatJob.user_id == "u-heartbeat-wake")
            )
        ).scalar_one()

    assert result["scheduled"] == 1
    assert result["skipped_no_trigger"] == 0
    assert job.kind == "autonomous_tick"
    assert job.payload["dream_nominated"] is True
    assert job.payload["heartbeat_intent"] is True
    assert job.payload["wake_text"] == "bedtime drift"
