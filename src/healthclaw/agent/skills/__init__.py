"""Health skill registry.

All registered skills. The SkillActivator queries this list via LLM to decide
which (if any) skills to activate for a given turn.
"""
from __future__ import annotations

from healthclaw.agent.skills.medication_adherence import MedicationAdherenceSkill
from healthclaw.agent.skills.mental_health import MentalHealthSkill
from healthclaw.agent.skills.movement import MovementSkill
from healthclaw.agent.skills.nutrition import NutritionSkill
from healthclaw.agent.skills.sleep import SleepSkill

ALL_SKILLS = [
    SleepSkill(),
    NutritionSkill(),
    MovementSkill(),
    MentalHealthSkill(),
    MedicationAdherenceSkill(),
]

SKILL_MAP = {skill.name: skill for skill in ALL_SKILLS}


def get_enabled_skills(settings: object) -> list:
    """Return skills that are individually enabled by feature flag."""
    enabled = []
    for skill in ALL_SKILLS:
        flag = f"health_skill_{skill.name}_enabled"
        if getattr(settings, flag, False):
            enabled.append(skill)
    return enabled
