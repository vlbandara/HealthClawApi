from __future__ import annotations

from pathlib import Path
from typing import Any

from healthclaw.agent.skills.base import skill_prompt_path
from healthclaw.schemas.actions import Action, ScheduleProtocolPayload


class SleepSkill:
    name = "sleep"
    memory_kinds = {"sleep_protocol", "routine", "user_pattern"}
    description_for_llm = (
        "Activate when the user mentions sleep quality, tiredness, insomnia, waking at night, "
        "bedtime, naps, or when wearable_sleep signals are present. "
        "Focuses on sleep hygiene, circadian alignment, and protocol setting."
    )
    prompt_module_path: Path = skill_prompt_path("sleep")
    output_schema_fragment: dict[str, Any] = {
        "schedule_protocol": {
            "kind": "sleep_protocol",
            "description": "Create or update a bedtime or wind-down routine.",
        },
        "log_metric": {
            "metric": "sleep_hours",
            "description": "Log how many hours the user slept.",
        },
    }

    def extract_actions(self, llm_payload: dict[str, Any]) -> list[Action]:
        actions = []
        for raw in llm_payload.get("actions", []):
            if not isinstance(raw, dict):
                continue
            action_type = str(raw.get("type") or "")
            payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
            if action_type == "schedule_protocol" and payload.get("kind") == "sleep_protocol":
                try:
                    ScheduleProtocolPayload(**payload)
                    actions.append(Action(type=action_type, payload=payload))
                except Exception:
                    pass
            elif action_type == "log_metric" and payload.get("metric") == "sleep_hours":
                actions.append(Action(type=action_type, payload=payload))
        return actions
