from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalendarEvent:
    title: str
    start_at: datetime
    end_at: datetime | None
    location: str | None
    is_outdoor_hint: bool

    def minutes_until(self, now: datetime) -> int:
        delta = self.start_at - now
        return max(0, int(delta.total_seconds() / 60))

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start_at": self.start_at.isoformat(),
            "end_at": self.end_at.isoformat() if self.end_at else None,
            "location": self.location,
            "is_outdoor_hint": self.is_outdoor_hint,
        }


_OUTDOOR_KEYWORDS = {
    "walk", "run", "jog", "hike", "cycle", "bike", "swim", "outdoor", "outside",
    "park", "beach", "field", "garden", "lunch", "picnic", "commute",
}


def _is_outdoor_hint(title: str) -> bool:
    lower = title.lower()
    return any(kw in lower for kw in _OUTDOOR_KEYWORDS)


class CalendarProvider(ABC):
    @abstractmethod
    async def list_upcoming(
        self, user_id: str, *, window_hours: int = 12, now: datetime | None = None
    ) -> list[CalendarEvent]:
        raise NotImplementedError


class NullCalendarProvider(CalendarProvider):
    async def list_upcoming(
        self, user_id: str, *, window_hours: int = 12, now: datetime | None = None
    ) -> list[CalendarEvent]:
        return []


class IcalCalendarProvider(CalendarProvider):
    """Reads a publicly accessible iCal URL stored per user in integration_credentials."""

    def __init__(self, ical_url: str) -> None:
        self._url = ical_url

    async def list_upcoming(
        self, user_id: str, *, window_hours: int = 12, now: datetime | None = None
    ) -> list[CalendarEvent]:
        import httpx

        try:
            import icalendar
        except ImportError:
            logger.warning("icalendar not installed; IcalCalendarProvider unavailable")
            return []

        base = now or datetime.now(UTC)
        end = base + timedelta(hours=window_hours)

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
                cal = icalendar.Calendar.from_ical(resp.content)
        except Exception as exc:
            logger.warning("iCal fetch failed: %s", exc)
            return []

        events: list[CalendarEvent] = []
        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            dtstart = component.get("DTSTART")
            dtend = component.get("DTEND")
            if dtstart is None:
                continue
            start = _to_utc(dtstart.dt)
            end_dt = _to_utc(dtend.dt) if dtend else None
            if start < base or start > end:
                continue
            title = str(component.get("SUMMARY", ""))
            location = str(component.get("LOCATION", "")) or None
            events.append(
                CalendarEvent(
                    title=title,
                    start_at=start,
                    end_at=end_dt,
                    location=location,
                    is_outdoor_hint=_is_outdoor_hint(title),
                )
            )
        return sorted(events, key=lambda e: e.start_at)


class GoogleCalendarProvider(CalendarProvider):
    """Google Calendar via OAuth refresh token stored in integration_credentials."""

    def __init__(self, access_token: str) -> None:
        self._token = access_token

    async def list_upcoming(
        self, user_id: str, *, window_hours: int = 12, now: datetime | None = None
    ) -> list[CalendarEvent]:
        import httpx

        base = now or datetime.now(UTC)
        end = base + timedelta(hours=window_hours)

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                    headers={"Authorization": f"Bearer {self._token}"},
                    params={
                        "timeMin": base.isoformat(),
                        "timeMax": end.isoformat(),
                        "singleEvents": "true",
                        "orderBy": "startTime",
                        "maxResults": 20,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("Google Calendar fetch failed for user %s: %s", user_id, exc)
            return []

        events: list[CalendarEvent] = []
        for item in data.get("items", []):
            start_raw = item.get("start", {})
            end_raw = item.get("end", {})
            start_str = start_raw.get("dateTime") or start_raw.get("date")
            end_str = end_raw.get("dateTime") or end_raw.get("date")
            if not start_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=UTC)
                end_dt = None
                if end_str:
                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=UTC)
            except ValueError:
                continue
            title = str(item.get("summary", ""))
            location = item.get("location")
            events.append(
                CalendarEvent(
                    title=title,
                    start_at=start_dt,
                    end_at=end_dt,
                    location=location,
                    is_outdoor_hint=_is_outdoor_hint(title),
                )
            )
        return events


def _to_utc(dt: Any) -> datetime:
    from datetime import date

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day, tzinfo=UTC)
    return datetime.now(UTC)


async def calendar_provider_for_user(
    user_id: str, session: Any
) -> CalendarProvider:
    """Load the appropriate CalendarProvider from stored credentials."""
    from sqlalchemy import select

    from healthclaw.db.models import IntegrationCredential

    result = await session.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.user_id == user_id,
            IntegrationCredential.provider.in_(["google_calendar", "ical"]),
            IntegrationCredential.status == "active",
        )
    )
    cred = result.scalar_one_or_none()
    if cred is None:
        return NullCalendarProvider()

    if cred.provider == "ical":
        import json as _json

        payload = _json.loads(cred.encrypted_payload or "{}")
        url = payload.get("url", "")
        if url:
            return IcalCalendarProvider(url)
    return NullCalendarProvider()
