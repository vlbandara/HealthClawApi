from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateReminderAction(BaseModel):
    type: Literal["create_reminder"]
    text: str = Field(..., max_length=1000)
    due_at_iso: str
    idempotency_key: str | None = None


class CreateOpenLoopAction(BaseModel):
    type: Literal["create_open_loop"]
    title: str = Field(..., max_length=240)
    kind: Literal["commitment", "open_loop"] = "commitment"
    due_after_iso: str | None = None


class CloseOpenLoopAction(BaseModel):
    type: Literal["close_open_loop"]
    id: str
    summary: str = Field(..., max_length=500)
    outcome: Literal["completed", "dropped", "reframed"]


class NoneAction(BaseModel):
    type: Literal["none"]


Action = CreateReminderAction | CreateOpenLoopAction | CloseOpenLoopAction | NoneAction
