from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from healthclaw.db.models import Ritual, User, new_id
from healthclaw.db.session import SessionLocal
from healthclaw.heartbeat.streaks import RitualStreakService


def _make_ritual(
    *,
    user_id: str,
    kind: str = "morning_check_in",
    enabled: bool = True,
    last_fired_at: datetime | None = None,
    streak_count: int = 0,
    streak_last_date: str | None = None,
) -> Ritual:
    return Ritual(
        id=new_id(),
        user_id=user_id,
        kind=kind,
        title="Morning check-in",
        schedule_cron="0 8 * * *",
        prompt_template="Good morning.",
        enabled=enabled,
        last_fired_at=last_fired_at,
        streak_count=streak_count,
        streak_last_date=streak_last_date,
    )


async def test_attribution_window_first_reply_only() -> None:
    now = datetime(2026, 4, 21, 10, 0, tzinfo=UTC)
    async with SessionLocal() as session:
        user = User(
            id="u-streak-window",
            timezone="UTC",
            quiet_start="23:00",
            quiet_end="07:00",
        )
        ritual = _make_ritual(user_id=user.id, last_fired_at=now - timedelta(hours=2))
        session.add_all([user, ritual])
        await session.commit()

        service = RitualStreakService(session)
        advanced1 = await service.record_meaningful_exchange(user, now, "wellness")
        advanced2 = await service.record_meaningful_exchange(
            user,
            now + timedelta(hours=1),
            "wellness",
        )

        refreshed = (
            await session.execute(select(Ritual).where(Ritual.id == ritual.id))
        ).scalar_one()

    assert len(advanced1) == 1
    assert len(advanced2) == 0
    assert refreshed.streak_count == 1
    assert refreshed.streak_last_date == "2026-04-21"


async def test_outside_window_no_credit() -> None:
    now = datetime(2026, 4, 21, 10, 0, tzinfo=UTC)
    async with SessionLocal() as session:
        user = User(
            id="u-streak-outside",
            timezone="UTC",
            quiet_start="23:00",
            quiet_end="07:00",
        )
        ritual = _make_ritual(user_id=user.id, last_fired_at=now - timedelta(hours=14))
        session.add_all([user, ritual])
        await session.commit()

        advanced = await RitualStreakService(session).record_meaningful_exchange(
            user,
            now,
            "wellness",
        )
        refreshed = (
            await session.execute(select(Ritual).where(Ritual.id == ritual.id))
        ).scalar_one()

    assert advanced == []
    assert refreshed.streak_count == 0
    assert refreshed.streak_last_date is None


async def test_crisis_skip_even_inside_window() -> None:
    now = datetime(2026, 4, 21, 10, 0, tzinfo=UTC)
    async with SessionLocal() as session:
        user = User(
            id="u-streak-crisis",
            timezone="UTC",
            quiet_start="23:00",
            quiet_end="07:00",
        )
        ritual = _make_ritual(user_id=user.id, last_fired_at=now - timedelta(hours=1))
        session.add_all([user, ritual])
        await session.commit()

        advanced = await RitualStreakService(session).record_meaningful_exchange(
            user,
            now,
            "crisis",
        )
        refreshed = (
            await session.execute(select(Ritual).where(Ritual.id == ritual.id))
        ).scalar_one()

    assert advanced == []
    assert refreshed.streak_count == 0


async def test_day_over_day_advance_and_gap_reset() -> None:
    now = datetime(2026, 4, 21, 10, 0, tzinfo=UTC)
    async with SessionLocal() as session:
        user = User(
            id="u-streak-gap",
            timezone="UTC",
            quiet_start="23:00",
            quiet_end="07:00",
        )
        ritual_advance = _make_ritual(
            user_id=user.id,
            last_fired_at=now - timedelta(hours=1),
            streak_count=3,
            streak_last_date="2026-04-20",
        )
        ritual_reset = _make_ritual(
            user_id=user.id,
            kind="morning_check_in_reset",
            last_fired_at=now - timedelta(hours=1),
            streak_count=5,
            streak_last_date="2026-04-18",
        )
        session.add_all([user, ritual_advance, ritual_reset])
        await session.commit()

        service = RitualStreakService(session)
        await service.record_meaningful_exchange(user, now, "wellness")

        refreshed_advance = (
            await session.execute(select(Ritual).where(Ritual.id == ritual_advance.id))
        ).scalar_one()
        refreshed_reset = (
            await session.execute(select(Ritual).where(Ritual.id == ritual_reset.id))
        ).scalar_one()

    assert refreshed_advance.streak_count == 4
    assert refreshed_advance.streak_last_date == "2026-04-21"
    assert refreshed_reset.streak_count == 1
    assert refreshed_reset.streak_last_date == "2026-04-21"


async def test_timezone_correctness_uses_user_local_date() -> None:
    turn_at = datetime(2026, 4, 21, 23, 30, tzinfo=UTC)
    async with SessionLocal() as session:
        utc_user = User(
            id="u-streak-tz-utc",
            timezone="UTC",
            quiet_start="23:00",
            quiet_end="07:00",
        )
        nz_user = User(
            id="u-streak-tz-nz",
            timezone="Pacific/Auckland",
            quiet_start="23:00",
            quiet_end="07:00",
        )
        utc_ritual = _make_ritual(
            user_id=utc_user.id,
            last_fired_at=turn_at - timedelta(hours=1),
            streak_count=3,
            streak_last_date="2026-04-21",
        )
        nz_ritual = _make_ritual(
            user_id=nz_user.id,
            last_fired_at=turn_at - timedelta(hours=1),
            streak_count=3,
            streak_last_date="2026-04-21",
        )
        session.add_all([utc_user, nz_user, utc_ritual, nz_ritual])
        await session.commit()

        await RitualStreakService(session).record_meaningful_exchange(
            utc_user,
            turn_at,
            "wellness",
        )
        await RitualStreakService(session).record_meaningful_exchange(
            nz_user,
            turn_at,
            "wellness",
        )

        utc_refreshed = (
            await session.execute(select(Ritual).where(Ritual.id == utc_ritual.id))
        ).scalar_one()
        nz_refreshed = (
            await session.execute(select(Ritual).where(Ritual.id == nz_ritual.id))
        ).scalar_one()

    assert utc_refreshed.streak_count == 3
    assert utc_refreshed.streak_last_date == "2026-04-21"
    assert nz_refreshed.streak_count == 4
    assert nz_refreshed.streak_last_date == "2026-04-22"


async def test_streaks_payload_filters_enabled_and_sorts_desc() -> None:
    async with SessionLocal() as session:
        user = User(id="u-streak-payload", timezone="UTC", quiet_start="23:00", quiet_end="07:00")
        r1 = _make_ritual(user_id=user.id, kind="a", streak_count=5, streak_last_date="2026-04-20")
        r2 = _make_ritual(user_id=user.id, kind="b", streak_count=2, streak_last_date="2026-04-20")
        r3 = _make_ritual(
            user_id=user.id, kind="c", enabled=False, streak_count=10, streak_last_date="2026-04-20"
        )
        r4 = _make_ritual(user_id=user.id, kind="d", streak_count=0, streak_last_date=None)
        session.add_all([user, r1, r2, r3, r4])
        await session.commit()

        payload = await RitualStreakService(session).streaks_payload(user.id)

    assert [p["kind"] for p in payload] == ["a", "b"]
    assert payload[0]["streak_count"] == 5
