from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UserPreferencesPatch(BaseModel):
    timezone: str | None = None
    quiet_start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    quiet_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    proactive_enabled: bool | None = None
    proactive_max_per_day: int | None = Field(default=None, ge=0, le=8)
    proactive_cooldown_minutes: int | None = Field(default=None, ge=15, le=1440)


class UserRead(BaseModel):
    id: str
    timezone: str
    quiet_start: str
    quiet_end: str
    onboarding_status: str = "new"
    consent_version: str = "wellness-v1"
    locale: str = "en"
    notification_channel: str = "telegram"
    last_active_at: datetime | None = None
    proactive_enabled: bool
    proactive_max_per_day: int
    proactive_cooldown_minutes: int
    proactive_paused_until: datetime | None = None
    monthly_llm_token_budget: int = 500_000
    monthly_llm_tokens_used: int = 0


class SoulPreferencesPatch(BaseModel):
    tone_preferences: dict[str, Any] | None = None
    response_preferences: dict[str, Any] | None = None


class SoulPreferencesRead(BaseModel):
    user_id: str
    version: int
    tone_preferences: dict[str, Any] = Field(default_factory=dict)
    response_preferences: dict[str, Any] = Field(default_factory=dict)
    blocked_policy_keys: list[str] = Field(default_factory=list)


class TimelineMessage(BaseModel):
    id: str
    role: str
    content: str
    channel: str
    created_at: datetime


class OpenLoopRead(BaseModel):
    id: str
    kind: str
    title: str
    status: str
    due_after: datetime | None = None
    last_checked_at: datetime | None = None


class UserTimelineResponse(BaseModel):
    user_id: str
    thread_id: str | None = None
    thread_summary: str = ""
    open_loops: list[OpenLoopRead] = Field(default_factory=list)
    recent_messages: list[TimelineMessage] = Field(default_factory=list)
