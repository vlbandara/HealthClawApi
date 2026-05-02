"""Medication adherence skill.

Wellness-only: tracks adherence patterns and reminds. Never recommends
doses, drug interactions, substitutions, or provides clinical advice.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from healthclaw.agent.skills.base import skill_prompt_path
from healthclaw.schemas.actions import Action, ScheduleProtocolPayload


class MedicationAdherenceSkill:
    name = "medication_adherence"
    memory_kinds = {"medication_schedule", "commitment", "routine"}
    description_for_llm = (
        "Activate when the user mentions medication, pills, doses, prescriptions, supplements, "
        "or forgetting to take something. Helps track adherence patterns and set reminders. "
        "Strictly wellness-only: never recommend doses, interactions, or substitutions."
    )
    prompt_module_path: Path = skill_prompt_path("medication_adherence")
    output_schema_fragment: dict[str, Any] = {
        "schedule_protocol": {
            "kind": "medication_schedule",
            "description": "Set a recurring medication reminder.",
        },
        "create_reminder": {
            "description": "Create a one-off medication reminder.",
        },
    }

    def extract_actions(self, llm_payload: dict[str, Any]) -> list[Action]:
        actions = []
        for raw in llm_payload.get("actions", []):
            if not isinstance(raw, dict):
                continue
            action_type = str(raw.get("type") or "")
            payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
            if action_type == "schedule_protocol" and payload.get("kind") == "medication_schedule":
                try:
                    ScheduleProtocolPayload(**payload)
                    actions.append(Action(type=action_type, payload=payload))
                except Exception:
                    pass
            elif action_type == "create_reminder":
                actions.append(Action(type=action_type, payload=payload))
        return actions
