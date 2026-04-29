from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.agent.time_context import build_time_context
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
        for user in users:
            refresh_jobs += await self.ensure_refresh_jobs(user.id, now=now)

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
        return {"refresh_jobs": refresh_jobs, "open_loop_jobs": open_loop_jobs}

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
        if not user.proactive_enabled:
            return False, "proactive_disabled"
        if user.proactive_paused_until is not None and user.proactive_paused_until > now:
            return False, "proactive_paused"
        if user.monthly_llm_tokens_used >= user.monthly_llm_token_budget:
            return False, "quota_exceeded"
        time_context = build_time_context(user, now=now)
        if time_context.quiet_hours:
            return False, "quiet_hours"
        cooldown_start = now - timedelta(minutes=user.proactive_cooldown_minutes)
        recent_sent = await self.session.execute(
            select(ProactiveEvent)
            .where(
                ProactiveEvent.user_id == user.id,
                ProactiveEvent.decision == "sent",
                ProactiveEvent.created_at >= cooldown_start,
            )
            .limit(1)
        )
        if recent_sent.scalar_one_or_none() is not None:
            return False, "cooldown"
        recent_heartbeat = await self.session.execute(
            select(HeartbeatEvent)
            .where(
                HeartbeatEvent.user_id == user.id,
                HeartbeatEvent.decision == "sent",
                HeartbeatEvent.created_at >= cooldown_start,
            )
            .limit(1)
        )
        if recent_heartbeat.scalar_one_or_none() is not None:
            return False, "cooldown"
        day_start = now - timedelta(hours=24)
        sent_today = await self.session.execute(
            select(HeartbeatEvent).where(
                HeartbeatEvent.user_id == user.id,
                HeartbeatEvent.decision == "sent",
                HeartbeatEvent.created_at >= day_start,
            )
        )
        if len(list(sent_today.scalars())) >= user.proactive_max_per_day:
            return False, "daily_limit"
        return True, "eligible"

    async def should_send_soft(
        self,
        job: HeartbeatJob,
        user: User,
        now: datetime,
    ) -> tuple[str, str | None, str, dict, str | None]:
        """LLM soft gate. Returns (decision, action, reason, audit input, model)."""
        from healthclaw.heartbeat.decision import build_decision_input, decide

        time_context = build_time_context(user, now=now)

        open_loops_result = await self.session.execute(
            select(OpenLoop).where(
                OpenLoop.user_id == user.id,
                OpenLoop.status == "open",
            ).limit(10)
        )
        open_loops = list(open_loops_result.scalars())

        # Load last 3 exchange pairs for context
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

        engagement = await self._engagement_state(user.id)
        relationship = build_relationship_context(engagement, now=now)
        decision_input = build_decision_input(
            job,
            user,
            time_context,
            open_loops,
            recent_exchanges,
            relationship=relationship,
        )

        stale_open_loop = self._has_stale_open_loop(job, open_loops)
        if job.kind == "autonomous_tick" and self._recent_meaningful_exchange_within(
            relationship,
            hours=12,
            now=now,
        ) and not stale_open_loop:
            return "skip", None, "recent_meaningful_exchange", decision_input, None
        if (
            job.kind == "autonomous_tick"
            and float(relationship.get("sentiment_ema") or 0.0) < -0.5
            and not stale_open_loop
            and not parse_heartbeat_md(user.heartbeat_md).standing_intent
        ):
            return "skip", None, "low_sentiment_without_trigger", decision_input, None

        if job.kind in {"ritual", "autonomous_tick"} and self._silent_for_48h(user, now):
            if not self._allows_long_silence_ping(user.heartbeat_md):
                return "skip", None, "user_silent_48h", decision_input, None

        result = await decide(
            job=job,
            user=user,
            time_context=time_context,
            open_loops=open_loops,
            recent_exchanges=recent_exchanges,
            settings=self._settings,
            relationship=relationship,
        )
        return result.decision, result.action, result.reason, result.decision_input, result.model

    @staticmethod
    def _silent_for_48h(user: User, now: datetime) -> bool:
        if user.last_active_at is None:
            return True
        last_active = user.last_active_at
        if last_active.tzinfo is None:
            last_active = last_active.replace(tzinfo=UTC)
        return now - last_active >= timedelta(hours=48)

    async def _engagement_state(self, user_id: str) -> UserEngagementState | None:
        result = await self.session.execute(
            select(UserEngagementState).where(UserEngagementState.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _has_stale_open_loop(job: HeartbeatJob, open_loops: list[OpenLoop]) -> bool:
        if isinstance(job.payload.get("open_loop_id"), str):
            return True
        now = datetime.now(UTC)
        for loop in open_loops:
            created_at = loop.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            else:
                created_at = created_at.astimezone(UTC)
            if (now - created_at).total_seconds() >= 18 * 3600:
                return True
        return False

    @staticmethod
    def _recent_meaningful_exchange_within(
        relationship: dict,
        *,
        hours: int,
        now: datetime,
    ) -> bool:
        value = relationship.get("last_meaningful_exchange_at")
        if not isinstance(value, datetime):
            return False
        last_meaningful = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return now - last_meaningful.astimezone(UTC) <= timedelta(hours=hours)

    @staticmethod
    def _allows_long_silence_ping(heartbeat_md: str) -> bool:
        return parse_heartbeat_md(heartbeat_md).allow_long_silence is True

    async def render_job(self, job: HeartbeatJob, action_override: str | None = None) -> str:
        # Ritual: use the action_override from the decision gate, or fall back to the template
        if job.kind == "ritual":
            if action_override:
                return action_override
            return str(job.payload.get("prompt_template") or "How are you doing today?")

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
