"""Tests for anticipation layer (Workstream C)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from healthclaw.agent.anticipation import _compute_day_arc, _upcoming_events


def _user(quiet_start="22:00", quiet_end="07:00"):
    u = MagicMock()
    u.quiet_start = quiet_start
    u.quiet_end = quiet_end
    u.timezone = "Asia/Singapore"
    return u


def test_day_arc_peak_phase() -> None:
    time_ctx = {
        "circadian_phase": "peak",
        "part_of_day": "afternoon",
        "local_datetime": "2026-05-02T14:00:00+08:00",
    }
    result = _compute_day_arc(time_ctx, _user())
    assert 0.0 < result["position"] < 1.0
    assert result["phase"] == "peak"


def test_day_arc_deep_sleep() -> None:
    time_ctx = {
        "circadian_phase": "deep_sleep",
        "part_of_day": "night",
        "local_datetime": "2026-05-02T02:00:00+08:00",
    }
    result = _compute_day_arc(time_ctx, _user())
    assert result["position"] >= 0.8  # should be late in the sleep arc


@pytest.mark.asyncio
async def test_upcoming_events_empty_session() -> None:
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=result_mock)
    events = await _upcoming_events("user1", session)
    assert events == []


@pytest.mark.asyncio
async def test_upcoming_events_filters_window() -> None:
    """Signals outside the 12h window should not be included."""
    now = datetime.now(UTC)
    far_future = now + timedelta(hours=24)

    session = AsyncMock()
    sig = MagicMock()
    sig.kind = "calendar_event"
    sig.value = {"title": "Far Future Meeting", "start_at": far_future.isoformat()}
    sig.observed_at = now

    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=[sig])
    session.execute = AsyncMock(return_value=result_mock)

    events = await _upcoming_events("user1", session)
    # Should be empty because far_future > now + 12h
    assert events == []
