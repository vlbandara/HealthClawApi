from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from healthclaw.db.models import User, UserEngagementState
from healthclaw.db.session import SessionLocal
from healthclaw.schemas.events import ConversationEvent
from healthclaw.services.conversation import ConversationService


async def test_user_engagement_state_defaults_are_safe() -> None:
    async with SessionLocal() as session:
        session.add(
            User(
                id="u-engagement-defaults",
                timezone="UTC",
                quiet_start="23:00",
                quiet_end="07:00",
            )
        )
        session.add(UserEngagementState(user_id="u-engagement-defaults"))
        await session.commit()

        engagement = (
            await session.execute(
                select(UserEngagementState).where(
                    UserEngagementState.user_id == "u-engagement-defaults"
                )
            )
        ).scalar_one()

    assert engagement.sentiment_ema == 0.0
    assert engagement.voice_text_ratio == 0.0
    assert engagement.reply_latency_seconds_ema is None
    assert engagement.last_meaningful_exchange_at is None


async def test_text_turn_updates_sentiment_and_reply_latency() -> None:
    async with SessionLocal() as session:
        service = ConversationService(session)
        await service.handle_event(
            ConversationEvent(
                user_id="u-engagement-text",
                content="I feel overwhelmed and exhausted today, and I am pretty stuck.",
            )
        )
        await service.handle_event(
            ConversationEvent(
                user_id="u-engagement-text",
                content="Still feeling stressed and rough today, and my routine slipped again.",
            )
        )

        engagement = (
            await session.execute(
                select(UserEngagementState).where(
                    UserEngagementState.user_id == "u-engagement-text"
                )
            )
        ).scalar_one()

    assert engagement.sentiment_ema < 0.0
    assert engagement.reply_latency_seconds_ema is not None
    assert engagement.last_meaningful_exchange_at is not None


async def test_voice_turn_increases_voice_text_ratio() -> None:
    async with SessionLocal() as session:
        service = ConversationService(session)
        await service.handle_event(
            ConversationEvent(
                user_id="u-engagement-voice",
                content="feeling tired and a bit overwhelmed today",
                content_type="voice_transcript",
            )
        )

        engagement = (
            await session.execute(
                select(UserEngagementState).where(
                    UserEngagementState.user_id == "u-engagement-voice"
                )
            )
        ).scalar_one()

    assert engagement.voice_note_count == 1
    assert engagement.voice_text_ratio > 0.0
    assert engagement.last_meaningful_exchange_at is not None


async def test_command_turn_does_not_update_meaningful_exchange_fields() -> None:
    async with SessionLocal() as session:
        service = ConversationService(session)
        await service.handle_event(
            ConversationEvent(
                user_id="telegram:engagement-command",
                channel="telegram",
                content="/start",
            )
        )

        engagement = (
            await session.execute(
                select(UserEngagementState).where(
                    UserEngagementState.user_id == "telegram:engagement-command"
                )
            )
        ).scalar_one()

    assert engagement.last_seen_at is not None
    assert engagement.last_user_message_at is not None
    assert engagement.sentiment_ema == 0.0
    assert engagement.voice_text_ratio == 0.0
    assert engagement.reply_latency_seconds_ema is None
    assert engagement.last_meaningful_exchange_at is None
    assert engagement.trust_level == 0.3
