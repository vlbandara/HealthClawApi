from __future__ import annotations

from fastapi import APIRouter

from healthclaw.api.routes import (
    audit,
    conversations,
    health,
    owner,
    reminders,
    users,
    webhooks,
    websocket,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(conversations.router)
api_router.include_router(users.router)
api_router.include_router(reminders.router)
api_router.include_router(audit.router)
api_router.include_router(owner.router)
api_router.include_router(webhooks.router)
api_router.include_router(websocket.router)
