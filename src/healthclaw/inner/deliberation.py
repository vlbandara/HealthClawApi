from __future__ import annotations

import logging
from importlib.resources import files

from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.core.config import get_settings
from healthclaw.core.tracing import start_span
from healthclaw.db.models import Thought

logger = logging.getLogger(__name__)

_PROMPT_MODULES = ("companion.md", "wellbeing_lens.md")


async def run_inner_deliberation(thought_id: str, session: AsyncSession) -> dict:
    """LLM deliberation step — called only when salience crosses the threshold.

    Reuses wellbeing.reflect_on_wellbeing infrastructure (same WellbeingDecision shape,
    same OpenRouter call) but with the inner_voice system prompt. If reach_out=True,
    delegates to SpeechGate which creates the HeartbeatJob.
    """
    settings = get_settings()
    thought = await session.get(Thought, thought_id)
    if thought is None:
        return {"status": "skipped", "reason": "thought_not_found"}

    user = None
    from healthclaw.db.models import User

    user = await session.get(User, thought.user_id)
    if user is None:
        return {"status": "skipped", "reason": "user_not_found"}

    from healthclaw.agent.time_context import TimeContext

    try:
        time_ctx = TimeContext(**thought.time_context)
    except Exception:
        from healthclaw.agent.time_context import build_time_context

        time_ctx = build_time_context(user)

    # Build decision input reusing the wellbeing input builder
    from healthclaw.agent.wellbeing import build_wellbeing_input, reflect_on_wellbeing
    from sqlalchemy import select
    from healthclaw.db.models import Message, OpenLoop, UserEngagementState
    from healthclaw.engagement.metrics import build_relationship_context
    from datetime import UTC, datetime

    now = datetime.now(UTC)

    engagement_result = await session.execute(
        select(UserEngagementState).where(UserEngagementState.user_id == user.id)
    )
    engagement = engagement_result.scalar_one_or_none()
    relationship = build_relationship_context(engagement, now=now) if engagement else {}

    open_loops_result = await session.execute(
        select(OpenLoop).where(
            OpenLoop.user_id == user.id,
            OpenLoop.status == "open",
        ).limit(5)
    )
    open_loops = [
        {"id": ol.id, "title": ol.title, "kind": ol.kind}
        for ol in open_loops_result.scalars()
    ]

    msgs_result = await session.execute(
        select(Message)
        .where(Message.user_id == user.id, Message.role.in_(["user", "assistant"]))
        .order_by(Message.created_at.desc())
        .limit(6)
    )
    recent_exchanges = [
        {"role": m.role, "content": m.content[:200]}
        for m in reversed(list(msgs_result.scalars()))
    ]

    decision_input = build_wellbeing_input(
        user_id=user.id,
        source_kind="inner_tick",
        timezone=user.timezone,
        quiet_start=user.quiet_start,
        quiet_end=user.quiet_end,
        time_context=time_ctx.to_dict(),
        heartbeat_md=user.heartbeat_md or "",
        relationship=relationship,
        open_loops=open_loops,
        recent_exchanges=recent_exchanges,
        candidate={
            "kind": "afferent_signal",
            "summary": thought.content_summary,
            "salience": thought.salience,
            "salience_breakdown": thought.salience_breakdown,
        },
        last_active_at=user.last_active_at,
        proactive_paused_until=user.proactive_paused_until,
        proactive_enabled=user.proactive_enabled,
    )

    # Stamp thought as deliberated
    thought.mode = "deliberated"
    await session.flush()

    async with start_span("inner_deliberation", {"user_id": user.id, "thought_id": thought_id}):
        decision = await reflect_on_wellbeing(
            settings=settings,
            user_id=user.id,
            decision_input=decision_input,
            metadata={"source": "inner_tick", "thought_id": thought_id},
        )

    if not decision.reach_out:
        logger.debug(
            "Inner deliberation held for user %s: %s", user.id, decision.rationale
        )
        return {"status": "held", "rationale": decision.rationale}

    # Route through speech gate
    from healthclaw.inner.speech_gate import SpeechGate

    gate = SpeechGate(session)
    outcome = await gate.evaluate(thought, user, time_ctx, decision)
    return {"status": "gate_evaluated", "emit": outcome.emit, "rationale": outcome.rationale}
