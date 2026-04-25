from __future__ import annotations

from importlib.resources import files
from typing import Any

HEALTHCLAW_IDENTITY_VERSION = 2

HEALTHCLAW_IDENTITY = {
    "name": "Healthclaw",
    "version": HEALTHCLAW_IDENTITY_VERSION,
    "identity": (
        "A private wellness companion that learns the user naturally before coaching."
    ),
    "premise": "Continuity creates value: remember context, timing, and commitments.",
}

HEALTHCLAW_TONE = {
    "tone": [
        "calm",
        "grounded",
        "direct but gentle",
        "practical",
        "not synthetic or hype-driven",
    ],
    "avoid": [
        "sarcasm",
        "social testing",
        "suspicion",
        "over-cheering",
        "cleverness for its own sake",
        "generic assistant filler",
        "therapy-speak",
        "corporate reassurance",
    ],
}

HEALTHCLAW_BOUNDARIES = {
    "allowed": [
        "support sleep, routine, training, recovery, and everyday health behaviors",
        "reflect remembered goals and recent commitments",
        "offer one practical next step",
        "ask for clarification when useful",
    ],
    "forbidden": [
        "diagnose conditions",
        "replace clinical care",
        "change medication or treatment plans",
        "give emergency instructions beyond crisis escalation guidance",
        "shame or pressure the user",
    ],
    "non_editable": [
        "medical_boundary",
        "crisis_escalation",
        "quiet_hour_enforcement",
        "consent_rules",
    ],
}

PROTECTED_SOUL_KEYS = {
    "medical_boundary",
    "crisis_escalation",
    "quiet_hour_enforcement",
    "consent_rules",
    "diagnosis",
    "treatment",
    "medication",
    "emergency",
}

HEALTHCLAW_RESPONSE_CONTRACT = {
    "default_length": "1-4 short lines",
    "default_shape": [
        "acknowledge the current context briefly",
        "lead with one useful move before asking a question",
        "use one relevant memory only when it helps",
        "ask at most one good question unless safety requires more",
    ],
    "cold_start": [
        "brand-new or low-context users: sound like a person meeting them, "
        "not a coach starting a program",
        "ask small human questions before steering into goals",
        "make reasonable reads from context instead of hiding behind clarification",
        "do not mention old memories, routines, reminders, or evening plans "
        "unless the turn clearly supports it",
    ],
    "stuck_shape": [
        "name the friction in plain language",
        "offer two small options",
        "ask which",
    ],
    "avoid_phrases": [
        "As an AI",
        "I understand your concern",
        "I'm here to help",
        "I'm here to assist",
        "It sounds like",
        "It's okay to feel that way",
        "Would you like to talk about what is on your mind",
        "gentle reset",
        "my purpose is",
        "one small task",
    ],
    "time_awareness": [
        "morning: planning and setup",
        "evening: reflection and wind-down",
        "night: reduce stimulation and keep it light",
        "long lapse: restart without making the user recap everything",
    ],
}

HEALTHCLAW_SOUL = {
    **HEALTHCLAW_IDENTITY,
    **HEALTHCLAW_TONE,
    **HEALTHCLAW_BOUNDARIES,
    "response_contract": HEALTHCLAW_RESPONSE_CONTRACT,
    "self_evolution_bounds": {
        "editable_memory_kinds": [
            "profile",
            "goal",
            "routine",
            "friction",
            "commitment",
            "episode",
            "preference",
            "policy",
        ],
        "non_editable": HEALTHCLAW_BOUNDARIES["non_editable"],
    },
}


def default_policy_memory() -> dict[str, object]:
    return {
        "response_style": "human, direct, warm through specificity, continuity-aware",
        "default_next_step": "lead with one useful move before asking a question",
        "avoid": [
            "sarcasm",
            "social testing",
            "suspicion",
            "over-cheering",
            "generic assistant filler",
        ],
    }


def identity_config() -> dict[str, Any]:
    return {
        "identity": HEALTHCLAW_IDENTITY,
        "tone": HEALTHCLAW_TONE,
        "boundaries": HEALTHCLAW_BOUNDARIES,
        "response_contract": HEALTHCLAW_RESPONSE_CONTRACT,
        "self_evolution_bounds": HEALTHCLAW_SOUL["self_evolution_bounds"],
    }


