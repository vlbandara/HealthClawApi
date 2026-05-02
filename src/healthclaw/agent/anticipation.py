"""Anticipation layer — populates TimeContext's forward-looking fields.

Fills:
  - day_arc_position: where the user is in their day (0..1 normalised by circadian window)
  - anticipated_events: calendar signals in the next 12h
  - interaction_rhythm: learned from self_model:engagement_rhythm memory

These are then visible to the inner synthesizer and salience scorer,
enabling nudges like "you have a 2pm meeting — here's a pre-meeting water reminder".
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.db.models import Memory, Signal, User

logger = logging.getLogger(__name__)


async def populate_anticipation(
    user: User,
    time_ctx_dict: dict[str, Any],
    session: AsyncSession,
) -> dict[str, Any]:
    """Return an enriched copy of *time_ctx_dict* with anticipation fields filled."""
    enriched = dict(time_ctx_dict)

    # 1. Day-arc position (0..1 where 0=wake, 1=sleep)
    enriched["day_arc_position"] = _compute_day_arc(time_ctx_dict, user)

    # 2. Anticipated calendar events (next 12h from signals bus)
    enriched["anticipated_events"] = await _upcoming_events(user.id, session)

    # 3. Interaction rhythm from self-model memory
    enriched["interaction_rhythm"] = await _load_interaction_rhythm(user.id, session)

    return enriched


def _compute_day_arc(time_ctx: dict[str, Any], user: User) -> dict[str, Any]:
    """Compute normalised position in the user's waking day based on circadian phase."""
    part = time_ctx.get("part_of_day", "")
    phase = time_ctx.get("circadian_phase", "")

    arc_map = {
        "pre_wake": 0.0,
        "waking": 0.1,
        "peak": 0.4,
        "dip": 0.6,
        "wind_down": 0.8,
        "deep_sleep": 0.95,
    }
    position = arc_map.get(phase, 0.5)

    # Refine by local hour if available
    local_dt_str = time_ctx.get("local_datetime", "")
    if local_dt_str:
        try:
            local_dt = datetime.fromisoformat(local_dt_str)
            hour = local_dt.hour
            quiet_start_h = int((user.quiet_start or "22:00").split(":")[0])
            quiet_end_h = int((user.quiet_end or "07:00").split(":")[0])
            waking_hours = (quiet_start_h - quiet_end_h) % 24 or 15
            hours_awake = (hour - quiet_end_h) % 24
            position = min(1.0, hours_awake / waking_hours)
        except Exception:
            pass

    return {"position": round(position, 2), "phase": phase, "part_of_day": part}


async def _upcoming_events(user_id: str, session: AsyncSession) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    window_end = now + timedelta(hours=12)
    result = await session.execute(
        select(Signal).where(
            Signal.user_id == user_id,
            Signal.kind == "calendar_event",
            Signal.observed_at >= now - timedelta(hours=1),
        ).order_by(Signal.observed_at.asc()).limit(10)
    )
    events = []
    for sig in result.scalars():
        val = sig.value if isinstance(sig.value, dict) else {}
        start_raw = val.get("start_at", "")
        try:
            start_dt = datetime.fromisoformat(start_raw)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=UTC)
            if now <= start_dt <= window_end:
                mins_until = int((start_dt - now).total_seconds() / 60)
                events.append({
                    "title": val.get("title", ""),
                    "start_at": start_dt.isoformat(),
                    "mins_until": mins_until,
                })
        except (ValueError, TypeError):
            pass
    return events


async def _load_interaction_rhythm(user_id: str, session: AsyncSession) -> dict[str, Any]:
    """Load engagement_rhythm / user_pattern:engagement_rhythm from Memory."""
    result = await session.execute(
        select(Memory).where(
            Memory.user_id == user_id,
            Memory.kind.in_(["user_pattern", "rhythm"]),
            Memory.key.in_(["engagement_pattern", "engagement_rhythm"]),
            Memory.is_active.is_(True),
        ).limit(2)
    )
    rhythm: dict[str, Any] = {}
    for mem in result.scalars():
        if isinstance(mem.value, dict):
            rhythm.update(mem.value)
    return rhythm
