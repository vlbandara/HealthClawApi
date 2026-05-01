"""Tests for WS5 extensions to time_context — circadian phase, day arc, anticipated events."""
from __future__ import annotations

from datetime import UTC, datetime

from healthclaw.agent.time_context import (
    build_time_context,
    circadian_phase_for,
    day_arc_for,
    part_of_day_for,
)
from healthclaw.db.models import User


def _user(**overrides) -> User:
    defaults = dict(
        id="u1",
        timezone="Asia/Singapore",
        quiet_start="22:00",
        quiet_end="07:00",
        chronotype="intermediate",
        home_lat=None,
        home_lon=None,
    )
    defaults.update(overrides)
    return User(**defaults)


# ── circadian_phase_for ───────────────────────────────────────────────────────


def test_intermediate_morning_is_peak() -> None:
    assert circadian_phase_for(10, "intermediate") == "peak_morning"


def test_intermediate_late_night_is_deep_sleep() -> None:
    assert circadian_phase_for(2, "intermediate") == "deep_sleep"


def test_early_bird_morning_is_peak() -> None:
    assert circadian_phase_for(8, "early") == "peak_morning"


def test_night_owl_noon_is_wake_window() -> None:
    assert circadian_phase_for(10, "late") == "wake_window"


def test_wind_down_phase() -> None:
    phase = circadian_phase_for(22, "intermediate")
    assert phase == "evening_wind_down"


# ── day_arc_for ──────────────────────────────────────────────────────────────


def test_day_arc_hours_since_wake() -> None:
    arc = day_arc_for(11, "intermediate")  # typical wake=7
    assert arc["hours_since_typical_wake"] == 4


def test_day_arc_typical_wake_hour() -> None:
    arc = day_arc_for(10, "early")
    assert arc["typical_wake_hour"] == 5


# ── build_time_context with new fields ───────────────────────────────────────


def test_build_time_context_populates_circadian_phase() -> None:
    user = _user()
    # 14:00 Singapore = 06:00 UTC
    now = datetime(2026, 5, 1, 6, 0, tzinfo=UTC)
    ctx = build_time_context(user, now=now)
    assert ctx.circadian_phase != "unknown"
    assert isinstance(ctx.day_arc_position, dict)
    assert "hours_since_typical_wake" in ctx.day_arc_position


def test_build_time_context_includes_calendar_events() -> None:
    from types import SimpleNamespace

    user = _user()
    now = datetime(2026, 5, 1, 6, 0, tzinfo=UTC)
    fake_event = {"title": "Lunch", "start_at": "2026-05-01T12:00:00+08:00"}
    ctx = build_time_context(user, now=now, calendar_events=[fake_event])
    assert len(ctx.anticipated_events) == 1
    assert ctx.anticipated_events[0]["title"] == "Lunch"


def test_build_time_context_rhythm_memory_injected() -> None:
    user = _user()
    now = datetime(2026, 5, 1, 6, 0, tzinfo=UTC)
    rhythm = {"typical_engage_hours": [8, 12, 21], "deviation_from_pattern_min": 30}
    ctx = build_time_context(user, now=now, rhythm_memory=rhythm)
    assert ctx.interaction_rhythm["typical_engage_hours"] == [8, 12, 21]


def test_to_dict_roundtrip_includes_new_fields() -> None:
    user = _user()
    now = datetime(2026, 5, 1, 6, 0, tzinfo=UTC)
    ctx = build_time_context(user, now=now)
    d = ctx.to_dict()
    assert "circadian_phase" in d
    assert "day_arc_position" in d
    assert "anticipated_events" in d
    assert "interaction_rhythm" in d
