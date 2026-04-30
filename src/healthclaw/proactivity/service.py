from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.agent.time_context import build_time_context
from healthclaw.agent.wellbeing import WellbeingDecision, reflect_on_wellbeing
from healthclaw.core.config import get_settings
from healthclaw.db.models import (
    ChannelAccount,
    ConversationThread,
    HeartbeatEvent,
    Message,
    OpenLoop,
    ProactiveEvent,
    Reminder,
    User,
    UserEngagementState,
)
from healthclaw.engagement.metrics import build_relationship_context


class ProactivityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._settings = get_settings()

    async def due_reminders(self, now: datetime) -> list[Reminder]:
        result = await self.session.execute(
            select(Reminder).where(Reminder.status == "scheduled", Reminder.due_at <= now)
        )
        return list(result.scalars())

    async def create_reminder(
        self,
        *,
        user_id: str,
        text: str,
        due_at: datetime,
        channel: str = "telegram",
        idempotency_key: str | None = None,
    ) -> Reminder:
        key = idempotency_key
        if key:
            existing = await self.session.execute(
                select(Reminder).where(Reminder.idempotency_key == key).limit(1)
            )
            reminder = existing.scalar_one_or_none()
            if reminder is not None:
                return reminder
        else:
            key = f"reminder:{user_id}:{int(due_at.timestamp())}:{text[:40].strip().lower()}"

        reminder = Reminder(
            user_id=user_id,
            text=text,
            due_at=due_at,
            channel=channel,
            status="scheduled",
            idempotency_key=key,
        )
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def should_send(self, reminder: Reminder, now: datetime) -> WellbeingDecision:
        user = await self.session.get(User, reminder.user_id)
        if user is None:
            return WellbeingDecision(
                reach_out=False,
                when="hold",
                message_seed="",
                rationale="user not found",
                model=None,
                decision_input={"candidate": {"kind": "reminder", "channel": reminder.channel}},
            )

        time_context = build_time_context(user, now=now)
        open_loops_result = await self.session.execute(
            select(OpenLoop).where(OpenLoop.user_id == user.id, OpenLoop.status == "open").limit(10)
        )
        open_loops = list(open_loops_result.scalars())

        msgs_result = await self.session.execute(
            select(Message)
            .where(Message.user_id == user.id, Message.role.in_(["user", "assistant"]))
            .order_by(Message.created_at.desc())
            .limit(6)
        )
        recent_msgs = list(reversed(list(msgs_result.scalars())))
        recent_exchanges = [
            {"role": message.role, "content": message.content[:200]}
            for message in recent_msgs
        ]

        engagement = await self._engagement_state(user.id)
        relationship = build_relationship_context(engagement, now=now)
        outbound_count_24h, last_outbound_at = await self._outbound_activity(user.id, now=now)
        recent_activity = await self._recent_activity_at(user.id)

        if outbound_count_24h >= user.proactive_max_per_day:
            return WellbeingDecision(
                reach_out=False,
                when="hold",
                message_seed="",
                rationale="daily delivery cap reached",
                model=None,
                decision_input={
                    "candidate": {"kind": "reminder", "channel": reminder.channel},
                    "delivery_floor_applied": True,
                },
            )

        candidate = {
            "kind": "reminder",
            "channel": reminder.channel,
            "details": {
                "text": reminder.text[:240],
                "due_at": _iso(reminder.due_at),
                "recent_thread_activity_at": _iso(recent_activity),
            },
        }
        decision_input = {
            "user_id": user.id,
            "source_kind": "reminder",
            "time_context": time_context.to_dict(),
            "user_profile": {
                "timezone": user.timezone,
                "quiet_window": {"start": user.quiet_start, "end": user.quiet_end},
                "proactive_enabled": user.proactive_enabled,
                "proactive_paused_until": _iso(user.proactive_paused_until),
                "last_active_at": _iso(user.last_active_at),
                "heartbeat_profile": user.heartbeat_md[:1200],
            },
            "relationship": _serialize_relationship(relationship),
            "open_loops": [
                {
                    "id": loop.id,
                    "title": loop.title,
                    "kind": loop.kind,
                    "age_hours": _age_hours(loop.created_at, now),
                }
                for loop in open_loops[:5]
            ],
            "recent_exchanges": recent_exchanges[-3:],
            "delivery_context": {
                "outbound_count_24h": outbound_count_24h,
                "last_outbound_at": _iso(last_outbound_at),
                "daily_cap": user.proactive_max_per_day,
                "monthly_llm_tokens_used": user.monthly_llm_tokens_used,
                "monthly_llm_token_budget": user.monthly_llm_token_budget,
            },
            "candidate": candidate,
        }
        return await reflect_on_wellbeing(
            settings=self._settings,
            user_id=user.id,
            decision_input=decision_input,
            metadata={"channel": reminder.channel, "source_kind": "reminder"},
        )

    async def record_decision(
        self,
        reminder: Reminder,
        decision: str,
        reason: str,
        *,
        trace_id: str | None = None,
    ) -> None:
        self.session.add(
            ProactiveEvent(
                user_id=reminder.user_id,
                reminder_id=reminder.id,
                decision=decision,
                reason=reason,
                channel=reminder.channel,
                trace_id=trace_id,
            )
        )

    async def external_channel_id(self, user_id: str, channel: str) -> str | None:
        result = await self.session.execute(
            select(ChannelAccount)
            .where(ChannelAccount.user_id == user_id, ChannelAccount.channel == channel)
            .order_by(ChannelAccount.created_at.desc())
            .limit(1)
        )
        account = result.scalar_one_or_none()
        if account is not None:
            return account.external_id
        prefix = f"{channel}:"
        if user_id.startswith(prefix):
            return user_id.removeprefix(prefix)
        return None

    async def _engagement_state(self, user_id: str) -> UserEngagementState | None:
        result = await self.session.execute(
            select(UserEngagementState).where(UserEngagementState.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def _recent_activity_at(self, user_id: str) -> datetime | None:
        result = await self.session.execute(
            select(ConversationThread.last_message_at)
            .where(ConversationThread.user_id == user_id)
            .order_by(ConversationThread.last_message_at.desc())
            .limit(1)
        )
        value = result.scalar_one_or_none()
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

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


def _serialize_relationship(relationship: dict[str, object]) -> dict[str, object]:
    payload = dict(relationship)
    last_meaningful = payload.get("last_meaningful_exchange_at")
    if isinstance(last_meaningful, datetime):
        payload["last_meaningful_exchange_at"] = _iso(last_meaningful)
    return payload


def _age_hours(created_at: datetime | None, now: datetime) -> float:
    if created_at is None:
        return 0.0
    created = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
    return (now.astimezone(UTC) - created.astimezone(UTC)).total_seconds() / 3600


def _latest_datetime(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    normalized = [
        value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        for value in present
    ]
    return max(normalized)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
