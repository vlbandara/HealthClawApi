from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from healthclaw.api.deps import SessionDep
from healthclaw.core.security import require_api_key
from healthclaw.proactivity.service import ProactivityService
from healthclaw.schemas.reminders import ReminderCreate, ReminderRead
from healthclaw.services.conversation import ConversationService

router = APIRouter(
    prefix="/v1/reminders", tags=["reminders"], dependencies=[Depends(require_api_key)]
)


@router.post("", response_model=ReminderRead)
async def create_reminder(payload: ReminderCreate, session: SessionDep) -> ReminderRead:
    await ConversationService(session).ensure_user(payload.user_id)
    key = payload.idempotency_key or f"reminder:{payload.user_id}:{uuid.uuid4().hex}"
    reminder = await ProactivityService(session).create_reminder(
        user_id=payload.user_id,
        text=payload.text,
        due_at=payload.due_at,
        channel=payload.channel,
        idempotency_key=key,
    )
    await session.commit()
    return ReminderRead(
        id=reminder.id,
        user_id=reminder.user_id,
        text=reminder.text,
        due_at=reminder.due_at,
        channel=reminder.channel,
        status=reminder.status,
    )
