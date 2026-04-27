from __future__ import annotations

from fastapi import Header, HTTPException, status

from healthclaw.core.config import get_settings
from healthclaw.db.models import Account
from healthclaw.db.session import SessionLocal
from healthclaw.services.auth import (
    AuthConfigError,
    AuthService,
    InvalidSessionError,
)


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


async def require_account(authorization: str | None = Header(default=None)) -> Account:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )
    token = authorization.split(" ", 1)[1].strip()
    settings = get_settings()
    async with SessionLocal() as session:
        auth = AuthService(session, settings)
        try:
            account_id = auth.verify_session_token(token)
        except AuthConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        except InvalidSessionError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc
        account = await session.get(Account, account_id)
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found"
            )
        session.expunge(account)
        return account
