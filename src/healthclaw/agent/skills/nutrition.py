from __future__ import annotations

from pathlib import Path
from typing import Any

from healthclaw.agent.skills.base import skill_prompt_path
from healthclaw.schemas.actions import Action


class NutritionSkill:
    name = "nutrition"
    memory_kinds = {"nutrition_pattern", "preference", "user_pattern"}
    description_for_llm = (
        "Activate when the user mentions eating, meals, hunger, snacking, hydration/water, "
        "calories, macros, or specific foods. Focuses on balanced eating patterns and "
        "practical nutritional guidance. Wellness-only — never prescribe medical diets."
    )
    prompt_module_path: Path = skill_prompt_path("nutrition")
    output_schema_fragment: dict[str, Any] = {
        "log_metric": {
            "metric": "water_ml",
            "description": "Log water intake in ml.",
        },
        "schedule_protocol": {
            "kind": "nutrition_pattern",
            "description": "Set a recurring eating pattern or meal timing protocol.",
        },
    }

    def extract_actions(self, llm_payload: dict[str, Any]) -> list[Action]:
        actions = []
        for raw in llm_payload.get("actions", []):
            if not isinstance(raw, dict):
                continue
            action_type = str(raw.get("type") or "")
            payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
            if action_type == "log_metric" and payload.get("metric") in {"water_ml"}:
                actions.append(Action(type=action_type, payload=payload))
            elif action_type == "schedule_protocol" and payload.get("kind") == "nutrition_pattern":
                actions.append(Action(type=action_type, payload=payload))
        return actions
