from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from healthclaw.db.session import SessionLocal
from healthclaw.schemas.events import ConversationEvent
from healthclaw.schemas.messages import MessageCreate
from healthclaw.services.conversation import ConversationService

router = APIRouter(tags=["websocket"])


@router.websocket("/v1/ws/conversations/{user_id}")
async def conversation_ws(websocket: WebSocket, user_id: str) -> None:
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            payload = MessageCreate(**data)
            await websocket.send_json({"type": "status", "value": "processing"})
            async with SessionLocal() as session:
                response = await ConversationService(session).handle_event(
                    ConversationEvent(
                        user_id=user_id,
                        channel=payload.channel,
                        content=payload.content,
                        metadata=payload.metadata,
                    ),
                    timezone=payload.timezone,
                )
            for token in response.response.split():
                await websocket.send_json({"type": "token", "value": token + " "})
                await asyncio.sleep(0)
            await websocket.send_json({"type": "final", "value": response.model_dump(mode="json")})
    except WebSocketDisconnect:
        return