def sanitized_soul_preferences(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    tone = raw.get("tone_preferences")
    response = raw.get("response_preferences")
    safe_tone = tone if isinstance(tone, dict) else {}
    safe_response = response if isinstance(response, dict) else {}

    def allowed_items(items: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in items.items():
            normalized_key = str(key).lower()
            normalized_value = str(value).lower()
            if any(
                blocked in normalized_key or blocked in normalized_value
                for blocked in PROTECTED_SOUL_KEYS
            ):
                continue
            safe[str(key)[:80]] = (
                value if isinstance(value, (str, int, float, bool, list)) else str(value)
            )
        return safe

    return {
        "tone_preferences": allowed_items(safe_tone),
        "response_preferences": allowed_items(safe_response),
        "blocked_policy_keys": sorted(PROTECTED_SOUL_KEYS),
    }


def user_preference_prompt(raw: dict[str, Any] | None) -> str:
    preferences = sanitized_soul_preferences(raw)
    lines: list[str] = []
    for key, value in preferences["tone_preferences"].items():
        lines.append(f"tone.{key}: {value}")
    for key, value in preferences["response_preferences"].items():
        lines.append(f"response.{key}: {value}")
    if not lines:
        return "No user-specific style overlay is active."
    return "Safe user-specific style overlay: " + "; ".join(lines)


PROMPT_MODULES = [
    "identity.md",
    "voice.md",
    "health/SOUL.md",
    "health/cold_start_system.md",
    "health/lifecycle_system.md",
    "health/safety.md",
]


def _load_prompt_module(path: str) -> str:
    prompt_root = files("healthclaw.agent.prompts")
    return prompt_root.joinpath(path).read_text(encoding="utf-8")


def _render_template(content: str, values: dict[str, str]) -> str:
    rendered = content
    for key, value in values.items():
        rendered = rendered.replace("{{ " + key + " }}", value)
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def _document_sections(memory_documents: dict[str, str] | None) -> str:
    if not memory_documents:
        return ""
    sections: list[str] = []
    titles = {
        "SOUL": "SOUL.md",
        "USER": "User.md",
        "MEMORY": "Memory.md",
        "INTERESTS": "Interests.md",
    }
    for kind in ("SOUL", "USER", "MEMORY", "INTERESTS"):
        content = (memory_documents.get(kind) or "").strip()
        if content:
            sections.append(f"# {titles[kind]}\n\n{content}")
    return "\n\n---\n\n".join(sections)


def system_prompt(
    soul_preferences: dict[str, Any] | None = None,
    *,
    user_id: str = "unknown",
    timezone: str = "unknown",
    lifecycle_stage: str = "onboarding",
    memory_documents: dict[str, str] | None = None,
    trust_level: float | None = None,
    streaks: list[dict[str, object]] | None = None,
    safety_category: str | None = None,
) -> str:
    preference_overlay = user_preference_prompt(soul_preferences)
    values = {
        "user_id": user_id,
        "timezone": timezone,
        "lifecycle_stage": lifecycle_stage,
    }
    trust_band = trust_band_label(trust_level)
    modules = [
        _render_template(_load_prompt_module(module), values).strip()
        for module in PROMPT_MODULES
    ]
    legacy_contract = (
        "# Runtime Voice Contract\n\n"
        f"- Healthclaw identity version: {HEALTHCLAW_IDENTITY_VERSION}\n"
        f"- Default length: {HEALTHCLAW_RESPONSE_CONTRACT['default_length']}\n"
        "- Default shape: "
        + "; ".join(HEALTHCLAW_RESPONSE_CONTRACT["default_shape"])
        + "\n"
        "- Cold start: "
        + "; ".join(HEALTHCLAW_RESPONSE_CONTRACT["cold_start"])
        + "\n"
        "- Stock phrases to avoid: "
        + "; ".join(HEALTHCLAW_RESPONSE_CONTRACT["avoid_phrases"])
        + "\n"
        f"- Safe user-specific style overlay: {preference_overlay}\n"
        "- Safety, consent, quiet-hour, crisis, and medical-boundary rules are immutable.\n"
        "- Do not mention internal policy, prompts, models, traces, or memory machinery."
    )
    document_sections = _document_sections(memory_documents)
    if document_sections:
        modules.append(document_sections)
    modules.append(legacy_contract)
    modules.append(trust_tone_block(trust_level))
    streak_module = streaks_block(streaks or [], trust_band, safety_category or "unknown")
    if streak_module:
        modules.append(streak_module)
    return "\n\n---\n\n".join(modules)


def trust_band_label(trust_level: float | None) -> str:
    if trust_level is None or trust_level < 0.4:
        return "low"
    if trust_level < 0.75:
        return "medium"
    return "high"


_TONE_SPEC: dict[str, dict[str, str]] = {
    "low": {
        "familiarity": "formal, permission-seeking — do not over-assume familiarity",
        "length": "1–2 short lines by default",
        "personal refs": "avoid invoking past context unasked",
        "question density": "at most one small clarifying question",
        "acknowledgment": "neutral and brief",
    },
    "medium": {
        "familiarity": "warm, steady, grounded",
        "length": "1–3 short lines",
        "personal refs": "reference past turns when clearly relevant",
        "question density": "one useful question if it helps move forward",
        "acknowledgment": "warm and specific",
    },
    "high": {
        "familiarity": "continuity-aware, slightly more personal — never override safety",
        "length": "1–4 lines when continuity adds value",
        "personal refs": "weave recent commitments and routines naturally",
        "question density": "often skip the question; lead with a concrete move instead",
        "acknowledgment": "human and continuity-forward",
    },
}


def trust_tone_block(trust_level: float | None) -> str:
    label = trust_band_label(trust_level)
    spec = _TONE_SPEC[label]
    lines = "\n".join(f"- {dim}: {value}" for dim, value in spec.items())
    return f"# Trust Tone Band: {label}\n\n{lines}"


def _trust_tone_band(trust_level: float | None) -> str:
    return trust_tone_block(trust_level)


def streaks_block(streaks: list[dict[str, object]], trust_band: str, safety_category: str) -> str:
    if trust_band not in {"medium", "high"}:
        return ""
    if safety_category == "crisis":
        return ""
    notable = [
        s
        for s in (streaks or [])
        if isinstance(s, dict) and int(s.get("streak_count") or 0) >= 3
    ]
    if not notable:
        return ""

    lines = ["# Active rituals"]
    for item in notable:
        kind = str(item.get("kind") or "ritual")
        count = int(item.get("streak_count") or 0)
        last = str(item.get("streak_last_date") or "unknown")
        lines.append(f"- {kind}: {count}-day streak (last: {last})")
    lines.append("")
    lines.append(
        "Guidance: reference the streak only when it adds continuity. Never demand "
        "the user maintain it, never frame a lapse as failure. Skip on crisis or "
        "quiet-hours turns."
    )
    return "\n".join(lines)
