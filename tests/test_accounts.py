from __future__ import annotations

import pytest

from healthclaw.core.config import get_settings
from healthclaw.core.crypto import decrypt_secret, encrypt_secret
from healthclaw.db.session import SessionLocal
from healthclaw.services.account import (
    AccountService,
    BotIdentity,
    InvalidBotTokenError,
    InvalidEmailError,
)
from healthclaw.services.auth import (
    AuthService,
    InvalidMagicLinkError,
)


async def _stub_bot_identity(monkeypatch, *, telegram_id: str = "987", username: str = "demo_bot"):
    async def fake_fetch(self, token: str) -> BotIdentity:
        return BotIdentity(telegram_id=telegram_id, username=username, first_name="Demo")

    async def fake_register(self, account, token: str) -> None:  # pragma: no cover - trivial
        return None

    monkeypatch.setattr(
        "healthclaw.services.account.AccountService._fetch_bot_identity", fake_fetch
    )
    monkeypatch.setattr(
        "healthclaw.services.account.AccountService._register_webhook", fake_register
    )


async def test_encrypt_round_trip() -> None:
    settings = get_settings()
    cipher = encrypt_secret("secret-bot-token", settings)
    assert cipher != "secret-bot-token"
    assert decrypt_secret(cipher, settings) == "secret-bot-token"


async def test_account_create_and_bind_bot_token(monkeypatch) -> None:
    await _stub_bot_identity(monkeypatch)
    settings = get_settings()
    async with SessionLocal() as session:
        accounts = AccountService(session, settings)
        account = await accounts.get_or_create_by_email("Owner@Example.com")
        assert account.email == "owner@example.com"
        identity = await accounts.bind_bot_token(account, "1234567:ABCDEFGHIJKLMNOPQRSTUVWXY")
        assert identity.username == "demo_bot"
        assert account.bot_username == "demo_bot"
        assert account.bot_telegram_id == "987"
        assert account.webhook_secret is not None
        assert account.bot_token_ciphertext is not None
        assert accounts.decrypt_bot_token(account) == "1234567:ABCDEFGHIJKLMNOPQRSTUVWXY"
        await session.commit()


async def test_account_rejects_bad_email() -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        accounts = AccountService(session, settings)
        with pytest.raises(InvalidEmailError):
            await accounts.get_or_create_by_email("not-an-email")


async def test_account_rejects_bad_bot_token_shape() -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        accounts = AccountService(session, settings)
        account = await accounts.get_or_create_by_email("a@b.co")
        with pytest.raises(InvalidBotTokenError):
            await accounts.bind_bot_token(account, "not-a-token")


async def test_free_tier_counter_resets_on_period_change() -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        accounts = AccountService(session, settings)
        account = await accounts.get_or_create_by_email("counter@example.com")
        account.monthly_message_period_start = "1999-01"
        account.monthly_message_count = 999
        await accounts.increment_message_usage(account)
        assert account.monthly_message_count == 1
        assert account.monthly_message_period_start != "1999-01"


async def test_magic_link_round_trip(monkeypatch) -> None:
    settings = get_settings()
    captured: list[str] = []

    async def fake_send(self, email: str, token: str) -> None:
        captured.append(token)

    monkeypatch.setattr(
        "healthclaw.services.auth.AuthService._send_magic_link_email", fake_send
    )
    async with SessionLocal() as session:
        auth = AuthService(session, settings)
        account, raw = await auth.request_magic_link("magic@example.com")
        assert captured == [raw]
        await session.commit()

    async with SessionLocal() as session:
        auth = AuthService(session, settings)
        issued = await auth.consume_magic_link(raw)
        assert issued.account.id == account.id
        await session.commit()
        # Replay should fail
        with pytest.raises(InvalidMagicLinkError):
            await auth.consume_magic_link(raw)


async def test_magic_link_rejects_unknown_token() -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        auth = AuthService(session, settings)
        with pytest.raises(InvalidMagicLinkError):
            await auth.consume_magic_link("nope-not-a-real-token")


async def test_session_token_verifies(monkeypatch) -> None:
    settings = get_settings()

    async def fake_send(self, email: str, token: str) -> None:
        return None

    monkeypatch.setattr(
        "healthclaw.services.auth.AuthService._send_magic_link_email", fake_send
    )
    async with SessionLocal() as session:
        auth = AuthService(session, settings)
        _, raw = await auth.request_magic_link("session@example.com")
        await session.commit()
    async with SessionLocal() as session:
        auth = AuthService(session, settings)
        issued = await auth.consume_magic_link(raw)
        await session.commit()
        account_id = auth.verify_session_token(issued.token)
        assert account_id == issued.account.id
