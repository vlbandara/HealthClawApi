from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from healthclaw.api.deps import SessionDep
from healthclaw.core.security import require_api_key
from healthclaw.db.models import IntegrationCredential, User, UserLocation

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/integrations",
    tags=["integrations"],
    dependencies=[Depends(require_api_key)],
)


# ── Location ──────────────────────────────────────────────────────────────


class LocationPayload(BaseModel):
    user_id: str
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    label: str = Field(default="home", max_length=64)
    set_as_primary: bool = True


@router.post("/location", status_code=status.HTTP_201_CREATED)
async def register_location(
    payload: LocationPayload,
    session: SessionDep,
) -> dict:
    user = await session.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")

    if payload.set_as_primary:
        result = await session.execute(
            select(UserLocation).where(
                UserLocation.user_id == payload.user_id,
                UserLocation.is_primary.is_(True),
            )
        )
        for loc in result.scalars():
            loc.is_primary = False
        user.home_lat = payload.lat
        user.home_lon = payload.lon

    loc = UserLocation(
        user_id=payload.user_id,
        lat=payload.lat,
        lon=payload.lon,
        label=payload.label,
        source="manual",
        is_primary=payload.set_as_primary,
    )
    session.add(loc)
    await session.commit()
    return {"id": loc.id, "lat": loc.lat, "lon": loc.lon, "label": loc.label}


class LocationQuery(BaseModel):
    user_id: str


@router.get("/location")
async def list_locations(user_id: str, session: SessionDep) -> list[dict]:
    result = await session.execute(
        select(UserLocation).where(UserLocation.user_id == user_id)
    )
    return [
        {
            "id": loc.id,
            "lat": loc.lat,
            "lon": loc.lon,
            "label": loc.label,
            "is_primary": loc.is_primary,
        }
        for loc in result.scalars()
    ]


# ── iCal ──────────────────────────────────────────────────────────────────


class IcalPayload(BaseModel):
    user_id: str
    url: str = Field(..., max_length=1024)


@router.post("/ical/connect", status_code=status.HTTP_201_CREATED)
async def connect_ical(payload: IcalPayload, session: SessionDep) -> dict:
    result = await session.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.user_id == payload.user_id,
            IntegrationCredential.provider == "ical",
        ).limit(1)
    )
    cred = result.scalar_one_or_none()
    if cred is None:
        cred = IntegrationCredential(
            user_id=payload.user_id,
            provider="ical",
            scopes="calendar.read",
        )
        session.add(cred)

    cred.encrypted_payload = json.dumps({"url": payload.url})
    cred.status = "active"
    cred.updated_at = datetime.now(UTC)
    await session.commit()
    return {"provider": "ical", "status": "connected"}


# ── HealthKit push webhook ─────────────────────────────────────────────────


class HealthKitPushPayload(BaseModel):
    user_id: str
    sleep_hours: float | None = None
    recovery_score: float | None = None
    hrv_ms: float | None = None
    resting_hr: float | None = None


@router.post("/healthkit/push")
async def healthkit_push(payload: HealthKitPushPayload, session: SessionDep) -> dict:
    """Receive a HealthKit push from the iOS companion app and publish a wearable signal."""
    from healthclaw.sensing.bus import Signal, SignalBus

    now = datetime.now(UTC)
    today_key = now.strftime("%Y-%m-%d")
    value = payload.model_dump(exclude={"user_id"}, exclude_none=True)
    value["available"] = True

    bus = SignalBus(session)
    signal = Signal(
        kind="wearable_recovery",
        value=value,
        source="apple_health",
        observed_at=now,
        dedup_key=f"wearable_recovery:{payload.user_id}:{today_key}",
    )
    signal_id, is_new = await bus.publish(payload.user_id, signal)
    await session.commit()
    return {"signal_id": signal_id, "is_new": is_new}


# ── Credential status ──────────────────────────────────────────────────────


@router.get("/status")
async def integration_status(user_id: str, session: SessionDep) -> dict:
    result = await session.execute(
        select(IntegrationCredential).where(IntegrationCredential.user_id == user_id)
    )
    creds = list(result.scalars())
    user = await session.get(User, user_id)
    return {
        "has_location": user is not None and user.home_lat is not None,
        "providers": [
            {
                "provider": c.provider,
                "status": c.status,
                "expires_at": c.expires_at and c.expires_at.isoformat(),
            }
            for c in creds
        ],
    }
