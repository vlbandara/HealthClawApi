from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.core.config import Settings
from healthclaw.core.crypto import (
    CryptoError,
    decrypt_secret,
    encrypt_secret,
    generate_url_token,
)
from healthclaw.db.models import Account, utc_now

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TELEGRAM_TOKEN_REGEX = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")


class AccountError(Exception):
    pass


class InvalidEmailError(AccountError):
    pass


class InvalidBotTokenError(AccountError):
    pass


@dataclass(frozen=True)
class BotIdentity:
    telegram_id: str
    username: str | None
    first_name: str | None


class AccountService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def get_by_id(self, account_id: str) -> Account | None:
        return await self.session.get(Account, account_id)

    async def get_by_email(self, email: str) -> Account | None:
        normalized = self._normalize_email(email)
        result = await self.session.execute(
            select(Account).where(Account.email == normalized)
        )
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: str) -> Account | None:
        result = await self.session.execute(
            select(Account).where(Account.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_bot_token_for_user(self, user_id: str) -> str | None:
        account = await self.get_by_user_id(user_id)
        if account is None or account.paused_at is not None:
            return None
        return self.decrypt_bot_token(account)

    async def get_or_create_by_email(self, email: str) -> Account:
        normalized = self._normalize_email(email)
        existing = await self.get_by_email(normalized)
        if existing is not None:
            return existing
        account = Account(email=normalized, plan="free")
        self.session.add(account)
        await self.session.flush()
        return account

    async def bind_bot_token(self, account: Account, raw_token: str) -> BotIdentity:
        token = raw_token.strip()
        if not TELEGRAM_TOKEN_REGEX.match(token):
            raise InvalidBotTokenError("Telegram bot tokens look like '12345:ABCDE...'")
        identity = await self._fetch_bot_identity(token)
        account.bot_token_ciphertext = encrypt_secret(token, self.settings)
        account.bot_username = identity.username
        account.bot_telegram_id = identity.telegram_id
        if not account.webhook_secret:
            account.webhook_secret = generate_url_token(24)
        await self._register_webhook(account, token)
        return identity

    def decrypt_bot_token(self, account: Account) -> str | None:
        if not account.bot_token_ciphertext:
            return None
        try:
            return decrypt_secret(account.bot_token_ciphertext, self.settings)
        except CryptoError:
            logger.exception("Failed to decrypt bot token for account %s", account.id)
            return None

    async def pause(self, account: Account) -> None:
        account.paused_at = utc_now()

    async def resume(self, account: Account) -> None:
        account.paused_at = None

    async def increment_message_usage(self, account: Account) -> None:
        period = utc_now().strftime("%Y-%m")
        if account.monthly_message_period_start != period:
            account.monthly_message_period_start = period
            account.monthly_message_count = 0
        account.monthly_message_count += 1

    def is_over_free_tier(self, account: Account) -> bool:
        period = utc_now().strftime("%Y-%m")
        if account.monthly_message_period_start != period:
            return False
        return account.monthly_message_count >= self.settings.free_tier_monthly_messages

    async def unbind_bot_token(self, account: Account) -> None:
        token = self.decrypt_bot_token(account)
        if token is not None:
            await self._delete_webhook(token)
        account.bot_token_ciphertext = None
        account.bot_username = None
        account.bot_telegram_id = None
        account.webhook_secret = None

    async def mark_email_verified(self, account: Account, *, when: datetime | None = None) -> None:
        if account.email_verified_at is None:
            account.email_verified_at = when or utc_now()

    @staticmethod
    def _normalize_email(email: str) -> str:
        candidate = (email or "").strip().lower()
        if not EMAIL_REGEX.match(candidate):
            raise InvalidEmailError("Enter a valid email address")
        return candidate

    async def _fetch_bot_identity(self, token: str) -> BotIdentity:
        url = f"https://api.telegram.org/bot{token}/getMe"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise InvalidBotTokenError(f"Could not reach Telegram: {exc}") from exc
        payload: dict
        try:
            payload = response.json()
        except ValueError as exc:
            raise InvalidBotTokenError("Telegram returned an invalid response") from exc
        if not payload.get("ok"):
            description = payload.get("description") or "rejected"
            raise InvalidBotTokenError(f"Telegram rejected the token: {description}")
        result = payload.get("result") or {}
        return BotIdentity(
            telegram_id=str(result.get("id") or ""),
            username=result.get("username"),
            first_name=result.get("first_name"),
        )

    async def _register_webhook(self, account: Account, token: str) -> None:
        if not self.settings.public_base_url:
            logger.warning(
                "PUBLIC_BASE_URL not configured; skipping webhook registration for %s",
                account.id,
            )
            return
        webhook_url = f"{self.settings.public_base_url.rstrip('/')}/webhooks/telegram/{account.id}"
        url = f"https://api.telegram.org/bot{token}/setWebhook"
        params = {
            "url": webhook_url,
            "secret_token": account.webhook_secret,
            "drop_pending_updates": "true",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=params)
        except httpx.HTTPError as exc:
            raise InvalidBotTokenError(f"Could not register webhook: {exc}") from exc
        body = response.json() if response.content else {}
        if not body.get("ok"):
            raise InvalidBotTokenError(
                f"Telegram refused setWebhook: {body.get('description') or response.text}"
            )

    async def _delete_webhook(self, token: str) -> None:
        url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(url, json={"drop_pending_updates": "false"})
        except httpx.HTTPError:
            logger.warning("Could not deleteWebhook on Telegram; proceeding anyway")
