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
