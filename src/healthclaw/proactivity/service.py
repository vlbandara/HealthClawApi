from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.agent.time_context import build_time_context
from healthclaw.db.models import (
    ChannelAccount,
    ConversationThread,
    ProactiveEvent,
    Reminder,
    User,
)


class ProactivityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def due_reminders(self, now: datetime) -> list[Reminder]:
        result = await self.session.execute(
            select(Reminder).where(Reminder.status == "scheduled", Reminder.due_at <= now)
        )
        return list(result.scalars())

    async def should_send(self, reminder: Reminder, now: datetime) -> tuple[bool, str]:
        user = await self.session.get(User, reminder.user_id)
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

        day_start = now - timedelta(hours=24)
        sent_today = await self.session.execute(
            select(ProactiveEvent).where(
                ProactiveEvent.user_id == user.id,
                ProactiveEvent.decision == "sent",
                ProactiveEvent.created_at >= day_start,
            )
        )
        if len(list(sent_today.scalars())) >= user.proactive_max_per_day:
            return False, "daily_limit"

        recent_activity = await self.session.execute(
            select(ConversationThread)
            .where(
                ConversationThread.user_id == user.id,
                ConversationThread.last_message_at >= now - timedelta(minutes=15),
            )
            .limit(1)
        )
        if recent_activity.scalar_one_or_none() is not None:
            return False, "recent_activity"
        return True, "eligible"

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
