from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SalienceResult:
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)
    dampened: bool = False
    dampening_reason: str = ""

    @property
    def above_threshold(self) -> bool:
        from healthclaw.core.config import get_settings

        return self.score >= get_settings().inner_salience_threshold


def compute_salience(
    signals: list[Any],
    time_context: dict[str, Any],
    *,
    outbound_in_cooldown: bool = False,
    quiet_hours: bool | None = None,
    already_deliberated_today: bool = False,
) -> SalienceResult:
    # Allow caller to override quiet_hours; fall back to time_context dict value
    if quiet_hours is None:
        quiet_hours = bool(time_context.get("quiet_hours", False))
    """Pure-Python salience scoring — no LLM, no I/O.

    Inputs are Signal ORM rows (duck-typed: must have .kind and .value dict).
    Returns a SalienceResult with an additive score clamped to [0, 1] and a breakdown dict
    suitable for persisting in the thoughts.salience_breakdown column.
    """
    raw: dict[str, float] = {}
    score = 0.0

    for sig in signals:
        kind = str(sig.kind)
        val: dict[str, Any] = sig.value if isinstance(sig.value, dict) else {}

        if kind == "weather":
            temp_c = float(val.get("temp_c", 0))
            humidity = int(val.get("humidity_pct", 0))
            uv = float(val.get("uv_index", 0))
            wmo = int(val.get("wmo_code", 0))
            if temp_c > 31 and humidity > 70:
                _add(raw, "weather_heat_stress", 0.4)
            if uv > 8:
                _add(raw, "weather_high_uv", 0.2)
            if wmo >= 80:
                _add(raw, "weather_severe", 0.3)

        elif kind == "calendar_event":
            from datetime import UTC, datetime

            start_raw = val.get("start_at", "")
            try:
                start_dt = datetime.fromisoformat(start_raw)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=UTC)
                now = datetime.now(UTC)
                mins_until = max(0, int((start_dt - now).total_seconds() / 60))
                if 0 < mins_until <= 90:
                    _add(raw, "calendar_imminent_event", 0.3)
                elif mins_until == 0:
                    _add(raw, "calendar_event_now", 0.1)
            except (ValueError, TypeError):
                pass

        elif kind == "wearable_recovery":
            recovery_score = val.get("recovery_score")
            if recovery_score is not None and float(recovery_score) < 33:
                _add(raw, "wearable_low_recovery", 0.5)

        elif kind == "wearable_sleep":
            sleep_hours = val.get("sleep_hours")
            if sleep_hours is not None and float(sleep_hours) < 5:
                _add(raw, "wearable_poor_sleep", 0.4)

    # Time-context bonuses
    if time_context.get("long_lapse"):
        _add(raw, "long_lapse", 0.2)
    circadian = time_context.get("circadian_phase", "")
    if circadian in {"deep_sleep", "pre_wake"} and not quiet_hours:
        _add(raw, "out_of_circadian_window", 0.1)

    base_score = min(1.0, sum(raw.values()))
    raw["_base"] = base_score

    # Dampening
    dampened = False
    dampening_reason = ""
    multiplier = 1.0

    if already_deliberated_today:
        multiplier = 0.0
        dampened = True
        dampening_reason = "already_deliberated_today"
    elif quiet_hours:
        multiplier = min(multiplier, 0.2)
        dampened = True
        dampening_reason = "quiet_hours"
    elif outbound_in_cooldown:
        multiplier = min(multiplier, 0.4)
        dampened = True
        dampening_reason = "cooldown"

    final = round(base_score * multiplier, 4)
    raw["_multiplier"] = multiplier
    raw["_final"] = final

    return SalienceResult(
        score=final,
        breakdown=raw,
        dampened=dampened,
        dampening_reason=dampening_reason,
    )


def _add(breakdown: dict[str, float], key: str, value: float) -> None:
    breakdown[key] = breakdown.get(key, 0.0) + value
