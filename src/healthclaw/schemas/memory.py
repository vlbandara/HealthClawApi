from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryKind = Literal[
    "profile",
    "goal",
    "routine",
    "friction",
    "commitment",
    "open_loop",
    "relationship",
    "episode",
    "preference",
    "policy",
]


class MemoryRead(BaseModel):
    id: str
    kind: MemoryKind
    key: str
    layer: str = "durable"
    value: dict[str, Any]
    confidence: float
    freshness_score: float = 1.0
    source_message_ids: list[str]
    last_confirmed_at: datetime | None = None
    last_accessed_at: datetime | None = None
    refresh_after: datetime | None = None
    expires_at: datetime | None = None
    visibility: str = "user_visible"
    user_editable: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryDocumentRead(BaseModel):
    kind: str
    content: str
    source: str
    version: int
    updated_at: datetime


class UserMemoryResponse(BaseModel):
    memories: list[MemoryRead]
    documents: list[MemoryDocumentRead] = Field(default_factory=list)


class MemoryMutation(BaseModel):
    kind: MemoryKind
    key: str
    value: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    layer: str = "durable"
    refresh_after: datetime | None = None
    expires_at: datetime | None = None
    visibility: str = "user_visible"
    user_editable: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryPatch(BaseModel):
    value: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    refresh_after: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] | None = None
