"""Health skill protocol and registry.

A Skill is a domain lens injected into the system prompt when the LLM activator
decides it's relevant. Skills are NOT sub-agents — they just add a focused prompt
module and extend the action contract for their domain.

Activation is always LLM-judged (SkillActivator calls a small model).
No regex, no keyword matching.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from healthclaw.schemas.actions import Action


@runtime_checkable
class Skill(Protocol):
    name: str
    memory_kinds: set[str]
    description_for_llm: str   # shown to the activator LLM (1-2 sentences)
    prompt_module_path: Path
    output_schema_fragment: dict[str, Any]  # JSON schema fragment for skill-specific actions

    def extract_actions(self, llm_payload: dict[str, Any]) -> list[Action]:
        """Parse skill-specific actions from the LLM's output payload."""
        ...


def load_prompt_module(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


_PROMPTS_ROOT = Path(__file__).parent.parent / "prompts" / "health"


def skill_prompt_path(name: str) -> Path:
    return _PROMPTS_ROOT / f"{name}.md"
