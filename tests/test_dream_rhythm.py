"""Tests for Dream._learn_engagement_rhythm — statistical rhythm extraction."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from healthclaw.memory.dream import DreamService


def _make_user(timezone: str = "Asia/Singapore") -> MagicMock:
    user = MagicMock()
    user.id = "u1"
    user.timezone = timezone
    return user


def _make_message(hour_utc: int, role: str = "user") -> MagicMock:
    msg = MagicMock()
    # Singapore is UTC+8, so hour_utc=6 → local hour=14
    msg.created_at = datetime(2026, 4, 15, hour_utc, 30, tzinfo=UTC)
    msg.role = role
    return msg


def _mock_session_with_messages(msgs: list) -> MagicMock:
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value = iter(msgs)
    session.execute = AsyncMock(return_value=result_mock)
    return session


@pytest.mark.asyncio
async def test_learn_rhythm_writes_memory_for_sufficient_messages():
    msgs = [_make_message(6) for _ in range(10)] + [_make_message(0) for _ in range(5)]
    session = _mock_session_with_messages(msgs)

    upsert_calls = []

    async def mock_upsert(user_id, mutation, source_ids):
        upsert_calls.append(mutation)

    settings = MagicMock()
    memory_service = MagicMock()
    memory_service.upsert_memory = AsyncMock(side_effect=mock_upsert)
    service = DreamService(session, settings, memory_service)

    await service._learn_engagement_rhythm(_make_user())

    assert len(upsert_calls) == 1
    mutation = upsert_calls[0]
    assert mutation.kind == "rhythm"
    assert mutation.key == "engagement_pattern"
    assert isinstance(mutation.value.get("typical_engage_hours"), list)
    # SG local: hour 6 UTC = hour 14 local; hour 0 UTC = hour 8 local
    engage_hours = mutation.value["typical_engage_hours"]
    assert 14 in engage_hours or 8 in engage_hours


@pytest.mark.asyncio
async def test_learn_rhythm_skips_for_too_few_messages():
    msgs = [_make_message(6) for _ in range(5)]
    session = _mock_session_with_messages(msgs)

    settings = MagicMock()
    memory_service = MagicMock()
    memory_service.upsert_memory = AsyncMock()
    service = DreamService(session, settings, memory_service)

    await service._learn_engagement_rhythm(_make_user())
    memory_service.upsert_memory.assert_not_called()
