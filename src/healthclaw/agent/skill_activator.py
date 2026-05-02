"""LLM-driven skill activator.

Decides which (if any) health skills to inject into the system prompt for a given turn.
Activation is an LLM call — never regex, never keyword matching.

The activator sees:
  - The user's message (or synthesizer signal summary for the autonomy path)
  - Top memories (kinds + keys)
  - Active motives
  - Time context summary

It asks a small/cheap model to return a JSON list of activated skill names + reasons.
Capped at HEALTH_SKILL_MAX_ACTIVE skills per turn.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

ACTIVATOR_SYSTEM_PROMPT_TEMPLATE = """\
You are a health-skill router for a wellness companion.

Given a user message and context, decide which health skill lenses (if any) are relevant.
Return ONLY valid JSON with key "activated" containing a list of objects
each having "name" and "reason" keys.
Example output: {{"activated": [{{"name": "sleep", "reason": "user mentioned insomnia"}}]}}

Available skills:
__SKILL_DESCRIPTIONS__

Rules:
- Activate only skills that are clearly relevant to the user's message or context.
- Return an empty list if nothing clearly applies.
- Never activate more than __MAX_ACTIVE__ skills.
- Do NOT activate mental_health unless the user expresses emotional difficulty.
"""


async def select_skills(
    user_content: str,
    memories: list[dict[str, Any]],
    motives: list[dict[str, Any]],
    time_ctx: dict[str, Any],
    enabled_skills: list[Any],
    *,
    max_active: int = 2,
    settings: Any,
) -> list[Any]:
    """Return a list of activated Skill objects (max_active)."""
    if not enabled_skills:
        return []

    from healthclaw.integrations.openrouter import OpenRouterClient

    client = OpenRouterClient(settings)
    if not client.enabled:
        return []

    skill_desc_lines = "\n".join(
        f"- {skill.name}: {skill.description_for_llm}"
        for skill in enabled_skills
    )
    system_prompt = (
        ACTIVATOR_SYSTEM_PROMPT_TEMPLATE
        .replace("__SKILL_DESCRIPTIONS__", skill_desc_lines)
        .replace("__MAX_ACTIVE__", str(max_active))
    )
    memory_summary = [
        f"{m.get('kind')}:{m.get('key')}"
        for m in memories[:8]
    ]
    motive_summary = [
        f"{m.get('name')} (weight={m.get('weight', 0):.2f})"
        for m in motives
        if m.get("weight", 0) > 0.2
    ]
    user_payload = {
        "user_message": user_content[:400],
        "top_memories": memory_summary,
        "active_motives": motive_summary,
        "part_of_day": time_ctx.get("part_of_day", ""),
        "circadian_phase": time_ctx.get("circadian_phase", ""),
    }

    try:
        result = await client.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            max_tokens=settings.skill_activator_max_tokens,
            temperature=0.1,
            model=settings.skill_activator_model,
            metadata={"model_role": "skill_activator"},
        )
        raw = result.content.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:].strip()
        payload = json.loads(raw)
        activated_names = [
            str(entry.get("name") or "")
            for entry in (payload.get("activated") or [])
            if isinstance(entry, dict)
        ]
        skill_map = {s.name: s for s in enabled_skills}
        activated = [
            skill_map[name]
            for name in activated_names
            if name in skill_map
        ][:max_active]
        logger.debug("SkillActivator: activated=%s", [s.name for s in activated])
        return activated
    except Exception as exc:
        logger.warning("SkillActivator failed, no skills activated: %s", exc)
        return []
