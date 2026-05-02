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

    # WS6: populate anticipation fields (calendar events, day-arc, rhythm)
    from healthclaw.core.config import get_settings
    settings = get_settings()
    if settings.anticipation_enabled:
        from healthclaw.agent.anticipation import populate_anticipation
        time_ctx_dict = await populate_anticipation(user, time_ctx_dict, session)

    # WS6: load motives for motive-weighted salience
    motives = []
    if settings.motives_enabled:
        from healthclaw.inner.motives import MotiveService
        motives = await MotiveService(session).get_active_motives(user_id)

    outbound_in_cooldown = await _outbound_in_cooldown(user, now, session)
    already_deliberated_today = await _deliberated_today(user_id, signals, session)

    salience = compute_salience(
        signals,
        time_ctx_dict,
        outbound_in_cooldown=outbound_in_cooldown,
        quiet_hours=time_ctx.quiet_hours,
        already_deliberated_today=already_deliberated_today,
        motives=motives,
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
        # WS6: route to synthesizer when enabled, else fall back to legacy deliberation
        if settings.inner_synthesizer_enabled:
            from healthclaw.inner.synthesizer import InnerSynthesizer

            try:
                synthesizer = InnerSynthesizer(session)
                intent = await synthesizer.synthesize(
                    thought.id, user, signals, motives, time_ctx_dict
                )
                # Store intent fields on Thought for audit
                thought.intent_kind = intent.kind
                thought.intent_motive = intent.motive or None
                if intent.discarded:
                    thought.discarded_reason = intent.discarded_reason
                await session.flush()

                if intent.kind in {"reflect_silently", "wait"} or intent.discarded:
                    result["intent"] = intent.kind
                    result["status"] = "reflected_silently"
                elif intent.kind == "investigate" and intent.needs_web_search:
                    # Tavily search deferred to speech gate / heartbeat executor
                    result["intent"] = "investigate"
                    result["web_search_query"] = intent.web_search_query
                else:
                    # Route through upgraded speech gate with InnerIntent
                    from healthclaw.inner.speech_gate import SpeechGate
                    gate = SpeechGate(session)
                    outcome = await gate.evaluate_intent(thought, user, time_ctx, intent)
                    result["intent"] = intent.kind
                    result["status"] = "gate_evaluated"
                    result["emit"] = outcome.emit
                    result["rationale"] = outcome.rationale
            except Exception as exc:
                logger.warning("Synthesizer failed for thought %s: %s", thought.id, exc)
                result["synthesizer_error"] = str(exc)
        else:
            # Legacy deliberation path
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
