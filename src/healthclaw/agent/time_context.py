from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    # WS5: richer self-awareness fields
    circadian_phase: str = "unknown"
    day_arc_position: dict = field(default_factory=dict)
    anticipated_events: list = field(default_factory=list)
    interaction_rhythm: dict = field(default_factory=dict)
    # WS7: Time Truth — authoritative human-readable strings for LLM injection
    human_phrasing: dict = field(default_factory=dict)
    timezone_confidence: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def time_truth_block(self) -> str:
        """Return the authoritative 'NOW' block to inject at the top of every system prompt.

        The block is designed to prevent LLM time hallucination — the LLM is told
        explicitly what day/time it is and forbidden from claiming anything different.
        """
        hp = self.human_phrasing
        if not hp:
            return ""
        now_str = hp.get("now_user_local", "unknown")
        confidence = self.timezone_confidence
        if confidence < 0.6:
            return (
                "# Time Truth (authoritative)\n\n"
                f"NOW (server time, user timezone not yet confirmed): {now_str}\n"
                "- timezone_confidence is LOW. Do NOT volunteer the time or day to the user.\n"
                "- If asked what time/day it is, say you're not certain of their timezone yet "
                "and ask them once.\n"
                "- Never invent a day, date, or hour."
            )
        return (
            "# Time Truth (authoritative)\n\n"
            f"NOW (user's local time): {now_str}\n"
            f"- Weekday: {hp.get('weekday_user', 'unknown')}\n"
            f"- Date: {hp.get('date_user', 'unknown')}\n"
            f"- Time: {hp.get('time_user', 'unknown')}\n"
            f"- Timezone: {hp.get('tz_name', 'unknown')} ({hp.get('tz_offset', '?')})\n"
            "- You may NEVER state a different day, date, or hour than shown above.\n"
            "- If the user contradicts you about the time, trust THEIR statement, "
            "apologise once briefly, and move on — do NOT re-state the time.\n"
            "- Use the weekday and time naturally when relevant; do not over-reference it."
        )


def _build_human_phrasing(local: datetime, tz_name: str) -> dict[str, object]:
    """Build unambiguous, LLM-friendly human phrasing for the current local datetime."""
    offset = local.strftime("%z")  # e.g. "+0530"
    if len(offset) == 5:  # "+0530" → "+05:30"
        offset = offset[:3] + ":" + offset[3:]
    return {
        "now_user_local": local.strftime(f"%A, %-d %B %Y, %-I:%M %p ({tz_name}, UTC{offset})"),
        "now_short": local.strftime("%a %-I:%M %p"),
        "weekday_user": local.strftime("%A"),
        "date_user": local.strftime("%-d %B %Y"),
        "time_user": local.strftime("%-I:%M %p"),
        "tz_name": tz_name,
        "tz_offset": f"UTC{offset}",
        "is_weekend": local.weekday() >= 5,
    }


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


# Typical wake/sleep windows per chronotype (local hours)
_CHRONOTYPE_WINDOWS: dict[str, dict[str, int]] = {
    "early": {
        "wake": 5, "peak_start": 7, "peak_end": 11, "dip": 13, "wind_down": 19, "sleep": 21,
    },
    "intermediate": {
        "wake": 7, "peak_start": 9, "peak_end": 13, "dip": 14, "wind_down": 21, "sleep": 23,
    },
    "late": {
        "wake": 9, "peak_start": 11, "peak_end": 15, "dip": 15, "wind_down": 23, "sleep": 1,
    },
}


def circadian_phase_for(local_hour: int, chronotype: str) -> str:
    w = _CHRONOTYPE_WINDOWS.get(chronotype, _CHRONOTYPE_WINDOWS["intermediate"])
    wake = w["wake"]
    peak_start = w["peak_start"]
    peak_end = w["peak_end"]
    dip = w["dip"]
    wind_down = w["wind_down"]
    sleep = w["sleep"]

    # Handle late chronotype where sleep wraps past midnight
    if sleep < wake:
        if local_hour >= sleep and local_hour < wake:
            return "deep_sleep"
    else:
        if local_hour >= sleep or local_hour < wake:
            return "deep_sleep"

    if local_hour < wake:
        return "pre_wake"
    if local_hour < peak_start:
        return "wake_window"
    if local_hour < peak_end:
        return "peak_morning"
    if local_hour == dip:
        return "post_lunch_dip"
    if local_hour < wind_down:
        return "afternoon"
    return "evening_wind_down"


