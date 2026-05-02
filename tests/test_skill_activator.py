"""Tests for LLM skill activator (Workstream D)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from healthclaw.agent.skills import ALL_SKILLS, get_enabled_skills
from healthclaw.agent.skills.sleep import SleepSkill


def _mock_settings(**overrides):
    s = MagicMock()
    s.skill_activator_model = "test-model"
    s.skill_activator_max_tokens = 120
    s.health_skill_sleep_enabled = overrides.get("health_skill_sleep_enabled", False)
    s.health_skill_nutrition_enabled = overrides.get("health_skill_nutrition_enabled", False)
    s.health_skill_movement_enabled = overrides.get("health_skill_movement_enabled", False)
    s.health_skill_mental_health_enabled = overrides.get(
        "health_skill_mental_health_enabled", False
    )
    s.health_skill_medication_adherence_enabled = overrides.get(
        "health_skill_medication_adherence_enabled", False
    )
    return s


def test_all_skills_have_required_attributes() -> None:
    for skill in ALL_SKILLS:
        assert hasattr(skill, "name")
        assert hasattr(skill, "memory_kinds")
        assert hasattr(skill, "description_for_llm")
        assert hasattr(skill, "prompt_module_path")
        assert hasattr(skill, "output_schema_fragment")
        assert callable(skill.extract_actions)
        assert len(skill.description_for_llm) > 10


def test_skill_names_unique() -> None:
    names = [s.name for s in ALL_SKILLS]
    assert len(names) == len(set(names))


def test_get_enabled_skills_flag_off() -> None:
    settings = _mock_settings()
    enabled = get_enabled_skills(settings)
    assert enabled == []


def test_get_enabled_skills_one_enabled() -> None:
    settings = _mock_settings(health_skill_sleep_enabled=True)
    enabled = get_enabled_skills(settings)
    assert len(enabled) == 1
    assert enabled[0].name == "sleep"


def test_sleep_skill_extract_actions_schedule_protocol() -> None:
    skill = SleepSkill()
    payload = {
        "actions": [
            {
                "type": "schedule_protocol",
                "payload": {
                    "title": "Wind down by 22:00",
                    "kind": "sleep_protocol",
                    "cadence": "daily",
                },
            }
        ]
    }
    actions = skill.extract_actions(payload)
    assert len(actions) == 1
    assert actions[0].type == "schedule_protocol"


def test_sleep_skill_ignores_wrong_kind() -> None:
    skill = SleepSkill()
    payload = {
        "actions": [
            {
                "type": "schedule_protocol",
                "payload": {"title": "Test", "kind": "nutrition_pattern", "cadence": "daily"},
            }
        ]
    }
    actions = skill.extract_actions(payload)
    assert actions == []


def test_no_regex_in_activator_module() -> None:
    """Enforce the no-regex rule: skill_activator.py must not use re.match/re.search."""
    import ast
    from pathlib import Path
    src = (Path(__file__).parent.parent / "src/healthclaw/agent/skill_activator.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in {"match", "search", "findall", "fullmatch"} and isinstance(
                node.value, ast.Name
            ) and node.value.id == "re":
                pytest.fail(f"regex call found in skill_activator.py at line {node.lineno}")


@pytest.mark.asyncio
async def test_skill_activator_no_skills_when_client_disabled() -> None:
    """When OpenRouter is disabled, activator returns empty list."""
    from healthclaw.agent.skill_activator import select_skills

    settings = _mock_settings(health_skill_sleep_enabled=True)
    enabled = [SleepSkill()]

    mock_client = MagicMock()
    mock_client.enabled = False

    with patch("healthclaw.integrations.openrouter.OpenRouterClient", return_value=mock_client):
        activated = await select_skills(
            "I can't sleep at night",
            memories=[],
            motives=[],
            time_ctx={"part_of_day": "evening"},
            enabled_skills=enabled,
            max_active=2,
            settings=settings,
        )

    assert activated == []


@pytest.mark.asyncio
async def test_skill_activator_returns_empty_on_error() -> None:
    """Activator returns [] gracefully when LLM call fails."""
    from healthclaw.agent.skill_activator import select_skills

    settings = _mock_settings(health_skill_sleep_enabled=True)
    enabled = [SleepSkill()]

    mock_client = AsyncMock()
    mock_client.enabled = True
    mock_client.chat_completion = AsyncMock(side_effect=RuntimeError("network error"))

    with patch("healthclaw.integrations.openrouter.OpenRouterClient", return_value=mock_client):
        activated = await select_skills(
            "I can't sleep at night",
            memories=[],
            motives=[],
            time_ctx={"part_of_day": "evening"},
            enabled_skills=enabled,
            max_active=2,
            settings=settings,
        )

    assert activated == []
