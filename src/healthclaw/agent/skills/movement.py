from __future__ import annotations

from pathlib import Path
from typing import Any

from healthclaw.agent.skills.base import skill_prompt_path
from healthclaw.schemas.actions import Action


class MovementSkill:
    name = "movement"
    memory_kinds = {"movement_routine", "goal", "user_pattern"}
    description_for_llm = (
        "Activate when the user mentions walking, running, gym, workout, steps, soreness, "
        "stretching, exercise plans, or sedentary behaviour. Focuses on consistent movement "
        "patterns, recovery balance, and realistic goal-setting. Wellness-only."
    )
    prompt_module_path: Path = skill_prompt_path("movement")
    output_schema_fragment: dict[str, Any] = {
        "log_metric": {
            "metric": "steps",
            "description": "Log today's step count.",
        },
        "schedule_protocol": {
            "kind": "movement_routine",
            "description": "Set a recurring movement or exercise routine.",
        },
    }

    def extract_actions(self, llm_payload: dict[str, Any]) -> list[Action]:
        actions = []
        for raw in llm_payload.get("actions", []):
            if not isinstance(raw, dict):
                continue
            action_type = str(raw.get("type") or "")
            payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
            if action_type == "log_metric" and payload.get("metric") in {"steps", "weight_kg"}:
                actions.append(Action(type=action_type, payload=payload))
            elif action_type == "schedule_protocol" and payload.get("kind") == "movement_routine":
                actions.append(Action(type=action_type, payload=payload))
        return actions
