"""Tests for sensing/bus.py — signal publication and deduplication."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from healthclaw.sensing.bus import Signal, SignalBus


def _make_session(existing_signal=None) -> AsyncMock:
    """Build a mock session. result of execute() is a sync MagicMock with scalar_one_or_none."""
    session = AsyncMock()
    # session.execute is async; its return value must support .scalar_one_or_none() synchronously
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing_signal
    session.execute = AsyncMock(return_value=result_mock)
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


def test_signal_auto_generates_dedup_key():
    sig = Signal(kind="weather", value={"temp_c": 33}, source="open_meteo")
    assert sig.dedup_key.startswith("weather:open_meteo:")


def test_signal_explicit_dedup_key_preserved():
    sig = Signal(kind="weather", value={}, source="x", dedup_key="custom:key")
    assert sig.dedup_key == "custom:key"


@pytest.mark.asyncio
async def test_publish_new_signal_returns_is_new_true():
    session = _make_session(existing_signal=None)

    bus = SignalBus(session)
    signal = Signal(kind="weather", value={"temp_c": 33}, source="open_meteo", dedup_key="k1")
    signal_id, is_new = await bus.publish("user1", signal)

    assert is_new is True
    assert isinstance(signal_id, str) and len(signal_id) == 32  # new_id() returns 32-char hex
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_publish_duplicate_dedup_key_returns_is_new_false():
    existing = MagicMock()
    existing.id = "existing_id"
    session = _make_session(existing_signal=existing)

    bus = SignalBus(session)
    signal = Signal(kind="weather", value={}, source="test", dedup_key="dup:key")
    signal_id, is_new = await bus.publish("user1", signal)

    assert is_new is False
    assert signal_id == "existing_id"
    session.add.assert_not_called()
