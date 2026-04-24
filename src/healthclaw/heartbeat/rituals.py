from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.db.models import HeartbeatJob, Ritual, User, new_id

logger = logging.getLogger(__name__)

DEFAULT_RITUALS = [
    {
        "kind": "morning_check_in",
        "title": "Morning check-in",
        "schedule_cron": "0 8 * * *",
        "prompt_template": (
            "Good morning. How are you feeling today, and what's one thing you want "
            "to stay on track with?"
        ),
    },
]


class RitualService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def seed_defaults_for_user(self, user: User) -> list[Ritual]:
        """Create default rituals for a new user if they don't already exist."""
        created: list[Ritual] = []
        for spec in DEFAULT_RITUALS:
            result = await self.session.execute(
                select(Ritual).where(
                    Ritual.user_id == user.id,
                    Ritual.kind == spec["kind"],
                )
            )
            if result.scalar_one_or_none() is not None:
                continue
            ritual = Ritual(
                id=new_id(),
                user_id=user.id,
                kind=spec["kind"],
                title=spec["title"],
                schedule_cron=spec["schedule_cron"],
                prompt_template=spec["prompt_template"],
                enabled=True,
            )
            self.session.add(ritual)
            created.append(ritual)
        if created:
            await self.session.flush()
        return created

    async def enqueue_due_rituals(self, user: User, now: datetime) -> int:
        """Check all enabled rituals for this user and enqueue any that are due."""
        result = await self.session.execute(
            select(Ritual).where(Ritual.user_id == user.id, Ritual.enabled.is_(True))
        )
        rituals = list(result.scalars())

        try:
            tz = ZoneInfo(user.timezone)
        except (ZoneInfoNotFoundError, Exception):
            tz = ZoneInfo("UTC")

        local_now = now.astimezone(tz)
        enqueued = 0

        for ritual in rituals:
            if not self._is_due(ritual, local_now):
                continue

            idempotency_key = f"ritual:{ritual.id}:{local_now.strftime('%Y-%m-%dT%H')}"
            existing = await self.session.execute(
                select(HeartbeatJob).where(
                    HeartbeatJob.idempotency_key == idempotency_key
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue

            job = HeartbeatJob(
                id=new_id(),
                user_id=user.id,
                ritual_id=ritual.id,
                kind="ritual",
                due_at=now,
                channel=user.notification_channel or "telegram",
                payload={
                    "ritual_kind": ritual.kind,
                    "title": ritual.title,
                    "prompt_template": ritual.prompt_template,
                },
                idempotency_key=idempotency_key,
            )
            self.session.add(job)
            ritual.last_fired_at = now
            enqueued += 1

        if enqueued:
            await self.session.flush()
        return enqueued

    async def enqueue_due_for_all_users(self, now: datetime) -> int:
        """Sweep all users and enqueue due ritual jobs. Called by the worker sweep."""
        result = await self.session.execute(
            select(User).where(User.proactive_enabled.is_(True))
        )
        users = list(result.scalars())
        total = 0
        for user in users:
            try:
                total += await self.enqueue_due_rituals(user, now)
            except Exception as exc:
                logger.warning("Failed to enqueue rituals for %s: %s", user.id, exc)
        return total

    @staticmethod
    def _is_due(ritual: Ritual, local_now: datetime) -> bool:
        """Check if the ritual cron schedule fired in the current hour window."""
        try:
            # croniter checks if the schedule ran in the last 60 minutes
            cron = croniter(ritual.schedule_cron, local_now)
            prev_fire = cron.get_prev(datetime)
            minutes_since = (local_now - prev_fire).total_seconds() / 60
            # Consider due if the last fire was within the last 60 minutes
            if minutes_since > 60:
                return False
            # Avoid re-firing if we already fired recently
            if ritual.last_fired_at is not None:
                last_local = ritual.last_fired_at.astimezone(local_now.tzinfo)
                if (local_now - last_local).total_seconds() < 3600:
                    return False
            return True
        except Exception:
            return False
