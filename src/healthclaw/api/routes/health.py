from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/")
async def root() -> dict[str, str]:
    return {"message": "Healthclaw API is running"}


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
