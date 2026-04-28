from __future__ import annotations

import json
import logging
import re

from healthclaw.core.config import get_settings
from healthclaw.core.tracing import start_span
from healthclaw.integrations.openrouter import OpenRouterClient
from healthclaw.schemas.memory import MemoryMutation

logger = logging.getLogger(__name__)

NAME_RE = re.compile(
    r"\b(?:my name is|call me)\s+(?P<name>[A-Za-z][A-Za-z .'-]{1,60})(?:[.!?]|$)",
    re.I,
)
GOAL_RE = re.compile(
    r"\b(?:"
    r"my goal is|i want to|i need to|"
    r"(?:i'?m|i am|im)?\s*trying to|"
    r"i'?d like to|i would like to"
    r")\s+(?P<goal>[^.!\n]+)",
    re.I,
)
PREFERENCE_RE = re.compile(r"\b(?:i prefer|please be|talk to me)\s+(?P<pref>[^.!\n]+)", re.I)
FRICTION_RE = re.compile(
    r"\b(?:struggle with|hard for me to|i keep missing|i keep)\s+(?P<friction>[^.!\n]+)",
    re.I,
)
ROUTINE_RE = re.compile(
    r"\b(?:my routine is|i usually|i normally)\s+(?P<routine>[^.!\n]+)", re.I
)
COMMITMENT_RE = re.compile(
    r"\b(?:i will|i'll|tonight i will|tomorrow i will)\s+(?P<commitment>[^.!\n]+)", re.I
)
RELATIONSHIP_RE = re.compile(
    r"\b(?:you helped me|last time we|we talked about)\s+(?P<context>[^.!\n]+)", re.I
)

ALLOWED_LLM_KINDS = {
    "profile",
    "goal",
    "routine",
    "friction",
    "commitment",
    "open_loop",
    "relationship",
    "episode",
    "preference",
}


def extract_memory_mutations(content: str) -> list[MemoryMutation]:
    mutations: list[MemoryMutation] = []
    if match := NAME_RE.search(content):
        mutations.append(
            MemoryMutation(
                kind="profile",
                key="preferred_name",
                value={"text": match.group("name").strip()},
                confidence=0.9,
                reason="User stated their preferred name.",
                layer="profile",
            )
        )
    if match := GOAL_RE.search(content):
        goal_text, implied_friction = _split_goal_text(match.group("goal").strip())
        mutations.append(
            MemoryMutation(
                kind="goal",
                key="current_goal",
                value={"text": goal_text},
                confidence=0.78,
                reason="User stated an active goal.",
            )
        )
        if implied_friction:
            mutations.append(
                MemoryMutation(
                    kind="friction",
                    key="friction_point",
                    value={"text": implied_friction},
                    confidence=0.72,
                    reason="User described a friction point while stating a goal.",
                )
            )
    if (match := FRICTION_RE.search(content)) and not any(
        mutation.kind == "friction" and mutation.key == "friction_point"
        for mutation in mutations
    ):
        mutations.append(
            MemoryMutation(
                kind="friction",
                key="friction_point",
                value={"text": match.group("friction").strip()},
                confidence=0.72,
                reason="User described a friction point.",
            )
        )
    if match := PREFERENCE_RE.search(content):
        mutations.append(
            MemoryMutation(
                kind="preference",
                key="user_tone_preference",
                value={"text": match.group("pref").strip()},
                confidence=0.66,
                reason="User stated a communication preference.",
            )
        )
    if match := ROUTINE_RE.search(content):
        mutations.append(
            MemoryMutation(
                kind="routine",
                key="current_routine",
                value={"text": match.group("routine").strip()},
                confidence=0.7,
                reason="User described a routine.",
            )
        )
    if match := COMMITMENT_RE.search(content):
        mutations.append(
            MemoryMutation(
                kind="commitment",
                key="latest_commitment",
                value={"text": match.group("commitment").strip()},
                confidence=0.68,
                reason="User made a near-term commitment.",
                layer="open_loop",
            )
        )
    if match := RELATIONSHIP_RE.search(content):
        mutations.append(
            MemoryMutation(
                kind="relationship",
                key="relationship_context",
                value={"text": match.group("context").strip()},
                confidence=0.62,
                reason="User referenced shared conversation history.",
                layer="relationship",
            )
        )
    return mutations


def _split_goal_text(raw_goal: str) -> tuple[str, str | None]:
    for separator in (" but ", " though ", " although "):
        before, found, after = raw_goal.partition(separator)
        if found and before.strip():
            friction = _normalize_implied_friction(after)
            return before.strip(), friction
    return raw_goal.strip(), None


def _normalize_implied_friction(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    match = re.match(r"^(?:i\s+)?keep\s+(?P<friction>.+)$", text, re.I)
    if match:
        return match.group("friction").strip()
    return text


async def extract_memory_mutations_enriched(content: str) -> list[MemoryMutation]:
    mutations = extract_memory_mutations(content)
    settings = get_settings()
    client = OpenRouterClient(settings)
    if not client.enabled or len(content) < 24:
        return mutations

    messages = [
        {
            "role": "system",
            "content": (
                "Extract durable wellness companion memory as JSON only. "
                "Return an array of objects with kind, key, value, confidence, reason, and layer. "
                "Allowed kinds: profile, goal, routine, friction, commitment, open_loop, "
                "relationship, episode, preference. Do not create medical, crisis, consent, "
                "or diagnosis policy memories."
            ),
        },
        {"role": "user", "content": content},
    ]
    async with start_span(
        "memory.extract.llm",
        attributes={"model_role": "extract"},
    ):
        result = None
        try:
            result = await client.chat_completion(
                messages,
                max_tokens=500,
                temperature=0,
                metadata={"model_role": "extract"},
            )
            raw_items = json.loads(result.content)
        except (RuntimeError, json.JSONDecodeError, TypeError, ValueError):
            from opentelemetry import trace as otel_trace

            span = otel_trace.get_current_span()
            span.set_attribute("parse_error", True)
            raw_content = getattr(result, "content", "")[:200] if result else ""
            logger.warning(
                "memory_extract_parse_error",
                extra={"raw_content": raw_content},
            )
            return mutations
    if not isinstance(raw_items, list):
        return mutations

    seen = {(mutation.kind, mutation.key) for mutation in mutations}
    for raw in raw_items[:6]:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", ""))
        key = str(raw.get("key", ""))[:128]
        if kind not in ALLOWED_LLM_KINDS or not key or (kind, key) in seen:
            continue
        value = raw.get("value")
        if not isinstance(value, dict):
            text = str(value or "").strip()
            value = {"text": text} if text else {}
        if not value:
            continue
        try:
            mutation = MemoryMutation(
                kind=kind,  # type: ignore[arg-type]
                key=key,
                value=value,
                confidence=float(raw.get("confidence", 0.55)),
                reason=str(raw.get("reason") or "Structured memory extraction."),
                layer=str(raw.get("layer") or _default_layer(kind)),
                metadata={"extractor": "llm"},
            )
        except ValueError:
            continue
        mutations.append(mutation)
        seen.add((mutation.kind, mutation.key))
    return mutations


def _default_layer(kind: str) -> str:
    return {
        "profile": "profile",
        "goal": "durable",
        "routine": "durable",
        "friction": "durable",
        "commitment": "open_loop",
        "open_loop": "open_loop",
        "relationship": "relationship",
        "episode": "episode",
        "preference": "preference",
    }.get(kind, "durable")
