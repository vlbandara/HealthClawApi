from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Channel = Literal["web", "telegram", "whatsapp", "slack", "discord"]


class ConversationEvent(BaseModel):
    user_id: str
    channel: Channel = "web"
    external_user_id: str | None = None
    content: str
    content_type: Literal["text", "voice_transcript"] = "text"
    occurred_at: datetime | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
