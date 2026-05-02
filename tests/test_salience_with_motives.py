"""Tests: motive-weighted salience scoring."""
from __future__ import annotations

from unittest.mock import MagicMock

from healthclaw.inner.salience import compute_salience


def _sig(kind: str, value: dict) -> MagicMock:
    s = MagicMock()
    s.kind = kind
    s.value = value
    return s


def _motive(name: str, weight: float) -> MagicMock:
    m = MagicMock()
    m.name = name
    m.weight = weight
    return m


def test_heat_stress_amplified_by_hydration_motive() -> None:
    signals = [_sig("weather", {"temp_c": 35, "humidity_pct": 80, "uv_index": 5, "wmo_code": 1})]
    motives = [_motive("hydration", 0.8)]
    result_with = compute_salience(signals, {}, motives=motives)
    result_without = compute_salience(signals, {}, motives=[])
    # With motive, heat_stress contribution is amplified
    assert result_with.score > result_without.score
    assert "weather_heat_stress" in result_with.breakdown


def test_no_signals_zero_salience() -> None:
    result = compute_salience([], {})
    assert result.score == 0.0


def test_hydration_need_signal_scored() -> None:
    signals = [_sig("hydration_need", {"severity": 0.7})]
    motives = [_motive("hydration", 0.6)]
    result = compute_salience(signals, {}, motives=motives)
    assert result.score > 0
    assert "hydration_need" in result.breakdown


def test_dampening_cooldown() -> None:
    signals = [_sig("weather", {"temp_c": 35, "humidity_pct": 80, "uv_index": 5, "wmo_code": 1})]
    result = compute_salience(signals, {}, outbound_in_cooldown=True)
    assert result.dampened
    assert result.dampening_reason == "cooldown"


def test_dampening_already_deliberated() -> None:
    signals = [_sig("weather", {"temp_c": 35, "humidity_pct": 80, "uv_index": 5, "wmo_code": 1})]
    result = compute_salience(signals, {}, already_deliberated_today=True)
    assert result.dampened
    assert result.score == 0.0
