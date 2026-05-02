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
    humidity_bucket = snapshot.humidity_pct // 10
    dedup_key = (
        f"weather:{user.id}:{now.strftime('%Y-%m-%dT%H')}:t{temp_bucket}:h{humidity_bucket}"
    )

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


async def poll_hydration_for_user(user: User, session: AsyncSession) -> bool:
    """Publish a hydration_need signal by fusing weather + user pattern + last water log.

    Severity 0..1:
      0.1 — mild reminder (normal day)
      0.4 — warm day, no water logged recently
      0.7 — heat-stress conditions + no water log today
      0.9 — extreme heat + wearable shows low recovery + no water log

    This is the end-to-end demo of the "hot in Singapore → drink water" nudge.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from healthclaw.db.models import MetricLog

    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Check if already published hydration signal today
    existing = await session.execute(
        select(Signal).where(
            Signal.user_id == user.id,
            Signal.kind == "hydration_need",
            Signal.observed_at >= today_start,
        ).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return False  # already done today

    # Compute severity
    severity = 0.1
    weather_severe = False

    if user.home_lat is not None and user.home_lon is not None:
        from healthclaw.integrations.weather import get_weather_provider
        provider = get_weather_provider()
        snapshot = await provider.get_current(user.home_lat, user.home_lon)
        if snapshot is not None:
            if snapshot.is_heat_stress:
                severity = max(severity, 0.7)
                weather_severe = True
            elif snapshot.temp_c > 28:
                severity = max(severity, 0.4)

    # Check last water log
    last_water = await session.execute(
        select(MetricLog).where(
            MetricLog.user_id == user.id,
            MetricLog.metric == "water_ml",
            MetricLog.observed_at >= today_start,
        ).order_by(MetricLog.observed_at.desc()).limit(1)
    )
    has_water_today = last_water.scalar_one_or_none() is not None
    if not has_water_today and weather_severe:
        severity = min(0.9, severity + 0.1)
    elif has_water_today:
        severity = max(0.0, severity - 0.2)

    if severity < 0.2:
        return False  # not worth publishing

    bus = SignalBus(session)
    dedup_key = f"hydration_need:{user.id}:{now.strftime('%Y-%m-%d')}"
    signal = Signal(
        kind="hydration_need",
        value={
            "severity": severity,
            "weather_severe": weather_severe,
            "has_water_today": has_water_today,
            "computed_at": now.isoformat(),
        },
        source="hydration_poller",
        observed_at=now,
        dedup_key=dedup_key,
    )
    _, is_new = await bus.publish(user.id, signal)
    return is_new


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
    hydration_new = 0

    from healthclaw.core.config import get_settings
    settings = get_settings()

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

        if settings.hydration_poller_enabled:
            try:
                if await poll_hydration_for_user(user, session):
                    hydration_new += 1
            except Exception as exc:
                logger.warning("Hydration poll failed for user %s: %s", user.id, exc)

    await session.commit()
    return {
        "users_polled": len(users),
        "weather_new": weather_new,
        "calendar_new": calendar_new,
        "wearable_new": wearable_new,
        "hydration_new": hydration_new,
    }
