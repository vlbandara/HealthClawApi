from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.db.models import User
from healthclaw.sensing.bus import Signal, SignalBus

logger = logging.getLogger(__name__)


async def poll_weather_for_user(user: User, session: AsyncSession) -> bool:
    """Fetch and publish a weather signal for the user's primary location.
    Returns True if a new (non-dedup) signal was published."""
    if user.home_lat is None or user.home_lon is None:
        return False

    from healthclaw.integrations.weather import get_weather_provider

    provider = get_weather_provider()
    snapshot = await provider.get_current(user.home_lat, user.home_lon)
    if snapshot is None:
        return False

    # dedup_key includes temperature bucket to avoid re-ticking on identical conditions
    temp_bucket = round(snapshot.temp_c / 2) * 2  # 2°C granularity
    now = datetime.now(UTC)
    dedup_key = f"weather:{user.id}:{now.strftime('%Y-%m-%dT%H')}:t{temp_bucket}:h{snapshot.humidity_pct // 10}"

    signal = Signal(
        kind="weather",
        value=snapshot.to_dict(),
        source="open_meteo",
        observed_at=now,
        dedup_key=dedup_key,
    )
    bus = SignalBus(session)
    _, is_new = await bus.publish(user.id, signal)
    return is_new


async def poll_calendar_for_user(user: User, session: AsyncSession) -> bool:
    """Fetch and publish calendar signals for upcoming events.
    Returns True if at least one new signal was published."""
    from healthclaw.integrations.calendar import calendar_provider_for_user

    provider = await calendar_provider_for_user(user.id, session)
    now = datetime.now(UTC)
    events = await provider.list_upcoming(user.id, window_hours=12, now=now)
    bus = SignalBus(session)
    published = 0
    for event in events:
        dedup_key = f"calendar:{user.id}:{event.start_at.isoformat()}:{event.title[:32]}"
        signal = Signal(
            kind="calendar_event",
            value=event.to_dict(),
            source="calendar",
            observed_at=now,
            dedup_key=dedup_key,
        )
        _, is_new = await bus.publish(user.id, signal)
        if is_new:
            published += 1
    return published > 0


async def poll_wearables_for_user(user: User, session: AsyncSession) -> bool:
    """Fetch and publish wearable recovery/sleep signals.
    Returns True if at least one new signal was published."""
    from healthclaw.integrations.wearables import get_wearable_provider

    provider = get_wearable_provider(user.id)
    now = datetime.now(UTC)
    recovery = await provider.get_latest_recovery(user.id)
    if not recovery.get("available", False):
        return False

    recovery_score = recovery.get("recovery_score")
    sleep_hours = recovery.get("sleep_hours")
    today_key = now.strftime("%Y-%m-%d")
    bus = SignalBus(session)
    published = 0

    if recovery_score is not None:
        signal = Signal(
            kind="wearable_recovery",
            value=recovery,
            source="wearable",
            observed_at=now,
            dedup_key=f"wearable_recovery:{user.id}:{today_key}",
        )
        _, is_new = await bus.publish(user.id, signal)
        if is_new:
            published += 1

    if sleep_hours is not None:
        signal = Signal(
            kind="wearable_sleep",
            value={"sleep_hours": sleep_hours, **recovery},
            source="wearable",
            observed_at=now,
            dedup_key=f"wearable_sleep:{user.id}:{today_key}",
        )
        _, is_new = await bus.publish(user.id, signal)
        if is_new:
            published += 1

    return published > 0


async def run_sensing_poll(session: AsyncSession) -> dict[str, int]:
    """Poll all enabled signals for all users with proactive mode on and a location set."""
    from sqlalchemy import select

    result = await session.execute(
        select(User).where(
            User.proactive_enabled.is_(True),
        )
    )
    users = list(result.scalars())

    weather_new = 0
    calendar_new = 0
    wearable_new = 0

    for user in users:
        try:
            if await poll_weather_for_user(user, session):
                weather_new += 1
        except Exception as exc:
            logger.warning("Weather poll failed for user %s: %s", user.id, exc)

        try:
            if await poll_calendar_for_user(user, session):
                calendar_new += 1
        except Exception as exc:
            logger.warning("Calendar poll failed for user %s: %s", user.id, exc)

        try:
            if await poll_wearables_for_user(user, session):
                wearable_new += 1
        except Exception as exc:
            logger.warning("Wearable poll failed for user %s: %s", user.id, exc)

    await session.commit()
    return {
        "users_polled": len(users),
        "weather_new": weather_new,
        "calendar_new": calendar_new,
        "wearable_new": wearable_new,
    }
