"""Tests for inner/speech_gate.py — hard gate rules."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from healthclaw.agent.time_context import TimeContext
from healthclaw.agent.wellbeing import WellbeingDecision
from healthclaw.inner.speech_gate import SpeechGate


def _time_ctx(quiet_hours: bool = False) -> TimeContext:
    return TimeContext(
        local_datetime=datetime.now(UTC).isoformat(),
        local_date=datetime.now(UTC).date().isoformat(),
        weekday="Thursday",
        part_of_day="afternoon",
        quiet_hours=quiet_hours,
        interaction_gap_days=0,
        long_lapse=False,
        circadian_phase="peak_morning",
        day_arc_position={},
        anticipated_events=[],
        interaction_rhythm={},
    )


def _user(proactive_max_per_day: int = 3, cooldown_minutes: int = 180) -> MagicMock:
    user = MagicMock()
    user.id = "u1"
    user.notification_channel = "telegram"
    user.proactive_max_per_day = proactive_max_per_day
    user.proactive_cooldown_minutes = cooldown_minutes
    return user


def _thought(salience: float = 0.7) -> MagicMock:
    thought = MagicMock()
    thought.id = "t1"
    thought.user_id = "u1"
    thought.salience = salience
    thought.salience_breakdown = {"weather_heat_stress": 0.4, "calendar_imminent_event": 0.3}
    thought.signal_ids = ["s1"]
    thought.content_summary = "heat and event"
    thought.became_utterance = False
    thought.heartbeat_job_id = None
    return thought


def _reach_out_decision() -> WellbeingDecision:
    return WellbeingDecision(
        reach_out=True,
        when="now",
        message_seed="SG is hot, drink water.",
        rationale="heat + event",
        model="gemini",
        decision_input={},
    )


def _make_empty_session() -> AsyncMock:
    """Session that returns empty results for all execute calls."""
    session = AsyncMock()
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    empty_result.scalars.return_value = iter([])
    session.execute = AsyncMock(return_value=empty_result)
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.mark.asyncio
async def test_gate_blocks_on_quiet_hours_low_salience():
    session = _make_empty_session()
    gate = SpeechGate(session)
    thought = _thought(salience=0.65)
    outcome = await gate.evaluate(thought, _user(), _time_ctx(quiet_hours=True), _reach_out_decision())
    assert outcome.emit is False
    assert "quiet_hours" in outcome.rationale


@pytest.mark.asyncio
async def test_gate_allows_high_salience_during_quiet_hours():
    session = _make_empty_session()
    job_mock = MagicMock()
    job_mock.id = "job1"

    with patch.object(SpeechGate, "_create_heartbeat_job", new=AsyncMock(return_value=job_mock)):
        gate = SpeechGate(session)
        thought = _thought(salience=0.9)  # above 0.85 override
        outcome = await gate.evaluate(thought, _user(), _time_ctx(quiet_hours=True), _reach_out_decision())

    assert outcome.emit is True


@pytest.mark.asyncio
async def test_gate_blocks_when_daily_cap_reached():
    # _outbound_count_24h calls execute twice (ProactiveEvent + HeartbeatEvent)
    # Each returns 3 events, totalling 6 > daily_cap=2
    call_count = 0

    async def execute_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value = iter([MagicMock() for _ in range(3)])
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)

    gate = SpeechGate(session)
    thought = _thought(salience=0.8)
    outcome = await gate.evaluate(
        thought,
        _user(proactive_max_per_day=2),
        _time_ctx(),
        _reach_out_decision(),
    )
    assert outcome.emit is False
    assert "daily_cap" in outcome.rationale


@pytest.mark.asyncio
async def test_gate_allows_when_no_blocks():
    session = _make_empty_session()
    job_mock = MagicMock()
    job_mock.id = "job1"

    with patch.object(SpeechGate, "_create_heartbeat_job", new=AsyncMock(return_value=job_mock)):
        gate = SpeechGate(session)
        thought = _thought(salience=0.7)
        outcome = await gate.evaluate(thought, _user(), _time_ctx(), _reach_out_decision())

    assert outcome.emit is True
    assert outcome.message_seed == "SG is hot, drink water."
