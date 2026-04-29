from __future__ import annotations

from importlib.resources import files
from typing import Any

HEALTHCLAW_IDENTITY_VERSION = 2

HEALTHCLAW_IDENTITY = {
    "name": "Healthclaw",
    "version": HEALTHCLAW_IDENTITY_VERSION,
    "identity": "A private wellbeing companion with continuity-first judgment.",
    "premise": "Continuity creates value: remember context, timing, and commitments.",
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

PROMPT_MODULES = ["companion.md", "exchanges.md"]


def identity_config() -> dict[str, Any]:
    return {
        "identity": HEALTHCLAW_IDENTITY,
        "prompt_modules": PROMPT_MODULES,
        "protected_policy_keys": sorted(PROTECTED_SOUL_KEYS),
    }


def sanitized_soul_preferences(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}

    def allowed_items(items: Any) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        if not isinstance(items, dict):
            return safe
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
        "tone_preferences": allowed_items(raw.get("tone_preferences")),
        "response_preferences": allowed_items(raw.get("response_preferences")),
        "blocked_policy_keys": sorted(PROTECTED_SOUL_KEYS),
    }


def system_prompt(
    soul_preferences: dict[str, Any] | None = None,
    *,
    user_id: str = "unknown",
    timezone: str = "unknown",
    local_time: dict[str, object] | None = None,
    lifecycle_stage: str = "onboarding",
    recent_message_count: int | None = None,
    memory_documents: dict[str, str] | None = None,
    trust_level: float | None = None,
    sentiment_ema: float | None = None,
    voice_text_ratio: float | None = None,
    reply_latency_seconds_ema: float | None = None,
    streaks: list[dict[str, object]] | None = None,
    open_loops: list[dict[str, object]] | None = None,
    safety_category: str | None = None,
) -> str:
    modules = [_load_prompt_module(module).strip() for module in PROMPT_MODULES]
    modules.append(
        _observable_context_block(
            soul_preferences=soul_preferences,
            user_id=user_id,
            timezone=timezone,
            local_time=local_time,
            lifecycle_stage=lifecycle_stage,
            recent_message_count=recent_message_count,
            trust_level=trust_level,
            sentiment_ema=sentiment_ema,
            voice_text_ratio=voice_text_ratio,
            reply_latency_seconds_ema=reply_latency_seconds_ema,
            streaks=streaks,
            open_loops=open_loops,
            safety_category=safety_category,
        )
    )
    document_sections = _document_sections(memory_documents)
    if document_sections:
        modules.append(document_sections)
    return "\n\n---\n\n".join(modules)


def _load_prompt_module(path: str) -> str:
    prompt_root = files("healthclaw.agent.prompts")
    return prompt_root.joinpath(path).read_text(encoding="utf-8")


def _observable_context_block(
    *,
    soul_preferences: dict[str, Any] | None,
    user_id: str,
    timezone: str,
    local_time: dict[str, object] | None,
    lifecycle_stage: str,
    recent_message_count: int | None,
    trust_level: float | None,
    sentiment_ema: float | None,
    voice_text_ratio: float | None,
    reply_latency_seconds_ema: float | None,
    streaks: list[dict[str, object]] | None,
    open_loops: list[dict[str, object]] | None,
    safety_category: str | None,
) -> str:
    lines = [
        "# Observable Context",
        "",
        f"- user_id: {user_id}",
        f"- timezone: {timezone}",
        f"- local_time: {local_time or {'status': 'unknown'}}",
        f"- lifecycle_hint: {lifecycle_stage} ({_lifecycle_hint(lifecycle_stage)})",
        f"- recent_message_count: {recent_message_count if recent_message_count is not None else 'unknown'}",
        f"- trust_level: {_trust_level_line(trust_level)}",
        f"- sentiment_ema: {_format_number(sentiment_ema)}",
        f"- voice_text_ratio: {_format_number(voice_text_ratio)}",
        f"- reply_latency_seconds_ema: {_format_number(reply_latency_seconds_ema)}",
        f"- safety_category: {safety_category or 'unknown'}",
        f"- soul_preferences: {_preference_overlay(soul_preferences)}",
        "- streaks:",
        *_fact_lines(streaks, formatter=_format_streak),
        "- open_loops:",
        *_fact_lines(open_loops, formatter=_format_open_loop),
    ]
    return "\n".join(lines)


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


def _lifecycle_hint(stage: str) -> str:
    return {
        "onboarding": "relationship is still early; do not act overfamiliar",
        "early": "continuity exists, but keep assumptions light",
        "settling": "shared patterns are clearer; use them when useful",
    }.get(stage, "use the facts you have instead of forcing a stage script")


def _trust_level_line(value: float | None) -> str:
    if value is None:
        return "unknown (no stable read yet)"
    interpretation = (
        "light trust; stay specific without assuming history"
        if value < 0.4
        else "building trust; continuity can help if it fits"
        if value < 0.75
        else "strong trust; familiar continuity is available, not mandatory"
    )
    return f"{value:.2f} ({interpretation})"


def _format_number(value: float | None) -> str:
    return "unknown" if value is None else str(value)


def _preference_overlay(raw: dict[str, Any] | None) -> str:
    safe = sanitized_soul_preferences(raw)
    lines: list[str] = []
    for key, value in safe["tone_preferences"].items():
        lines.append(f"tone.{key}={value}")
    for key, value in safe["response_preferences"].items():
        lines.append(f"response.{key}={value}")
    return "; ".join(lines) if lines else "none supplied"


def _fact_lines(
    items: list[dict[str, object]] | None,
    *,
    formatter: Any,
) -> list[str]:
    if not items:
        return ["  - none"]
    lines = [formatter(item) for item in items[:10]]
    return [f"  - {line}" for line in lines if line] or ["  - none"]


def _format_streak(item: dict[str, object]) -> str:
    return (
        f"kind={item.get('kind') or 'unknown'} | "
        f"title={item.get('title') or 'untitled'} | "
        f"count={item.get('streak_count') or 0} | "
        f"last={item.get('streak_last_date') or 'unknown'}"
    )


def _format_open_loop(item: dict[str, object]) -> str:
    return (
        f"id={item.get('id') or 'unknown'} | "
        f"title={item.get('title') or 'untitled'} | "
        f"kind={item.get('kind') or 'unknown'} | "
        f"age_hours={item.get('age_hours') or 'unknown'}"
    )
