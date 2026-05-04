"""Route heartbeat / proactive jobs through the InnerSynthesizer + SpeechGate.

When INNER_SYNTHESIZER_ENABLED is True, heartbeat jobs that would have used the
legacy `should_send_soft` path instead go through:
  1. Build a fused intent request from job + recent signals + open loops
  2. Call InnerSynthesizer.synthesize()
  3. Pass the InnerIntent to SpeechGate.evaluate_intent()
  4. Return the gate decision (emit / defer / suppress)

Daily greeting de-dup is enforced here regardless of synthesizer state:
if the user has already received a greeting (or sent one) in the current local day,
any check_in/morning-greeting job is suppressed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class SynthRouteDecision:
    action: str        # "emit" | "defer" | "suppress"
    message: str | None = None
    defer_until: datetime | None = None
    reason: str = ""


async def route_heartbeat_job_through_synth(
    job,  # HeartbeatJob ORM object
    user,  # User ORM object
    session: AsyncSession,
    now: datetime | None = None,
) -> SynthRouteDecision | None:
    """Route a heartbeat job through the synthesizer.

    Returns a SynthRouteDecision, or None if the synthesizer is disabled
    (caller should fall back to the legacy path).
    """
    from healthclaw.core.config import get_settings
    settings = get_settings()

    if not settings.inner_synthesizer_enabled:
        return None  # caller uses legacy path

    now = now or datetime.now(UTC)

    # ── Daily greeting de-dup ─────────────────────────────────────────────────
    # If job kind is a check_in or morning_greeting and the user already had a
    # greeting exchange today, suppress.
    job_kind = str(getattr(job, "kind", "") or "")
    if job_kind in ("check_in", "morning_greeting", "routine_checkin"):
        already_greeted = await _already_greeted_today(user.id, session, user.timezone, now)
        if already_greeted:
            logger.debug(
                "route_synth: suppress %s for user=%s — already greeted today",
                job_kind, user.id,
            )
            return SynthRouteDecision(
                action="suppress",
                reason="already_greeted_today",
            )

    # ── Load signals + context ────────────────────────────────────────────────
    try:
        from healthclaw.agent.anticipation import populate_anticipation
        from healthclaw.agent.time_context import build_time_context
        from healthclaw.db.models import Signal, Thought
        from healthclaw.inner.motives import MotiveService
        from healthclaw.inner.salience import compute_salience
        from healthclaw.inner.speech_gate import SpeechGate
        from healthclaw.inner.synthesizer import InnerSynthesizer

        time_ctx = build_time_context(user, now=now)
        time_ctx_dict = time_ctx.to_dict()

        if settings.anticipation_enabled:
            time_ctx_dict = await populate_anticipation(user, time_ctx_dict, session)

        # Load recent signals
        from datetime import timedelta
        cutoff = now - timedelta(minutes=30)
        result = await session.execute(
            select(Signal)
            .where(Signal.user_id == user.id, Signal.observed_at >= cutoff)
            .order_by(Signal.observed_at.desc())
            .limit(10)
        )
        signals = list(result.scalars())

        # Load motives
        motives = []
        if settings.motives_enabled:
            motives = await MotiveService(session).get_active(user.id)

        # Compute salience — if zero and the job has no strong signal, may suppress
        salience = compute_salience(signals, time_ctx, motives=motives if motives else None)

        # Create a stub Thought for the synthesizer
        thought = Thought(
            user_id=user.id,
            mode="proactive",
            salience=salience,
            content_summary=f"heartbeat:{job_kind}",
        )
        session.add(thought)
        await session.flush()

        synthesizer = InnerSynthesizer(session)
        intent = await synthesizer.synthesize(
            thought.id, user, signals, motives, time_ctx_dict
        )

        gate = SpeechGate(session)
        outcome = await gate.evaluate_intent(thought, user, time_ctx, intent)

        logger.debug(
            "route_synth: user=%s job=%s intent_kind=%s gate=%s",
            user.id, job_kind, intent.kind, outcome.get("status"),
        )

        if outcome.get("status") == "emitted":
            return SynthRouteDecision(
                action="emit",
                message=intent.draft_message,
                reason="synthesizer_approved",
            )
        elif outcome.get("status") == "deferred":
            defer_iso = intent.earliest_send_at
            defer_dt = None
            if defer_iso:
                try:
                    defer_dt = datetime.fromisoformat(defer_iso)
                    if defer_dt.tzinfo is None:
                        defer_dt = defer_dt.replace(tzinfo=UTC)
                except Exception:
                    pass
            return SynthRouteDecision(
                action="defer",
                defer_until=defer_dt,
                reason="synthesizer_deferred",
            )
        else:
            return SynthRouteDecision(
                action="suppress",
                reason=f"synthesizer_{intent.kind}",
            )

    except Exception as exc:
        logger.warning("route_synth failed for user=%s job=%s: %s", user.id, job_kind, exc)
        return None  # fall back to legacy path on any error


async def _already_greeted_today(
    user_id: str,
    session: AsyncSession,
    timezone: str,
    now: datetime,
) -> bool:
    """Return True if any greeting message was sent/received today in the user's local timezone."""
    from zoneinfo import ZoneInfo

    from healthclaw.db.models import Message

    try:
        tz = ZoneInfo(timezone)
        local_now = now.astimezone(tz)
        local_day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Convert back to UTC for the DB query
        day_start_utc = local_day_start.astimezone(UTC)
    except Exception:
        day_start_utc = now.replace(hour=0, minute=0, second=0, microsecond=0)

    result = await session.execute(
        select(Message).where(
            Message.user_id == user_id,
            Message.created_at >= day_start_utc,
        ).limit(1)
    )
    # Any message today (either direction) means the user has been in contact
    return result.scalar_one_or_none() is not None
