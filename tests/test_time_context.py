from __future__ import annotations

from datetime import UTC, datetime

from healthclaw.agent.time_context import build_time_context, is_quiet_hour, parse_hhmm
from healthclaw.db.models import User


def test_quiet_hours_wrap_midnight() -> None:
    assert is_quiet_hour(parse_hhmm("23:00"), "22:00", "07:00")
    assert is_quiet_hour(parse_hhmm("06:30"), "22:00", "07:00")
    assert not is_quiet_hour(parse_hhmm("12:00"), "22:00", "07:00")


def test_time_context_long_lapse() -> None:
    user = User(id="u1", timezone="Asia/Colombo", quiet_start="22:00", quiet_end="07:00")
    now = datetime(2026, 4, 21, 4, 0, tzinfo=UTC)
    last = datetime(2026, 4, 1, 4, 0, tzinfo=UTC)
    context = build_time_context(user, now=now, last_interaction_at=last)
    assert context.long_lapse is True
    assert context.interaction_gap_days == 20
