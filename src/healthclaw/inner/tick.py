from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.core.tracing import new_trace_id
from healthclaw.db.models import HeartbeatEvent, ProactiveEvent, Thought, User
from healthclaw.inner.salience import compute_salience
from healthclaw.sensing.bus import SignalBus

logger = logging.getLogger(__name__)


async def run_inner_tick(user_id: str, session: AsyncSession) -> dict:
    """
    One inner-tick cycle for a user.

    1. Pull fresh signals (last 30 min).
    2. Compute salience deterministically — no LLM.
    3. Write a Thought row (mode="passive").
    4. If salience >= threshold, enqueue deliberation.

    Returns a summary dict for the cron log.
    """
    now = datetime.now(UTC)

    user = await session.get(User, user_id)
    if user is None:
        return {"status": "skipped", "reason": "user_not_found"}

    bus = SignalBus(session)
    signals = await bus.recent_signals(user_id, window_minutes=30)
    if not signals:
        return {"status": "skipped", "reason": "no_recent_signals"}

    from healthclaw.agent.time_context import build_time_context

    time_ctx = build_time_context(user, now=now)
    time_ctx_dict = time_ctx.to_dict()

    outbound_in_cooldown = await _outbound_in_cooldown(user, now, session)
    already_deliberated_today = await _deliberated_today(user_id, signals, session)

    salience = compute_salience(
        signals,
        time_ctx_dict,
        outbound_in_cooldown=outbound_in_cooldown,
        quiet_hours=time_ctx.quiet_hours,
        already_deliberated_today=already_deliberated_today,
    )

    signal_summary = _summarize_signals(signals, time_ctx_dict)

    thought = Thought(
        user_id=user_id,
        mode="passive",
        content_summary=signal_summary,
        salience=salience.score,
        salience_breakdown=salience.breakdown,
        signal_ids=[str(s.id) for s in signals],
        time_context=time_ctx_dict,
        became_utterance=False,
        trace_id=new_trace_id(),
    )
    session.add(thought)
    await session.flush()

    result: dict = {
        "status": "ticked",
        "thought_id": thought.id,
        "salience": salience.score,
        "signal_count": len(signals),
        "dampened": salience.dampened,
    }

    if salience.above_threshold:
        from healthclaw.inner.deliberation import run_inner_deliberation

        try:
            deliberation_result = await run_inner_deliberation(thought.id, session)
            result["deliberation"] = deliberation_result
        except Exception as exc:
            logger.warning("Deliberation failed for thought %s: %s", thought.id, exc)
            result["deliberation_error"] = str(exc)

    return result


async def _outbound_in_cooldown(user: User, now: datetime, session: AsyncSession) -> bool:
    cooldown_start = now - timedelta(minutes=user.proactive_cooldown_minutes)
    pe = await session.execute(
        select(ProactiveEvent).where(
            ProactiveEvent.user_id == user.id,
            ProactiveEvent.decision == "sent",
            ProactiveEvent.created_at >= cooldown_start,
        ).limit(1)
    )
    if pe.scalar_one_or_none() is not None:
        return True
    hbe = await session.execute(
        select(HeartbeatEvent).where(
            HeartbeatEvent.user_id == user.id,
            HeartbeatEvent.decision == "sent",
            HeartbeatEvent.created_at >= cooldown_start,
        ).limit(1)
    )
    return hbe.scalar_one_or_none() is not None


async def _deliberated_today(
    user_id: str, signals: list, session: AsyncSession
) -> bool:
    """Check if any of the same-kind signals were already deliberated into an utterance today."""
    if not signals:
        return False
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    signal_kinds = {str(s.kind) for s in signals}

    # A thought became an utterance today with matching signal kinds in breakdown
    result = await session.execute(
        select(Thought).where(
            Thought.user_id == user_id,
            Thought.became_utterance.is_(True),
            Thought.created_at >= today_start,
        ).limit(20)
    )
    for thought in result.scalars():
        breakdown = thought.salience_breakdown or {}
        for key in breakdown:
            kind_prefix = key.split("_")[0] if "_" in key else key
            if any(kind_prefix in k for k in signal_kinds):
                return True
    return False


def _summarize_signals(signals: list, time_ctx: dict) -> str:
    parts: list[str] = []
    for sig in signals[:5]:
        kind = str(sig.kind)
        val = sig.value if isinstance(sig.value, dict) else {}
        if kind == "weather":
            parts.append(
                f"weather: {val.get('temp_c', '?')}°C, "
                f"{val.get('humidity_pct', '?')}% humidity, "
                f"{val.get('condition', '?')}"
            )
        elif kind == "calendar_event":
            parts.append(f"event: {val.get('title', '?')} at {val.get('start_at', '?')}")
        elif kind in {"wearable_recovery", "wearable_sleep"}:
            parts.append(f"{kind}: {val}")
    circadian = time_ctx.get("circadian_phase", "")
    if circadian:
        parts.append(f"circadian: {circadian}")
    return "; ".join(parts) or "signals present"
