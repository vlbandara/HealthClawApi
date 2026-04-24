from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from healthclaw.db.models import User


@dataclass(frozen=True)
class TimeContext:
    local_datetime: str
    local_date: str
    weekday: str
    part_of_day: str
    quiet_hours: bool
    interaction_gap_days: int | None
    long_lapse: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(hour=int(hour), minute=int(minute))


def is_quiet_hour(local_time: time, quiet_start: str, quiet_end: str) -> bool:
    start = parse_hhmm(quiet_start)
    end = parse_hhmm(quiet_end)
    if start <= end:
        return start <= local_time < end
    return local_time >= start or local_time < end


def part_of_day_for(local_time: time) -> str:
    if local_time.hour < 5:
        return "late_night"
    if local_time.hour < 12:
        return "morning"
    if local_time.hour < 17:
        return "afternoon"
    if local_time.hour < 21:
        return "evening"
    return "night"


def build_time_context(
    user: User | dict[str, Any],
    now: datetime | None = None,
    last_interaction_at: datetime | None = None,
) -> TimeContext:
    timezone = user["timezone"] if isinstance(user, dict) else user.timezone
    quiet_start = user["quiet_start"] if isinstance(user, dict) else user.quiet_start
    quiet_end = user["quiet_end"] if isinstance(user, dict) else user.quiet_end
    tz = ZoneInfo(timezone)
    base = now or datetime.now(UTC)
    local = base.astimezone(tz)
    gap_days = None
    if last_interaction_at is not None:
        if last_interaction_at.tzinfo is None:
            last_interaction_at = last_interaction_at.replace(tzinfo=UTC)
        gap_days = max(0, (base - last_interaction_at).days)
    return TimeContext(
        local_datetime=local.isoformat(),
        local_date=local.date().isoformat(),
        weekday=local.strftime("%A"),
        part_of_day=part_of_day_for(local.time()),
        quiet_hours=is_quiet_hour(local.time(), quiet_start, quiet_end),
        interaction_gap_days=gap_days,
        long_lapse=bool(gap_days is not None and gap_days >= 7),
    )
