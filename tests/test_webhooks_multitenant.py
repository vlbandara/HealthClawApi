from __future__ import annotations

import pytest

from healthclaw.core.config import get_settings
from healthclaw.core.crypto import encrypt_secret
from healthclaw.db.models import Account
from healthclaw.db.session import SessionLocal


def _telegram_update(chat_id: int = 555, update_id: int = 1, text: str = "hi") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 99,
            "from": {"id": chat_id, "is_bot": False, "first_name": "Tester"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1700000000,
            "text": text,
        },
    }


@pytest.fixture
async def seeded_account():
    settings = get_settings()
    async with SessionLocal() as session:
        account = Account(
            email="webhook@example.com",
            bot_token_ciphertext=encrypt_secret("1234567:DUMMYTOKEN_FOR_TESTSx", settings),
            bot_username="webhook_bot",
            bot_telegram_id="42",
            webhook_secret="super-secret-token",
            plan="free",
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)
        account_id = account.id
    return account_id


async def _patch_send_and_handle(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def fake_send_message(self, external_id, text, **_kwargs):
        sent.append((external_id, text))
        return None

    async def fake_handle_event(self, event, timezone=None, *, account_id=None):
        from healthclaw.schemas.messages import MessageResponse
        from healthclaw.services.account import AccountService

        if account_id is not None:
            account = await self.session.get(Account, account_id)
            if account is not None:
                if account.user_id is None:
                    account.user_id = event.user_id
                await AccountService(self.session, get_settings()).increment_message_usage(account)
                await self.session.commit()
        return MessageResponse(
            trace_id="trace-test",
            idempotent_replay=False,
            user_message_id="user-msg",
            assistant_message_id="asst-msg",
            thread_id="thread-test",
            response="echo: " + event.content,
            safety_category="ok",
            time_context={},
            memory_updates=[],
        )

    monkeypatch.setattr(
        "healthclaw.channels.telegram.TelegramAdapter.send_message",
        fake_send_message,
    )
    monkeypatch.setattr(
        "healthclaw.services.conversation.ConversationService.handle_event",
        fake_handle_event,
    )
    return sent


async def test_webhook_rejects_invalid_secret(client, seeded_account, monkeypatch) -> None:
    await _patch_send_and_handle(monkeypatch)
    response = await client.post(
        f"/webhooks/telegram/{seeded_account}",
        json=_telegram_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": False, "reason": "invalid_secret"}


async def test_webhook_unknown_account(client, monkeypatch) -> None:
    await _patch_send_and_handle(monkeypatch)
    response = await client.post(
        "/webhooks/telegram/nonexistent",
        json=_telegram_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "any"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": False, "reason": "unknown_account"}


async def test_webhook_paused_account_short_circuits(client, seeded_account, monkeypatch) -> None:
    sent = await _patch_send_and_handle(monkeypatch)
    async with SessionLocal() as session:
        account = await session.get(Account, seeded_account)
        from healthclaw.db.models import utc_now
        account.paused_at = utc_now()
        await session.commit()
    response = await client.post(
        f"/webhooks/telegram/{seeded_account}",
        json=_telegram_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "reason": "paused"}
    assert sent == []


async def test_webhook_auto_binds_user_id(client, seeded_account, monkeypatch) -> None:
    sent = await _patch_send_and_handle(monkeypatch)
    response = await client.post(
        f"/webhooks/telegram/{seeded_account}",
        json=_telegram_update(chat_id=777),
        headers={"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert sent == [("777", "echo: hi")]
    async with SessionLocal() as session:
        account = await session.get(Account, seeded_account)
        assert account.user_id == "telegram:777"
        assert account.monthly_message_count == 1


async def test_webhook_rejects_foreign_chat_after_binding(
    client,
    seeded_account,
    monkeypatch,
) -> None:
    sent = await _patch_send_and_handle(monkeypatch)
    async with SessionLocal() as session:
        account = await session.get(Account, seeded_account)
        account.user_id = "telegram:111"
        await session.commit()
    response = await client.post(
        f"/webhooks/telegram/{seeded_account}",
        json=_telegram_update(chat_id=222, update_id=9),
        headers={"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "reason": "account_user_mismatch"}
    assert sent == []


async def test_webhook_free_tier_soft_cap(client, seeded_account, monkeypatch) -> None:
    sent = await _patch_send_and_handle(monkeypatch)
    settings = get_settings()
    from healthclaw.db.models import utc_now
    period = utc_now().strftime("%Y-%m")
    async with SessionLocal() as session:
        account = await session.get(Account, seeded_account)
        account.monthly_message_period_start = period
        account.monthly_message_count = settings.free_tier_monthly_messages
        await session.commit()
    response = await client.post(
        f"/webhooks/telegram/{seeded_account}",
        json=_telegram_update(chat_id=333, update_id=10),
        headers={"X-Telegram-Bot-Api-Secret-Token": "super-secret-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "reason": "over_free_tier"}
    assert len(sent) == 1
    assert sent[0][0] == "333"
    assert "free monthly message limit" in sent[0][1]
