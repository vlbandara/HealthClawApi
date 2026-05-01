from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.agent.time_context import build_time_context
from healthclaw.agent.wellbeing import WellbeingDecision
from healthclaw.core.config import Settings, get_settings
from healthclaw.db.models import (
    ConversationThread,
    HeartbeatEvent,
    HeartbeatJob,
    Memory,
    Message,
    OpenLoop,
    ProactiveEvent,
    User,
    UserEngagementState,
)
from healthclaw.engagement.metrics import build_relationship_context
from healthclaw.heartbeat.profile import parse_heartbeat_md
from healthclaw.memory.service import MemoryService


class HeartbeatService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self._settings = settings or get_settings()

    async def create_open_loop(
        self,
        *,
        user_id: str,
        thread_id: str,
        source_message_id: str,
        title: str,
        kind: str = "commitment",
        due_after: datetime | None = None,
    ) -> OpenLoop:
        due_after = due_after or datetime.now(UTC) + timedelta(hours=18)
        result = await self.session.execute(
            select(OpenLoop)
            .where(
                OpenLoop.user_id == user_id,
                OpenLoop.status == "open",
                OpenLoop.kind == kind,
                OpenLoop.title == title[:240],
            )
            .limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
        open_loop = OpenLoop(
            user_id=user_id,
            thread_id=thread_id,
            source_message_id=source_message_id,
            kind=kind,
            title=title[:240],
            due_after=due_after,
            metadata_={"source": "memory_extraction"},
        )
        self.session.add(open_loop)
        thread = await self.session.get(ConversationThread, thread_id)
        if thread is not None:
            thread.open_loop_count += 1
        await self.session.flush()
        await self.ensure_job_for_open_loop(open_loop)
        return open_loop

    async def ensure_job_for_open_loop(self, open_loop: OpenLoop) -> HeartbeatJob:
        due_at = open_loop.due_after or datetime.now(UTC) + timedelta(hours=18)
        key = f"open-loop:{open_loop.id}:{due_at.date().isoformat()}"
        result = await self.session.execute(
            select(HeartbeatJob).where(HeartbeatJob.idempotency_key == key).limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
        job = HeartbeatJob(
            user_id=open_loop.user_id,
            open_loop_id=open_loop.id,
            kind="open_loop_followup",
            due_at=due_at,
            channel="telegram",
            payload={"title": open_loop.title, "kind": open_loop.kind},
            idempotency_key=key,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def ensure_refresh_jobs(self, user_id: str, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        count = 0
        memory_service = MemoryService(self.session)
        for memory in await memory_service.memories_due_for_refresh(user_id, now=now):
            key = f"memory-refresh:{memory.id}:{now.date().isoformat()}"
            result = await self.session.execute(
                select(HeartbeatJob).where(HeartbeatJob.idempotency_key == key).limit(1)
            )
            if result.scalar_one_or_none() is not None:
                continue
            self.session.add(
                HeartbeatJob(
                    user_id=user_id,
                    kind="memory_refresh",
                    due_at=now,
                    channel="telegram",
                    payload={"memory_id": memory.id, "kind": memory.kind, "key": memory.key},
                    idempotency_key=key,
                )
            )
            count += 1
        return count

    async def schedule_due_work(self, now: datetime) -> dict[str, int]:
        result = await self.session.execute(
            select(User).where(User.proactive_enabled.is_(True))
        )
        users = list(result.scalars())
        refresh_jobs = 0
        internal_jobs = 0
        for user in users:
            refresh_jobs += await self.ensure_refresh_jobs(user.id, now=now)
            internal_jobs += await self.schedule_internal_jobs(user, now=now)

        loop_result = await self.session.execute(
            select(OpenLoop).where(
                OpenLoop.status == "open",
                OpenLoop.due_after.is_not(None),
                OpenLoop.due_after <= now,
            )
        )
        open_loop_jobs = 0
        for open_loop in loop_result.scalars():
            before = await self.session.execute(
                select(HeartbeatJob).where(
                    HeartbeatJob.open_loop_id == open_loop.id,
                    HeartbeatJob.status == "scheduled",
                )
            )
            if before.scalar_one_or_none() is None:
                await self.ensure_job_for_open_loop(open_loop)
                open_loop_jobs += 1
        return {
            "refresh_jobs": refresh_jobs,
            "open_loop_jobs": open_loop_jobs,
            "internal_jobs": internal_jobs,
        }

    async def schedule_internal_jobs(self, user: User, *, now: datetime) -> int:
        """Idempotently create one dream + one consolidate job per user per day,
        due at the start of their quiet window. Returns number of new jobs created."""
        import zoneinfo

        time_context = build_time_context(user, now=now)

        # Compute UTC time of the next quiet-window start
        if time_context.quiet_hours:
            # Already in quiet hours — schedule immediately
            quiet_start_utc = now
        else:
            user_tz = zoneinfo.ZoneInfo(user.timezone or "UTC")
            quiet_h, quiet_m = map(int, (user.quiet_start or "22:00").split(":"))
            now_local = now.astimezone(user_tz)
            quiet_local = now_local.replace(
                hour=quiet_h, minute=quiet_m, second=0, microsecond=0
            )
            if now_local >= quiet_local:
                quiet_local = quiet_local + timedelta(days=1)
            quiet_start_utc = quiet_local.astimezone(UTC)

        date_key = quiet_start_utc.date().isoformat()
        count = 0
        for kind in ("dream", "consolidate"):
            key = f"{kind}:{user.id}:{date_key}"
            existing = await self.session.execute(
                select(HeartbeatJob).where(HeartbeatJob.idempotency_key == key).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                continue
            self.session.add(
                HeartbeatJob(
                    user_id=user.id,
                    kind=kind,
                    due_at=quiet_start_utc,
                    channel="internal",
                    payload={"reason": f"scheduled_{kind}"},
                    idempotency_key=key,
                )
            )
            count += 1
        if count:
            await self.session.flush()
        return count

    async def due_jobs(self, now: datetime) -> list[HeartbeatJob]:
        result = await self.session.execute(
            select(HeartbeatJob).where(
                HeartbeatJob.status == "scheduled",
                HeartbeatJob.due_at <= now,
            )
        )
        return list(result.scalars())

    async def should_send(self, job: HeartbeatJob, now: datetime) -> tuple[bool, str]:
        user = await self.session.get(User, job.user_id)
        if user is None:
            return False, "user_not_found"

        # Internal jobs (dream/consolidate) only run DURING quiet hours.
        if job.kind in {"dream", "consolidate"}:
            time_context = build_time_context(user, now=now)
            if not time_context.quiet_hours:
                return False, "awaiting_quiet_hours"
        return True, "eligible"

    async def should_send_soft(
        self,
        job: HeartbeatJob,
        user: User,
        now: datetime,
    ) -> WellbeingDecision:
        """Reflection-driven outbound decision for a heartbeat job.

        For afferent_signal jobs the speech gate already ran a full deliberation + wellbeing
        reflection and embedded the message_seed. Pass it through without a second LLM call.
        """
        if job.kind == "afferent_signal" and job.payload.get("message_seed"):
            return WellbeingDecision(
                reach_out=True,
                when="now",
                message_seed=str(job.payload["message_seed"]),
                rationale="afferent_signal_pre_deliberated",
                model=None,
                decision_input={"kind": "afferent_signal", "thought_id": job.payload.get("thought_id")},
            )

        from healthclaw.heartbeat.decision import decide

        time_context = build_time_context(user, now=now)
        engagement = await self._engagement_state(user.id)
        relationship = build_relationship_context(engagement, now=now)
        outbound_count_24h, last_outbound_at = await self._outbound_activity(user.id, now=now)

        if outbound_count_24h >= user.proactive_max_per_day:
            return WellbeingDecision(
                reach_out=False,
                when="hold",
                message_seed="",
                rationale="daily delivery cap reached",
                model=None,
                decision_input={
                    "candidate": {"kind": job.kind, "channel": job.channel},
                    "delivery_floor_applied": True,
                },
            )

        open_loops_result = await self.session.execute(
            select(OpenLoop).where(
                OpenLoop.user_id == user.id,
                OpenLoop.status == "open",
            ).limit(10)
        )
        open_loops = list(open_loops_result.scalars())

        msgs_result = await self.session.execute(
            select(Message)
            .where(
                Message.user_id == user.id,
                Message.role.in_(["user", "assistant"]),
            )
            .order_by(Message.created_at.desc())
            .limit(6)
        )
        recent_msgs = list(reversed(list(msgs_result.scalars())))
        recent_exchanges = [
            {"role": m.role, "content": m.content[:200]}
            for m in recent_msgs
        ]

        result = await decide(
            job=job,
            user=user,
            time_context=time_context,
            open_loops=open_loops,
            recent_exchanges=recent_exchanges,
            settings=self._settings,
            relationship=relationship,
            outbound_count_24h=outbound_count_24h,
            last_outbound_at=last_outbound_at,
            daily_cap=user.proactive_max_per_day,
        )
        return WellbeingDecision(
            reach_out=result.decision == "run",
            when=result.when,
            message_seed=result.action or "",
            rationale=result.reason,
            model=result.model or None,
            decision_input=result.decision_input,
        )

    async def _engagement_state(self, user_id: str) -> UserEngagementState | None:
        result = await self.session.execute(
            select(UserEngagementState).where(UserEngagementState.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def render_job(self, job: HeartbeatJob, action_override: str | None = None) -> str:
        # Ritual: use the action_override from the decision gate, or fall back to the template
        if job.kind == "ritual":
            if action_override:
                return action_override
            return str(job.payload.get("prompt_template") or "How are you doing today?")

        # Afferent signal: the speech gate already produced a message_seed in the payload
        if job.kind == "afferent_signal":
            seed = str(job.payload.get("message_seed") or "")
            if seed:
                return seed
            # Fallback: render a summary of the signal
            summary = str(job.payload.get("summary") or "something caught my attention")
            return summary

        if job.kind == "memory_refresh":
            memory = None
            memory_id = job.payload.get("memory_id")
            if isinstance(memory_id, str):
                memory = await self.session.get(Memory, memory_id)
            label = f"{memory.kind}:{memory.key}" if memory is not None else "something I remember"
            return (
                f"Quick check-in: I still have {label} in mind. "
                "Is that still true, or should I update it?"
            )
        title = str(job.payload.get("title") or "the thing you said you wanted to do")
        return (
            f"Small follow-up: you mentioned {title}. "
            "Did you get a chance to do it, or should we make the next step smaller?"
        )

    async def schedule_autonomous_wake(self, now: datetime) -> dict[str, int]:
        """Create due autonomous-tick heartbeat jobs for users with a meaningful trigger."""
        result = await self.session.execute(select(User).where(User.proactive_enabled.is_(True)))
        users = list(result.scalars())
        scheduled = 0
        skipped_no_trigger = 0

        bucket_minute = (now.minute // 15) * 15
        bucket = now.replace(minute=bucket_minute, second=0, microsecond=0)

        for user in users:
            trigger = await self._autonomous_trigger(user, now)
            if trigger is None:
                skipped_no_trigger += 1
                continue
            key = f"autonomous:{user.id}:{bucket.isoformat()}"
            existing = await self.session.execute(
                select(HeartbeatJob).where(HeartbeatJob.idempotency_key == key).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                continue
            self.session.add(
                HeartbeatJob(
                    user_id=user.id,
                    kind="autonomous_tick",
                    due_at=now,
                    channel=user.notification_channel or "telegram",
                    payload=trigger,
                    idempotency_key=key,
                )
            )
            scheduled += 1
        if scheduled:
            await self.session.flush()
        return {"scheduled": scheduled, "skipped_no_trigger": skipped_no_trigger}

    async def _autonomous_trigger(self, user: User, now: datetime) -> dict | None:
        heartbeat_profile = parse_heartbeat_md(user.heartbeat_md)
        heartbeat_intent = bool(heartbeat_profile.standing_intent)
        loop_result = await self.session.execute(
            select(OpenLoop)
            .where(
                OpenLoop.user_id == user.id,
                OpenLoop.status == "open",
                OpenLoop.created_at <= now - timedelta(hours=18),
            )
            .order_by(OpenLoop.created_at.asc())
            .limit(1)
        )
        stale_loop = loop_result.scalar_one_or_none()
        dream_nominated = bool(heartbeat_profile.wake_text)
        if not (heartbeat_intent or stale_loop is not None or dream_nominated):
            return None
        payload: dict[str, object] = {
            "reason": "autonomous_wake_check",
            "heartbeat_intent": heartbeat_intent,
            "dream_nominated": dream_nominated,
        }
        if heartbeat_profile.wake_text:
            payload["wake_text"] = heartbeat_profile.wake_text
        if stale_loop is not None:
            payload["open_loop_id"] = stale_loop.id
            payload["open_loop_title"] = stale_loop.title
        return payload

    async def record_event(
        self,
        job: HeartbeatJob,
        decision: str,
        reason: str,
        *,
        trace_id: str | None = None,
        decision_input: dict | None = None,
        decision_model: str | None = None,
        skip_reason: str | None = None,
    ) -> None:
        self.session.add(
            HeartbeatEvent(
                user_id=job.user_id,
                job_id=job.id,
                open_loop_id=job.open_loop_id,
                decision=decision,
                reason=reason,
                channel=job.channel,
                trace_id=trace_id,
                decision_input=decision_input or {},
                decision_model=decision_model,
                skip_reason=skip_reason,
            )
        )

    async def _outbound_activity(
        self,
        user_id: str,
        *,
        now: datetime,
    ) -> tuple[int, datetime | None]:
        day_start = now - timedelta(hours=24)
        proactive_stats = (
            await self.session.execute(
                select(func.count(), func.max(ProactiveEvent.created_at)).where(
                    ProactiveEvent.user_id == user_id,
                    ProactiveEvent.decision == "sent",
                    ProactiveEvent.created_at >= day_start,
                )
            )
        ).one()
        heartbeat_stats = (
            await self.session.execute(
                select(func.count(), func.max(HeartbeatEvent.created_at)).where(
                    HeartbeatEvent.user_id == user_id,
                    HeartbeatEvent.decision == "sent",
                    HeartbeatEvent.created_at >= day_start,
                )
            )
        ).one()
        proactive_count, proactive_last = proactive_stats
        heartbeat_count, heartbeat_last = heartbeat_stats
        return int(proactive_count or 0) + int(heartbeat_count or 0), _latest_datetime(
            proactive_last,
            heartbeat_last,
        )


def _latest_datetime(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    normalized = [
        value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        for value in present
    ]
    return max(normalized)
