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


# ── WS7: naturalness pass action extensions ──────────────────────────────────


class SetUserTimezonePayload(BaseModel):
    """Persist the user's timezone and optionally their location.

    Emitted when the user tells us their city/country/timezone or shares a location.
    The executor writes User.timezone, optional home_lat/home_lon, and timezone_confidence.
    """
    tz: str = Field(..., description="IANA timezone name, e.g. 'Asia/Colombo'")
    lat: float | None = None
    lon: float | None = None
    source: Literal["user_stated", "shared_location"] = "user_stated"
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class OpenTopicPayload(BaseModel):
    """Open a tracked topic — the agent's suggestion, commitment, or follow-up intent.

    When the agent makes a suggestion the user should follow up on, it emits open_topic.
    The executor writes an open_loops row. The topic has a cooldown and max_surfaces so
    it never gets re-surfaced more than twice without fresh engagement.
    """
    title: str = Field(..., max_length=240)
    kind: Literal["nudge", "commitment", "check_in", "question_pending"] = "nudge"
    cooldown_hours: int = Field(default=12, ge=1, le=168)   # 1h – 1 week
    max_surfaces: int = Field(default=2, ge=1, le=10)
    expires_at_iso: str | None = None


# ── WS6: health skill action extensions ───────────────────────────────────────


class LogMetricPayload(BaseModel):
    """Record a point-in-time health metric (written to metric_logs table)."""
    metric: Literal["sleep_hours", "mood_1_5", "steps", "water_ml", "weight_kg"]
    value: float
    observed_at_iso: str
    note: str | None = Field(default=None, max_length=500)


class ScheduleProtocolPayload(BaseModel):
    """Create a recurring health protocol as a Memory + optional HeartbeatJob."""
    title: str = Field(..., max_length=240)
    kind: Literal[
        "sleep_protocol", "nutrition_pattern", "movement_routine", "medication_schedule"
    ]
    cadence: Literal["daily", "weekly", "weekdays"] = "daily"
    time_local: str | None = None           # "HH:MM" in user's local timezone
    expires_at_iso: str | None = None


class WebSearchPayload(BaseModel):
    """Request a web search before finalising the response."""
    query: str = Field(..., max_length=300)
    health_clinical: bool = False           # enforce health-domain whitelist when True


# ── Output envelope (LLM self-tags safety_category every turn) ────────────────


class GenerationEnvelope(BaseModel):
    """The full JSON output the LLM is expected to produce each turn."""
    message: str
    actions: list[Action] = Field(default_factory=list)
    memory_proposals: list[dict[str, Any]] = Field(default_factory=list)
    safety_category: Literal["normal", "distress", "crisis_escalated"] = "normal"
