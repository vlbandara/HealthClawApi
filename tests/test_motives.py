"""Tests for the motive layer (Workstream A)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from healthclaw.inner.motives import (
    DEFAULT_MOTIVES,
    MOTIVE_SIGNAL_MAP,
    MotiveService,
    motive_weight_for_signal,
)


def _make_motive(name: str, weight: float = 0.5) -> MagicMock:
    m = MagicMock()
    m.name = name
    m.weight = weight
    m.rationale = ""
    m.source = "seeded"
    m.is_active = True
    return m


def test_default_motives_present() -> None:
    names = {name for name, _ in DEFAULT_MOTIVES}
    assert "hydration" in names
    assert "sleep_protection" in names
    assert "mood_stability" in names
    assert len(DEFAULT_MOTIVES) == 6


def test_motive_signal_map_hydration() -> None:
    assert "weather_heat_stress" in MOTIVE_SIGNAL_MAP["hydration"]
    assert "hydration_need" in MOTIVE_SIGNAL_MAP["hydration"]


def test_motive_weight_for_signal_amplifies() -> None:
    motives = [_make_motive("hydration", weight=0.8)]
    amp = motive_weight_for_signal(motives, "weather_heat_stress")
    # Expected: 1.0 + 0.8 = 1.8
    assert amp == pytest.approx(1.8)


def test_motive_weight_for_signal_no_match_returns_one() -> None:
    motives = [_make_motive("movement", weight=0.9)]
    amp = motive_weight_for_signal(motives, "weather_heat_stress")
    assert amp == pytest.approx(1.0)


def test_motive_weight_for_signal_empty_motives() -> None:
    amp = motive_weight_for_signal([], "weather_heat_stress")
    assert amp == pytest.approx(1.0)


def test_motive_service_to_dict() -> None:
    m = _make_motive("hydration", 0.5)
    m.id = "abc"
    result = MotiveService.to_dict(m)
    assert result["name"] == "hydration"
    assert result["weight"] == 0.5
