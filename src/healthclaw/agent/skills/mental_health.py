"""Mental health skill — contextual support and crisis awareness.

Crisis recognition is entirely LLM-driven (see companion.md safety section).
This skill adds domain-specific framing for anxiety, stress, mood, and
emotional regulation. It does NOT use regex to detect distress — the LLM
understands context, tone, and subtext.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from healthclaw.agent.skills.base import skill_prompt_path
from healthclaw.schemas.actions import Action


class MentalHealthSkill:
    name = "mental_health"
    memory_kinds = {"mood_pattern", "friction", "user_pattern"}
    description_for_llm = (
        "Activate when the user expresses emotional difficulty: anxiety, stress, sadness, "
        "overwhelm, loneliness, low motivation, or relationship difficulty. "
        "Also activate when mood_pattern memories show sustained distress. "
        "Provides grounded, non-clinical emotional support. "
        "Crisis recognition is context-driven — the companion notices when something feels serious."
    )
    prompt_module_path: Path = skill_prompt_path("mental_health")
    output_schema_fragment: dict[str, Any] = {
        "log_metric": {
            "metric": "mood_1_5",
            "description": "Log the user's current mood on a 1-5 scale.",
        },
    }

    def extract_actions(self, llm_payload: dict[str, Any]) -> list[Action]:
        actions = []
        for raw in llm_payload.get("actions", []):
            if not isinstance(raw, dict):
                continue
            action_type = str(raw.get("type") or "")
            payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
            if action_type == "log_metric" and payload.get("metric") == "mood_1_5":
                actions.append(Action(type=action_type, payload=payload))
        return actions
