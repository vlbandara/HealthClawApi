from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Action(BaseModel):
    type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = None


class CreateReminderPayload(BaseModel):
    text: str = Field(..., max_length=1000)
    due_at_iso: str
    idempotency_key: str | None = None


class CreateOpenLoopPayload(BaseModel):
    title: str = Field(..., max_length=240)
    kind: str = "commitment"
    due_after_iso: str | None = None


class CloseOpenLoopPayload(BaseModel):
    id: str
    summary: str = Field(..., max_length=500)
    outcome: Literal["completed", "dropped", "reframed"]
