from __future__ import annotations

from fastapi import APIRouter, Header

from healthclaw.channels.telegram import TelegramAdapter
from healthclaw.core.config import get_settings
from healthclaw.db.session import SessionLocal
from healthclaw.services.conversation import ConversationService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/telegram")
async def telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool | str]:
    settings = get_settings()
    if (
        settings.telegram_webhook_secret
        and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret
    ):
        return {"ok": False, "reason": "invalid_secret"}
    adapter = TelegramAdapter(settings)
    event = await adapter.event_from_update(update)
    if event is None:
        return {"ok": True, "reason": "ignored"}
    async with SessionLocal() as session:
        response = await ConversationService(session).handle_event(event)
    if event.external_user_id and not response.idempotent_replay:
        await adapter.send_message(event.external_user_id, response.response)
    return {"ok": True}


@router.post("/whatsapp")
async def whatsapp_reserved() -> dict[str, str]:
    return {"status": "reserved"}


@router.post("/slack")
async def slack_reserved() -> dict[str, str]:
    return {"status": "reserved"}


@router.post("/discord")
async def discord_reserved() -> dict[str, str]:
    return {"status": "reserved"}
