"""Tests for inner/salience.py — pure deterministic rules, no LLM, no DB."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from healthclaw.inner.salience import compute_salience


def _sig(kind: str, value: dict[str, Any]) -> Any:
    return SimpleNamespace(kind=kind, value=value)


def _time_ctx(**overrides: Any) -> dict:
    base = {
        "circadian_phase": "peak_morning",
        "quiet_hours": False,
        "long_lapse": False,
    }
    base.update(overrides)
    return base


# ── Weather rules ────────────────────────────────────────────────────────────


def test_heat_stress_scores_0_4() -> None:
    sig = _sig("weather", {"temp_c": 33, "humidity_pct": 85, "uv_index": 5, "wmo_code": 0})
    result = compute_salience([sig], _time_ctx())
    assert result.breakdown.get("weather_heat_stress") == 0.4
    assert result.score >= 0.4


def test_high_uv_adds_0_2() -> None:
    sig = _sig("weather", {"temp_c": 20, "humidity_pct": 50, "uv_index": 10, "wmo_code": 0})
    result = compute_salience([sig], _time_ctx())
    assert result.breakdown.get("weather_high_uv") == 0.2


def test_severe_weather_adds_0_3() -> None:
    sig = _sig("weather", {"temp_c": 20, "humidity_pct": 50, "uv_index": 3, "wmo_code": 95})
    result = compute_salience([sig], _time_ctx())
    assert result.breakdown.get("weather_severe") == 0.3


def test_mild_weather_scores_zero() -> None:
    sig = _sig("weather", {"temp_c": 22, "humidity_pct": 60, "uv_index": 4, "wmo_code": 1})
    result = compute_salience([sig], _time_ctx())
    assert result.score == 0.0


# ── Calendar rules ───────────────────────────────────────────────────────────


def test_imminent_event_within_90_min() -> None:
    start = (datetime.now(UTC) + timedelta(minutes=45)).isoformat()
    sig = _sig("calendar_event", {"start_at": start, "title": "Lunch", "is_outdoor_hint": False})
    result = compute_salience([sig], _time_ctx())
    assert result.breakdown.get("calendar_imminent_event") == 0.3


def test_event_more_than_90_min_away_does_not_score() -> None:
    start = (datetime.now(UTC) + timedelta(hours=4)).isoformat()
    sig = _sig("calendar_event", {"start_at": start, "title": "Meeting"})
    result = compute_salience([sig], _time_ctx())
    assert "calendar_imminent_event" not in result.breakdown


# ── Wearable rules ───────────────────────────────────────────────────────────


def test_low_recovery_scores_0_5() -> None:
    sig = _sig("wearable_recovery", {"recovery_score": 20, "available": True})
    result = compute_salience([sig], _time_ctx())
    assert result.breakdown.get("wearable_low_recovery") == 0.5
    assert result.score == 0.5


def test_poor_sleep_scores_0_4() -> None:
    sig = _sig("wearable_sleep", {"sleep_hours": 3.5, "available": True})
    result = compute_salience([sig], _time_ctx())
    assert result.breakdown.get("wearable_poor_sleep") == 0.4


# ── Dampening ────────────────────────────────────────────────────────────────


def test_quiet_hours_dampens_to_0_2() -> None:
    import pytest

    sig = _sig("weather", {"temp_c": 33, "humidity_pct": 85, "uv_index": 5, "wmo_code": 0})
    result = compute_salience([sig], _time_ctx(quiet_hours=True))
    assert result.dampened is True
    assert result.score == pytest.approx(0.4 * 0.2, rel=1e-3)


def test_cooldown_dampens_to_0_4() -> None:
    import pytest

    sig = _sig("weather", {"temp_c": 33, "humidity_pct": 85, "uv_index": 5, "wmo_code": 0})
    result = compute_salience([sig], _time_ctx(), outbound_in_cooldown=True)
    assert result.dampened is True
    assert result.score == pytest.approx(0.4 * 0.4, rel=1e-3)


def test_already_deliberated_today_scores_zero() -> None:
    sig = _sig("weather", {"temp_c": 33, "humidity_pct": 85, "uv_index": 5, "wmo_code": 0})
    result = compute_salience([sig], _time_ctx(), already_deliberated_today=True)
    assert result.score == 0.0
    assert result.dampened is True


# ── Score clamped at 1.0 ─────────────────────────────────────────────────────


def test_combined_signals_clamped_to_1() -> None:
    signals = [
        _sig("weather", {"temp_c": 33, "humidity_pct": 85, "uv_index": 9, "wmo_code": 95}),
        _sig("wearable_recovery", {"recovery_score": 10}),
        _sig("wearable_sleep", {"sleep_hours": 3}),
    ]
    result = compute_salience(signals, _time_ctx(long_lapse=True))
    assert result.score <= 1.0
