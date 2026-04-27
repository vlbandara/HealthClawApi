from __future__ import annotations

from fastapi import APIRouter, Header

from healthclaw.channels.telegram import TelegramAdapter
from healthclaw.core.config import get_settings
from healthclaw.db.session import SessionLocal
from healthclaw.services.account import AccountService
from healthclaw.services.conversation import ConversationService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/telegram/{account_id}")
async def telegram_webhook_for_account(
    account_id: str,
    update: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool | str]:
    settings = get_settings()
    async with SessionLocal() as session:
        accounts = AccountService(session, settings)
        account = await accounts.get_by_id(account_id)
        if account is None:
            return {"ok": False, "reason": "unknown_account"}
        if (
            account.webhook_secret
            and x_telegram_bot_api_secret_token != account.webhook_secret
        ):
            return {"ok": False, "reason": "invalid_secret"}
        if account.paused_at is not None:
            return {"ok": True, "reason": "paused"}
        bot_token = accounts.decrypt_bot_token(account)
        if bot_token is None:
            return {"ok": False, "reason": "bot_token_missing"}
        adapter = TelegramAdapter(settings)
        event = await adapter.event_from_update(update, bot_token=bot_token)
        if event is None:
            return {"ok": True, "reason": "ignored"}
        if account.user_id is not None and account.user_id != event.user_id:
            return {"ok": True, "reason": "account_user_mismatch"}
        if accounts.is_over_free_tier(account):
            await adapter.send_message(
                event.external_user_id,
                (
                    "You have reached the free monthly message limit. "
                    "It resets at the start of next month."
                ),
                bot_token=bot_token,
            )
            return {"ok": True, "reason": "over_free_tier"}
        response = await ConversationService(session).handle_event(
            event, account_id=account.id
        )
        if event.external_user_id and not response.idempotent_replay:
            await adapter.send_message(
                event.external_user_id, response.response, bot_token=bot_token
            )
        return {"ok": True}


@router.post("/telegram")
async def telegram_webhook_legacy(
    update: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool | str]:
    settings = get_settings()
    if settings.multi_tenant_mode:
        return {"ok": False, "reason": "multi_tenant_requires_account_path"}
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
