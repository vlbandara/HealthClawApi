from __future__ import annotations

from healthclaw.core.config import get_settings
from healthclaw.services.account import BotIdentity, WebhookRegistrationError


async def _sign_in(client, monkeypatch) -> str:
    captured: list[str] = []

    async def fake_send(self, email: str, token: str) -> None:
        captured.append(token)

    monkeypatch.setattr(
        "healthclaw.services.auth.AuthService._send_magic_link_email", fake_send
    )
    response = await client.post("/v1/auth/magic-link", json={"email": "owner@example.com"})
    assert response.status_code == 200
    assert captured
    session = await client.post("/v1/auth/session", json={"token": captured[0]})
    assert session.status_code == 200
    return session.json()["access_token"]


async def _stub_bot_identity(monkeypatch, *, telegram_id: str = "987", username: str = "demo_bot"):
    async def fake_fetch(self, token: str) -> BotIdentity:
        return BotIdentity(telegram_id=telegram_id, username=username, first_name="Demo")

    async def fake_register(self, account, token: str) -> None:
        return None

    monkeypatch.setattr(
        "healthclaw.services.account.AccountService._fetch_bot_identity", fake_fetch
    )
    monkeypatch.setattr(
        "healthclaw.services.account.AccountService._register_webhook", fake_register
    )


async def test_landing_page_renders(client) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "Healthclaw" in response.text
    assert "Send sign-in link" in response.text
    assert "/v1/auth/magic-link" in response.text


async def test_auth_callback_page_handles_session_exchange(client) -> None:
    response = await client.get("/auth/callback?token=abc")
    assert response.status_code == 200
    assert "/v1/auth/session" in response.text
    assert "healthclaw_owner_session" in response.text
    assert "/onboarding" in response.text


async def test_onboarding_page_redirects_unauthenticated_browser_to_sign_in(client) -> None:
    response = await client.get("/onboarding")
    assert response.status_code == 200
    assert "Connect your Telegram bot" in response.text
    assert 'window.location.replace("/#signin")' in response.text
    assert "if (response.status === 401)" in response.text
    assert "resetSessionAndRedirectToSignIn()" in response.text


async def test_bind_bot_token_api_rejects_second_bot(client, monkeypatch) -> None:
    await _stub_bot_identity(monkeypatch)
    token = await _sign_in(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    first = await client.post(
        "/v1/me/bot-token",
        json={"bot_token": "1234567:ABCDEFGHIJKLMNOPQRSTUVWXY"},
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["bot_url"] == "https://t.me/demo_bot"

    second = await client.post(
        "/v1/me/bot-token",
        json={"bot_token": "1234567:ZYXWVUTSRQPONMLKJIHGFEDCB"},
        headers=headers,
    )
    assert second.status_code == 409
    assert "already has a Telegram bot" in second.json()["detail"]


async def test_bind_bot_token_api_rejects_claimed_bot(client, monkeypatch) -> None:
    await _stub_bot_identity(monkeypatch, telegram_id="claimed-bot", username="claimed_bot")
    first_token = await _sign_in(client, monkeypatch)

    first = await client.post(
        "/v1/me/bot-token",
        json={"bot_token": "1234567:ABCDEFGHIJKLMNOPQRSTUVWXY"},
        headers={"Authorization": f"Bearer {first_token}"},
    )
    assert first.status_code == 200

    captured: list[str] = []

    async def fake_send(self, email: str, token: str) -> None:
        captured.append(token)

    monkeypatch.setattr(
        "healthclaw.services.auth.AuthService._send_magic_link_email", fake_send
    )
    await client.post("/v1/auth/magic-link", json={"email": "second@example.com"})
    session = await client.post("/v1/auth/session", json={"token": captured[-1]})
    second_token = session.json()["access_token"]

    second = await client.post(
        "/v1/me/bot-token",
        json={"bot_token": "7654321:ABCDEFGHIJKLMNOPQRSTUVWXY"},
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert second.status_code == 409
    assert "already connected to another account" in second.json()["detail"]


async def test_bind_bot_token_api_rejects_missing_public_base_url(client, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("PUBLIC_BASE_URL", "")
    token = await _sign_in(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    async def fake_fetch(self, token: str) -> BotIdentity:
        return BotIdentity(telegram_id="987", username="demo_bot", first_name="Demo")

    monkeypatch.setattr(
        "healthclaw.services.account.AccountService._fetch_bot_identity", fake_fetch
    )

    response = await client.post(
        "/v1/me/bot-token",
        json={"bot_token": "1234567:ABCDEFGHIJKLMNOPQRSTUVWXY"},
        headers=headers,
    )

    assert response.status_code == 422
    assert "PUBLIC_BASE_URL" in response.json()["detail"]
    get_settings.cache_clear()


async def test_bind_bot_token_api_rejects_local_public_base_url(client, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    token = await _sign_in(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    async def fake_fetch(self, token: str) -> BotIdentity:
        return BotIdentity(telegram_id="987", username="demo_bot", first_name="Demo")

    monkeypatch.setattr(
        "healthclaw.services.account.AccountService._fetch_bot_identity", fake_fetch
    )

    response = await client.post(
        "/v1/me/bot-token",
        json={"bot_token": "1234567:ABCDEFGHIJKLMNOPQRSTUVWXY"},
        headers=headers,
    )

    assert response.status_code == 422
    assert "PUBLIC_BASE_URL" in response.json()["detail"]
    get_settings.cache_clear()


async def test_bind_bot_token_api_surfaces_telegram_webhook_refusal(client, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://healthclaw.example")
    token = await _sign_in(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    async def fake_fetch(self, token: str) -> BotIdentity:
        return BotIdentity(telegram_id="987", username="demo_bot", first_name="Demo")

    async def fake_register(self, account, token: str) -> None:
        raise WebhookRegistrationError("Telegram refused setWebhook: Unauthorized")

    monkeypatch.setattr(
        "healthclaw.services.account.AccountService._fetch_bot_identity", fake_fetch
    )
    monkeypatch.setattr(
        "healthclaw.services.account.AccountService._register_webhook",
        fake_register,
    )

    response = await client.post(
        "/v1/me/bot-token",
        json={"bot_token": "1234567:ABCDEFGHIJKLMNOPQRSTUVWXY"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "Unauthorized" in response.json()["detail"]
    get_settings.cache_clear()


async def test_bind_bot_token_api_surfaces_telegram_reachability_error(client, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://healthclaw.example")
    token = await _sign_in(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    async def fake_fetch(self, token: str) -> BotIdentity:
        return BotIdentity(telegram_id="987", username="demo_bot", first_name="Demo")

    monkeypatch.setattr(
        "healthclaw.services.account.AccountService._fetch_bot_identity", fake_fetch
    )

    async def fake_register(self, account, token: str) -> None:
        raise WebhookRegistrationError("Could not reach Telegram: ConnectError")

    monkeypatch.setattr(
        "healthclaw.services.account.AccountService._register_webhook", fake_register
    )

    response = await client.post(
        "/v1/me/bot-token",
        json={"bot_token": "1234567:ABCDEFGHIJKLMNOPQRSTUVWXY"},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Could not reach Telegram: ConnectError"
    get_settings.cache_clear()
