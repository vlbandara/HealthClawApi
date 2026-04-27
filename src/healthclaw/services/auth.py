from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.core.config import Settings
from healthclaw.core.crypto import generate_url_token, hash_token
from healthclaw.db.models import Account, AuthMagicLink, utc_now
from healthclaw.services.account import AccountService

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
SESSION_AUDIENCE = "healthclaw.session"


class AuthError(Exception):
    pass


class AuthConfigError(AuthError):
    pass


class InvalidMagicLinkError(AuthError):
    pass


class InvalidSessionError(AuthError):
    pass


@dataclass(frozen=True)
class IssuedSession:
    token: str
    account: Account
    expires_at: datetime


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.accounts = AccountService(session, settings)

    async def request_magic_link(self, email: str) -> tuple[Account, str]:
        if not self.settings.jwt_secret:
            raise AuthConfigError("JWT_SECRET is not configured")
        account = await self.accounts.get_or_create_by_email(email)
        raw_token = generate_url_token(24)
        ttl = timedelta(minutes=self.settings.magic_link_ttl_minutes)
        expires_at = utc_now() + ttl
        link = AuthMagicLink(
            account_id=account.id,
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
        )
        self.session.add(link)
        await self.session.flush()
        await self._send_magic_link_email(account.email, raw_token)
        return account, raw_token

    async def consume_magic_link(self, raw_token: str) -> IssuedSession:
        if not self.settings.jwt_secret:
            raise AuthConfigError("JWT_SECRET is not configured")
        token_hash = hash_token(raw_token)
        result = await self.session.execute(
            select(AuthMagicLink).where(AuthMagicLink.token_hash == token_hash)
        )
        link = result.scalar_one_or_none()
        if link is None:
            raise InvalidMagicLinkError("Magic link is invalid")
        if link.consumed_at is not None:
            raise InvalidMagicLinkError("Magic link already used")
        expires_at = link.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < utc_now():
            raise InvalidMagicLinkError("Magic link expired")
        account = await self.session.get(Account, link.account_id)
        if account is None:
            raise InvalidMagicLinkError("Account not found")
        link.consumed_at = utc_now()
        await self.accounts.mark_email_verified(account)
        return self._issue_session(account)

    def verify_session_token(self, token: str) -> str:
        if not self.settings.jwt_secret:
            raise AuthConfigError("JWT_SECRET is not configured")
        try:
            payload = jwt.decode(
                token,
                self.settings.jwt_secret,
                algorithms=[JWT_ALGORITHM],
                audience=SESSION_AUDIENCE,
            )
        except jwt.PyJWTError as exc:
            raise InvalidSessionError(str(exc)) from exc
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise InvalidSessionError("Session subject missing")
        return sub

    def _issue_session(self, account: Account) -> IssuedSession:
        now = utc_now()
        expires_at = now + timedelta(days=self.settings.session_ttl_days)
        payload = {
            "sub": account.id,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "aud": SESSION_AUDIENCE,
            "email": account.email,
        }
        token = jwt.encode(payload, self.settings.jwt_secret, algorithm=JWT_ALGORITHM)
        return IssuedSession(token=token, account=account, expires_at=expires_at)

    async def _send_magic_link_email(self, email: str, raw_token: str) -> None:
        callback = self.settings.magic_link_callback_url
        link = f"{callback}?token={raw_token}"
        if not self.settings.resend_api_key:
            logger.info("Magic link for %s (no mailer configured): %s", email, link)
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {self.settings.resend_api_key}"},
                    json={
                        "from": self.settings.magic_link_from_email,
                        "to": [email],
                        "subject": "Your sign-in link",
                        "html": (
                            "<p>Sign in to your companion. This link expires in "
                            f"{self.settings.magic_link_ttl_minutes} minutes.</p>"
                            f'<p><a href="{link}">Sign in</a></p>'
                        ),
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Failed to deliver magic link email to %s", email)
