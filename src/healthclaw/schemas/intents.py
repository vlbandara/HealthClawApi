"""InnerIntent — the structured output of the inner synthesizer.

The synthesizer emits one InnerIntent per deliberation cycle. Most intents
are discarded or deferred — many mind-moments per spoken word (citta-vīthi).

kind meanings:
  nudge            — reach out with a supportive message
  check_in         — gentle open-ended check-in
  reflect_silently — agent chooses not to speak (logged but not emitted)
  investigate      — gather more info via web search before deciding
  wait             — explicitly defer to earliest_send_at
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class InnerIntent(BaseModel):
    kind: Literal["nudge", "check_in", "reflect_silently", "investigate", "wait"]
    motive: str = ""              # which motive drove this (auditable)
    why: str = ""                 # short rationale (not shown to user)
    fused_signals: list[str] = Field(default_factory=list)   # signal ids considered
    draft_message: str | None = None
    earliest_send_at: str | None = None    # ISO local datetime (wall clock)
    needs_web_search: bool = False
    web_search_query: str | None = None
    safety_category: Literal["normal", "distress", "crisis_escalated"] = "normal"
    confidence: float = 0.5
    # Internal fields set by tick / speech_gate (not from LLM)
    thought_id: str | None = None
    discarded: bool = False
    discarded_reason: str | None = None

    def model_post_init(self, __context: Any) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))
