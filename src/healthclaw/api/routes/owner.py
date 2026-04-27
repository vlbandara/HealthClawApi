from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from healthclaw.core.config import get_settings
from healthclaw.core.security import require_account
from healthclaw.db.models import Account
from healthclaw.db.session import SessionLocal
from healthclaw.services.account import (
    AccountService,
    InvalidBotTokenError,
    InvalidEmailError,
)
from healthclaw.services.auth import (
    AuthConfigError,
    AuthService,
    InvalidMagicLinkError,
)

router = APIRouter(tags=["owner"])


class MagicLinkRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class MagicLinkResponse(BaseModel):
    ok: bool = True
    message: str = "If the email is valid, a sign-in link has been sent."


class MagicLinkConsumeRequest(BaseModel):
    token: str = Field(min_length=1)


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    account_id: str
    email: str


class AccountStateResponse(BaseModel):
    account_id: str
    email: str
    email_verified: bool
    plan: str
    paused: bool
    bot_username: str | None
    bot_telegram_id: str | None
    bot_url: str | None
    user_id: str | None
    monthly_message_count: int
    monthly_message_limit: int
    monthly_period: str | None


class BindBotTokenRequest(BaseModel):
    bot_token: str = Field(min_length=10, max_length=120)


class BindBotTokenResponse(BaseModel):
    bot_username: str | None
    bot_telegram_id: str | None
    bot_url: str | None


def _bot_url(username: str | None) -> str | None:
    return f"https://t.me/{username}" if username else None


def _account_to_state(account: Account, settings) -> AccountStateResponse:
    return AccountStateResponse(
        account_id=account.id,
        email=account.email,
        email_verified=account.email_verified_at is not None,
        plan=account.plan,
        paused=account.paused_at is not None,
        bot_username=account.bot_username,
        bot_telegram_id=account.bot_telegram_id,
        bot_url=_bot_url(account.bot_username),
        user_id=account.user_id,
        monthly_message_count=account.monthly_message_count,
        monthly_message_limit=settings.free_tier_monthly_messages,
        monthly_period=account.monthly_message_period_start,
    )


@router.post("/v1/auth/magic-link", response_model=MagicLinkResponse)
async def request_magic_link(payload: MagicLinkRequest) -> MagicLinkResponse:
    settings = get_settings()
    async with SessionLocal() as session:
        auth = AuthService(session, settings)
        try:
            await auth.request_magic_link(payload.email)
        except InvalidEmailError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except AuthConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        await session.commit()
    return MagicLinkResponse()


@router.post("/v1/auth/session", response_model=SessionResponse)
async def consume_magic_link(payload: MagicLinkConsumeRequest) -> SessionResponse:
    settings = get_settings()
    async with SessionLocal() as session:
        auth = AuthService(session, settings)
        try:
            issued = await auth.consume_magic_link(payload.token)
        except InvalidMagicLinkError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except AuthConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        await session.commit()
        return SessionResponse(
            access_token=issued.token,
            expires_at=issued.expires_at.isoformat(),
            account_id=issued.account.id,
            email=issued.account.email,
        )


@router.get("/v1/me", response_model=AccountStateResponse)
async def me(account: Account = Depends(require_account)) -> AccountStateResponse:
    settings = get_settings()
    async with SessionLocal() as session:
        fresh = await session.get(Account, account.id)
        if fresh is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Account not found"
            )
        return _account_to_state(fresh, settings)


@router.post("/v1/me/bot-token", response_model=BindBotTokenResponse)
async def bind_bot_token(
    payload: BindBotTokenRequest, account: Account = Depends(require_account)
) -> BindBotTokenResponse:
    settings = get_settings()
    async with SessionLocal() as session:
        fresh = await session.get(Account, account.id)
        if fresh is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
        accounts = AccountService(session, settings)
        try:
            identity = await accounts.bind_bot_token(fresh, payload.bot_token)
        except InvalidBotTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        await session.commit()
    return BindBotTokenResponse(
        bot_username=identity.username,
        bot_telegram_id=identity.telegram_id,
        bot_url=_bot_url(identity.username),
    )


@router.post("/v1/me/pause", response_model=AccountStateResponse)
async def pause_account(account: Account = Depends(require_account)) -> AccountStateResponse:
    settings = get_settings()
    async with SessionLocal() as session:
        fresh = await session.get(Account, account.id)
        if fresh is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
        await AccountService(session, settings).pause(fresh)
        await session.commit()
        return _account_to_state(fresh, settings)


@router.post("/v1/me/resume", response_model=AccountStateResponse)
async def resume_account(account: Account = Depends(require_account)) -> AccountStateResponse:
    settings = get_settings()
    async with SessionLocal() as session:
        fresh = await session.get(Account, account.id)
        if fresh is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
        await AccountService(session, settings).resume(fresh)
        await session.commit()
        return _account_to_state(fresh, settings)


@router.delete("/v1/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account: Account = Depends(require_account)) -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        fresh = await session.get(Account, account.id)
        if fresh is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
        accounts = AccountService(session, settings)
        await accounts.unbind_bot_token(fresh)
        await session.delete(fresh)
        await session.commit()


@router.get("/v1/me/healthz", response_model=dict[str, Any])
async def health_for_account(account: Account = Depends(require_account)) -> dict[str, Any]:
    return {"ok": True, "account_id": account.id}
