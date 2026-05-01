from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.agent.wellbeing import WellbeingDecision
from healthclaw.core.tracing import start_span
from healthclaw.db.models import HeartbeatEvent, HeartbeatJob, ProactiveEvent, Thought

if TYPE_CHECKING:
    from healthclaw.agent.time_context import TimeContext
    from healthclaw.db.models import User

logger = logging.getLogger(__name__)

_HIGH_SALIENCE_QUIET_OVERRIDE = 0.85


@dataclass(frozen=True)
class GateOutcome:
    emit: bool
    message_seed: str
    rationale: str
    thought_id: str
    heartbeat_job_id: str | None = None


class SpeechGate:
    """Explicit, uniform gate between inner deliberation and outbound speech.

    Hard rules run first (no LLM). If all pass, the pre-computed WellbeingDecision
    from deliberation is used directly — no second LLM call.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def evaluate(
        self,
        thought: Thought,
        user: "User",
        time_ctx: "TimeContext",
        decision: WellbeingDecision,
    ) -> GateOutcome:
        async with start_span(
            "speech_gate",
            {"user_id": user.id, "thought_id": thought.id, "salience": thought.salience},
        ):
            reject_reason = await self._hard_gate(thought, user, time_ctx)
            if reject_reason:
                logger.debug(
                    "SpeechGate blocked for user %s: %s", user.id, reject_reason
                )
                return GateOutcome(
                    emit=False,
                    message_seed="",
                    rationale=reject_reason,
                    thought_id=thought.id,
                )

            if not decision.reach_out:
                return GateOutcome(
                    emit=False,
                    message_seed="",
                    rationale=decision.rationale or "deliberation_held",
                    thought_id=thought.id,
                )

            job = await self._create_heartbeat_job(thought, user, decision)
            thought.became_utterance = True
            thought.heartbeat_job_id = job.id
            await self.session.flush()

            return GateOutcome(
                emit=True,
                message_seed=decision.message_seed,
                rationale=decision.rationale,
                thought_id=thought.id,
                heartbeat_job_id=job.id,
            )

    async def _hard_gate(
        self,
        thought: Thought,
        user: "User",
        time_ctx: "TimeContext",
    ) -> str | None:
        """Return a rejection reason string if the gate should block, else None."""
        now = datetime.now(UTC)

        # Quiet hours — allow through if salience is extreme (emergency-like)
        if time_ctx.quiet_hours and thought.salience < _HIGH_SALIENCE_QUIET_OVERRIDE:
            return "quiet_hours"

        # Daily cap
        outbound_count = await self._outbound_count_24h(user.id, now)
        if outbound_count >= user.proactive_max_per_day:
            return "daily_cap_reached"

        # Cooldown window
        if user.proactive_cooldown_minutes and await self._in_cooldown(
            user.id, now, user.proactive_cooldown_minutes
        ):
            return "cooldown"

        # Dedup: same signal kind already spoke today
        if await self._deduped_today(user.id, thought, now):
            return "dedup_today"

        return None

    async def _outbound_count_24h(self, user_id: str, now: datetime) -> int:
        day_start = now - timedelta(hours=24)
        pe = await self.session.execute(
            select(ProactiveEvent).where(
                ProactiveEvent.user_id == user_id,
                ProactiveEvent.decision == "sent",
                ProactiveEvent.created_at >= day_start,
            )
        )
        hbe = await self.session.execute(
            select(HeartbeatEvent).where(
                HeartbeatEvent.user_id == user_id,
                HeartbeatEvent.decision == "sent",
                HeartbeatEvent.created_at >= day_start,
            )
        )
        return len(list(pe.scalars())) + len(list(hbe.scalars()))

    async def _in_cooldown(self, user_id: str, now: datetime, cooldown_minutes: int) -> bool:
        cutoff = now - timedelta(minutes=cooldown_minutes)
        pe = await self.session.execute(
            select(ProactiveEvent).where(
                ProactiveEvent.user_id == user_id,
                ProactiveEvent.decision == "sent",
                ProactiveEvent.created_at >= cutoff,
            ).limit(1)
        )
        if pe.scalar_one_or_none() is not None:
            return True
        hbe = await self.session.execute(
            select(HeartbeatEvent).where(
                HeartbeatEvent.user_id == user_id,
                HeartbeatEvent.decision == "sent",
                HeartbeatEvent.created_at >= cutoff,
            ).limit(1)
        )
        return hbe.scalar_one_or_none() is not None

    async def _deduped_today(self, user_id: str, thought: Thought, now: datetime) -> bool:
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        breakdown = thought.salience_breakdown or {}
        signal_kind_prefixes = {
            k.split("_")[0] for k in breakdown if not k.startswith("_")
        }
        result = await self.session.execute(
            select(Thought).where(
                Thought.user_id == user_id,
                Thought.became_utterance.is_(True),
                Thought.created_at >= today_start,
                Thought.id != thought.id,
            ).limit(10)
        )
        for prev in result.scalars():
            prev_breakdown = prev.salience_breakdown or {}
            prev_prefixes = {k.split("_")[0] for k in prev_breakdown if not k.startswith("_")}
            if signal_kind_prefixes & prev_prefixes:
                return True
        return False

    async def _create_heartbeat_job(
        self, thought: Thought, user: "User", decision: WellbeingDecision
    ) -> HeartbeatJob:
        from datetime import timedelta

        from healthclaw.agent.wellbeing import parse_delay_minutes

        now = datetime.now(UTC)
        delay = parse_delay_minutes(decision.when)
        due_at = now + timedelta(minutes=delay) if delay else now

        dedup_key_suffix = "_".join(
            k for k in (thought.salience_breakdown or {}) if not k.startswith("_")
        )[:64]
        idempotency_key = (
            f"afferent:{user.id}:{thought.id}:{now.strftime('%Y-%m-%dT%H')}"
        )

        job = HeartbeatJob(
            user_id=user.id,
            kind="afferent_signal",
            due_at=due_at,
            channel=user.notification_channel or "telegram",
            payload={
                "thought_id": thought.id,
                "signal_ids": thought.signal_ids,
                "summary": thought.content_summary,
                "salience": thought.salience,
                "message_seed": decision.message_seed,
            },
            idempotency_key=idempotency_key,
        )
        self.session.add(job)
        await self.session.flush()
        return job