def day_arc_for(local_hour: int, chronotype: str) -> dict[str, Any]:
    w = _CHRONOTYPE_WINDOWS.get(chronotype, _CHRONOTYPE_WINDOWS["intermediate"])
    typical_wake = w["wake"]
    typical_sleep = w["sleep"]
    hours_since_wake = max(0, local_hour - typical_wake)
    if typical_sleep > typical_wake:
        hours_until_sleep = max(0, typical_sleep - local_hour)
    else:
        if local_hour >= typical_wake:
            hours_until_sleep = 24 - local_hour + typical_sleep
        else:
            hours_until_sleep = max(0, typical_sleep - local_hour)
    return {
        "hours_since_typical_wake": hours_since_wake,
        "hours_until_typical_sleep": hours_until_sleep,
        "typical_wake_hour": typical_wake,
        "typical_sleep_hour": typical_sleep,
    }


def build_time_context(
    user: User | dict[str, Any],
    now: datetime | None = None,
    last_interaction_at: datetime | None = None,
    *,
    calendar_events: list[Any] | None = None,
    rhythm_memory: dict[str, Any] | None = None,
    timezone_confidence: float | None = None,
) -> TimeContext:
    timezone = user["timezone"] if isinstance(user, dict) else user.timezone
    quiet_start = user["quiet_start"] if isinstance(user, dict) else user.quiet_start
    quiet_end = user["quiet_end"] if isinstance(user, dict) else user.quiet_end
    chronotype = (
        user.get("chronotype", "intermediate")
        if isinstance(user, dict)
        else getattr(user, "chronotype", "intermediate")
    ) or "intermediate"
    # Prefer explicitly passed confidence; fall back to user attribute if it exists
    if timezone_confidence is None:
        timezone_confidence = float(
            getattr(user, "timezone_confidence", None)
            or user.get("timezone_confidence", 0.0)  # type: ignore[union-attr]
            if isinstance(user, dict)
            else getattr(user, "timezone_confidence", 0.0) or 0.0
        )

    tz = ZoneInfo(timezone)
    base = now or datetime.now(UTC)
    local = base.astimezone(tz)
    gap_days = None
    if last_interaction_at is not None:
        if last_interaction_at.tzinfo is None:
            last_interaction_at = last_interaction_at.replace(tzinfo=UTC)
        gap_days = max(0, (base - last_interaction_at).days)

    circadian = circadian_phase_for(local.hour, chronotype)
    day_arc = day_arc_for(local.hour, chronotype)

    # Calendar: next 12h events summarized
    anticipated: list[dict[str, Any]] = []
    if calendar_events:
        for evt in calendar_events[:5]:
            if hasattr(evt, "to_dict"):
                anticipated.append(evt.to_dict())
            elif isinstance(evt, dict):
                anticipated.append(evt)

    # Interaction rhythm from Dream-learned memory
    rhythm: dict[str, Any] = {}
    if rhythm_memory and isinstance(rhythm_memory, dict):
        rhythm = rhythm_memory

    human_phrasing = _build_human_phrasing(local, timezone)

    return TimeContext(
        local_datetime=local.isoformat(),
        local_date=local.date().isoformat(),
        weekday=local.strftime("%A"),
        part_of_day=part_of_day_for(local.time()),
        quiet_hours=is_quiet_hour(local.time(), quiet_start, quiet_end),
        interaction_gap_days=gap_days,
        long_lapse=bool(gap_days is not None and gap_days >= 7),
        circadian_phase=circadian,
        day_arc_position=day_arc,
        anticipated_events=anticipated,
        interaction_rhythm=rhythm,
        human_phrasing=human_phrasing,
        timezone_confidence=float(timezone_confidence or 0.0),
    )
