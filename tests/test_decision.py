from __future__ import annotations

from datetime import UTC, datetime, timedelta

from healthclaw.agent.wellbeing import WellbeingDecision
from healthclaw.core.config import Settings
from healthclaw.db.models import HeartbeatEvent, HeartbeatJob, ProactiveEvent, UserEngagementState
from healthclaw.db.session import SessionLocal
from healthclaw.heartbeat.decision import decide
from healthclaw.heartbeat.service import HeartbeatService
from tests.factories import make_time_context, make_user


async def test_decide_routes_heartbeat_context_through_reflection(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_reflect_on_wellbeing(*, settings, user_id, decision_input, metadata):
        captured["user_id"] = user_id
        captured["decision_input"] = decision_input
        captured["metadata"] = metadata
        return WellbeingDecision(
            reach_out=False,
            when="hold",
            message_seed="",
            rationale="late night and low value right now",
            model="test-model",
            decision_input=decision_input,
        )

    monkeypatch.setattr(
        "healthclaw.heartbeat.decision.reflect_on_wellbeing",
        fake_reflect_on_wellbeing,
    )

    job = HeartbeatJob(
        id="job-decision",
        user_id="u-decision",
        kind="autonomous_tick",
        channel="telegram",
        payload={"reason": "autonomous_wake_check"},
    )
    user = make_user("u-decision", heartbeat_md="Reach out if I disappear for a few days.")
    result = await decide(
        job=job,
        user=user,
        time_context=make_time_context(quiet_hours=True, part_of_day="night"),
        open_loops=[],
        recent_exchanges=[],
        settings=Settings(openrouter_api_key="test-key"),
        relationship={"sentiment_ema": -0.4, "last_meaningful_exchange_at": None},
        outbound_count_24h=1,
        daily_cap=6,
    )

    decision_input = captured["decision_input"]
    assert result.decision == "skip"
    assert result.reason == "late night and low value right now"
    assert result.when == "hold"
    assert captured["user_id"] == "u-decision"
    assert captured["metadata"] == {"job_kind": "autonomous_tick", "channel": "telegram"}
    assert decision_input["candidate"]["kind"] == "autonomous_tick"
    assert decision_input["time_context"]["quiet_hours"] is True
    assert decision_input["delivery_context"]["daily_cap"] == 6


async def test_should_send_soft_preserves_reflection_timing(monkeypatch) -> None:
    async def fake_reflect_on_wellbeing(*, settings, user_id, decision_input, metadata):
        return WellbeingDecision(
            reach_out=True,
            when="in_45m",
            message_seed="Check in after dinner.",
            rationale="later will land better than right now",
            model="test-model",
            decision_input=decision_input,
        )

    monkeypatch.setattr(
        "healthclaw.heartbeat.decision.reflect_on_wellbeing",
        fake_reflect_on_wellbeing,
    )

    async with SessionLocal() as session:
        user = make_user(
            "u-reflection-timing",
            last_active_at=datetime.now(UTC) - timedelta(hours=4),
            heartbeat_md="Evening check-ins are fine.",
        )
        job = HeartbeatJob(
            id="job-reflection-timing",
            user_id=user.id,
            kind="autonomous_tick",
            due_at=datetime.now(UTC),
            channel="telegram",
            payload={},
            idempotency_key="job-reflection-timing",
        )
        engagement = UserEngagementState(
            user_id=user.id,
            last_meaningful_exchange_at=datetime.now(UTC) - timedelta(hours=5),
            sentiment_ema=-0.2,
        )
        session.add_all([user, job, engagement])
        await session.flush()

        decision = await HeartbeatService(
            session,
            Settings(openrouter_api_key="test-key"),
        ).should_send_soft(job, user, datetime.now(UTC))

    assert decision.reach_out is False
    assert decision.when == "in_45m"
    assert decision.rationale == "later will land better than right now"
    assert decision.decision_input["relationship"]["bands"]["continuity_fresh"] is True


async def test_should_send_soft_applies_daily_cap_floor(monkeypatch) -> None:
    async def fake_reflect_on_wellbeing(*, settings, user_id, decision_input, metadata):
        return WellbeingDecision(
            reach_out=True,
            when="now",
            message_seed="A quick nudge for the walk.",
            rationale="the open loop is stale enough to check in",
            model="test-model",
            decision_input=decision_input,
        )

    monkeypatch.setattr(
        "healthclaw.heartbeat.decision.reflect_on_wellbeing",
        fake_reflect_on_wellbeing,
    )

    async with SessionLocal() as session:
        user = make_user("u-daily-cap", proactive_max_per_day=2)
        job = HeartbeatJob(
            id="job-daily-cap",
            user_id=user.id,
            kind="open_loop_followup",
            due_at=datetime.now(UTC),
            channel="telegram",
            payload={"title": "stretch"},
            idempotency_key="job-daily-cap",
        )
        session.add_all([user, job])
        await session.flush()
        session.add_all(
            [
                ProactiveEvent(
                    user_id=user.id,
                    decision="sent",
                    reason="prior reminder",
                    channel="telegram",
                    created_at=datetime.now(UTC) - timedelta(hours=2),
                ),
                HeartbeatEvent(
                    user_id=user.id,
                    job_id=job.id,
                    decision="sent",
                    reason="prior heartbeat",
                    channel="telegram",
                    created_at=datetime.now(UTC) - timedelta(hours=1),
                ),
            ]
        )
        await session.flush()

        decision = await HeartbeatService(
            session,
            Settings(openrouter_api_key="test-key"),
        ).should_send_soft(job, user, datetime.now(UTC))

    assert decision.reach_out is False
    assert decision.when == "hold"
    assert decision.rationale == "daily delivery cap reached"
    assert decision.decision_input["delivery_floor_applied"] is True
