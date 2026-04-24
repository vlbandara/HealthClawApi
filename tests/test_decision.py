from __future__ import annotations

from datetime import UTC, datetime, timedelta

from healthclaw.core.config import Settings
from healthclaw.db.models import HeartbeatJob, UserEngagementState
from healthclaw.db.session import SessionLocal
from healthclaw.heartbeat.decision import decide
from healthclaw.heartbeat.service import HeartbeatService
from tests.factories import make_time_context, make_user


async def test_decision_gate_returns_skip_on_quiet_hours() -> None:
    job = HeartbeatJob(id="job-str", user_id="u-decision", kind="proactive")
    user = make_user("u-decision", heartbeat_md="")
    
    # quiet_hours is True
    time_context = make_time_context(
        part_of_day="night",
        quiet_hours=True,
        interaction_gap_days=0.5,
        long_lapse=False
    )
    
    settings = Settings(openrouter_api_key="test-key")
    
    result = await decide(
        job=job,
        user=user,
        time_context=time_context,
        open_loops=[],
        recent_exchanges=[],
        settings=settings
    )

    assert result.decision == "skip"
    assert result.reason == "quiet hours active"
    assert result.action is None


async def test_soft_gate_skips_ritual_after_48h_silence() -> None:
    async with SessionLocal() as session:
        user = make_user(
            "u-silent",
            last_active_at=datetime.now(UTC) - timedelta(hours=49),
            heartbeat_md="",
        )
        job = HeartbeatJob(
            id="job-silent",
            user_id=user.id,
            kind="ritual",
            due_at=datetime.now(UTC),
            channel="telegram",
            payload={"prompt_template": "Morning?"},
            idempotency_key="job-silent",
        )
        session.add_all([user, job])
        await session.flush()

        decision, action, reason, decision_input, model = await HeartbeatService(
            session,
            Settings(openrouter_api_key="test-key"),
        ).should_send_soft(job, user, datetime.now(UTC))

        assert decision == "skip"
        assert action is None
        assert reason == "user_silent_48h"
        assert decision_input["candidate_trigger"]["kind"] == "ritual"
        assert model is None


async def test_soft_gate_skips_autonomous_tick_after_recent_meaningful_exchange() -> None:
    async with SessionLocal() as session:
        user = make_user(
            "u-recent-meaningful",
            last_active_at=datetime.now(UTC) - timedelta(hours=2),
            heartbeat_md="",
        )
        job = HeartbeatJob(
            id="job-recent-meaningful",
            user_id=user.id,
            kind="autonomous_tick",
            due_at=datetime.now(UTC),
            channel="telegram",
            payload={},
            idempotency_key="job-recent-meaningful",
        )
        engagement = UserEngagementState(
            user_id=user.id,
            last_meaningful_exchange_at=datetime.now(UTC) - timedelta(hours=3),
            sentiment_ema=0.1,
        )
        session.add_all([user, job, engagement])
        await session.flush()

        decision, action, reason, decision_input, model = await HeartbeatService(
            session,
            Settings(openrouter_api_key="test-key"),
        ).should_send_soft(job, user, datetime.now(UTC))

        assert decision == "skip"
        assert action is None
        assert reason == "recent_meaningful_exchange"
        assert decision_input["relationship"]["bands"]["continuity_fresh"] is True
        assert model is None


async def test_soft_gate_skips_low_sentiment_autonomous_tick_without_trigger() -> None:
    async with SessionLocal() as session:
        user = make_user(
            "u-low-sentiment",
            last_active_at=datetime.now(UTC) - timedelta(days=2),
            heartbeat_md="",
        )
        job = HeartbeatJob(
            id="job-low-sentiment",
            user_id=user.id,
            kind="autonomous_tick",
            due_at=datetime.now(UTC),
            channel="telegram",
            payload={},
            idempotency_key="job-low-sentiment",
        )
        engagement = UserEngagementState(
            user_id=user.id,
            sentiment_ema=-0.8,
            last_meaningful_exchange_at=datetime.now(UTC) - timedelta(days=3),
        )
        session.add_all([user, job, engagement])
        await session.flush()

        decision, action, reason, decision_input, model = await HeartbeatService(
            session,
            Settings(openrouter_api_key="test-key"),
        ).should_send_soft(job, user, datetime.now(UTC))

        assert decision == "skip"
        assert action is None
        assert reason == "low_sentiment_without_trigger"
        assert decision_input["relationship"]["sentiment_ema"] == -0.8
        assert decision_input["relationship"]["bands"]["low_pressure"] is True
        assert model is None
