from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.db.models import Ritual, User

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RitualStreakService:
    session: AsyncSession

    async def record_meaningful_exchange(
        self,
        user: User,
        user_turn_at: datetime,
        safety_category: str,
    ) -> list[Ritual]:
        if safety_category == "crisis":
            return []

        user_turn_at = _ensure_utc(user_turn_at)
        tz = _safe_zoneinfo(user.timezone)
        local_turn = user_turn_at.astimezone(tz)
        today_local = local_turn.date().isoformat()

        enabled_result = await self.session.execute(
            select(Ritual).where(Ritual.user_id == user.id, Ritual.enabled.is_(True))
        )
        enabled_rituals = list(enabled_result.scalars())
        if not enabled_rituals:
            return []

        fired_result = await self.session.execute(
            select(Ritual).where(Ritual.user_id == user.id, Ritual.last_fired_at.is_not(None))
        )
        fired_rituals = list(fired_result.scalars())

        advanced: list[Ritual] = []
        for ritual in enabled_rituals:
            if ritual.last_fired_at is None:
                continue
            fired_at = _ensure_utc(ritual.last_fired_at)
            if user_turn_at < fired_at:
                continue
            if local_turn - fired_at.astimezone(tz) > timedelta(hours=12):
                continue
            if _newer_other_kind_fired_between(
                fired_rituals, ritual_kind=ritual.kind, start=fired_at, end=user_turn_at
            ):
                continue

            next_count = _next_streak_count(
                streak_count=int(ritual.streak_count or 0),
                streak_last_date=ritual.streak_last_date,
                today_local=today_local,
            )
            if next_count is None:
                continue

            stmt = (
                update(Ritual)
                .where(
                    Ritual.id == ritual.id,
                    Ritual.streak_last_date.is_distinct_from(today_local),
                )
                .values(streak_count=next_count, streak_last_date=today_local)
            )
            result = await self.session.execute(stmt)
            if int(result.rowcount or 0) != 1:
                continue

            ritual.streak_count = next_count
            ritual.streak_last_date = today_local
            advanced.append(ritual)

        if advanced:
            await self.session.flush()
            logger.info(
                "Advanced ritual streaks for user %s: %s",
                user.id,
                [
                    {
                        "kind": ritual.kind,
                        "streak_count": int(ritual.streak_count or 0),
                        "streak_last_date": ritual.streak_last_date,
                    }
                    for ritual in advanced
                ],
            )
        return advanced

    async def streaks_payload(self, user_id: str) -> list[dict]:
        result = await self.session.execute(
            select(Ritual)
            .where(
                Ritual.user_id == user_id,
                Ritual.enabled.is_(True),
                Ritual.streak_count > 0,
            )
            .order_by(Ritual.streak_count.desc(), Ritual.created_at.asc())
        )
        rituals = list(result.scalars())
        return [
            {
                "kind": ritual.kind,
                "title": ritual.title,
                "streak_count": int(ritual.streak_count or 0),
                "streak_last_date": ritual.streak_last_date,
            }
            for ritual in rituals
            if int(ritual.streak_count or 0) > 0
        ]


def _safe_zoneinfo(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, Exception):
        return ZoneInfo("UTC")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _next_streak_count(
    *,
    streak_count: int,
    streak_last_date: str | None,
    today_local: str,
) -> int | None:
    last = _parse_iso_date(streak_last_date)
    today = date.fromisoformat(today_local)
    if last is None:
        return 1
    gap = (today - last).days
    if gap <= 0:
        return None
    if gap == 1:
        return max(1, streak_count) + 1
    return 1


def _newer_other_kind_fired_between(
    rituals: list[Ritual],
    *,
    ritual_kind: str,
    start: datetime,
    end: datetime,
) -> bool:
    for other in rituals:
        if other.kind == ritual_kind or other.last_fired_at is None:
            continue
        fired = _ensure_utc(other.last_fired_at)
        if start < fired <= end:
            return True
    return False
