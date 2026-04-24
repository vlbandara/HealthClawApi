from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from healthclaw.schemas.events import Channel


class ReminderCreate(BaseModel):
    user_id: str
    text: str = Field(min_length=1, max_length=1000)
    due_at: datetime
    channel: Channel = "telegram"
    idempotency_key: str | None = None


class ReminderRead(BaseModel):
    id: str
    user_id: str
    text: str
    due_at: datetime
    channel: str
    status: str
