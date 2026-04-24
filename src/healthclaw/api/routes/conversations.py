from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends

from healthclaw.api.deps import SessionDep
from healthclaw.core.security import require_api_key
from healthclaw.schemas.events import ConversationEvent
from healthclaw.schemas.messages import MessageCreate, MessageResponse, StreamTokenResponse
from healthclaw.services.conversation import ConversationService

router = APIRouter(
    prefix="/v1/conversations",
    tags=["conversations"],
    dependencies=[Depends(require_api_key)],
)


@router.post("/{user_id}/messages", response_model=MessageResponse)
async def create_message(
    user_id: str, payload: MessageCreate, session: SessionDep
) -> MessageResponse:
    event = ConversationEvent(
        user_id=user_id,
        channel=payload.channel,
        content=payload.content,
        metadata=payload.metadata,
    )
    return await ConversationService(session).handle_event(event, timezone=payload.timezone)


@router.get("/{user_id}/stream-token", response_model=StreamTokenResponse)
async def stream_token(user_id: str) -> StreamTokenResponse:
    return StreamTokenResponse(token=secrets.token_urlsafe(32))
