from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from healthclaw.schemas.events import Channel


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    channel: Channel = "web"
    timezone: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageResponse(BaseModel):
    trace_id: str
    idempotent_replay: bool = False
    user_message_id: str
    assistant_message_id: str
    thread_id: str
    response: str
    safety_category: str
    time_context: dict[str, Any]
    memory_updates: list[dict[str, Any]]


class StreamTokenResponse(BaseModel):
    token: str
