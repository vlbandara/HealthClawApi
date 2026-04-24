from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.core.security import require_api_key
from healthclaw.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ApiKeyDep = Annotated[None, Depends(require_api_key)]
