from __future__ import annotations

import json
import logging

from healthclaw.core.config import get_settings
from healthclaw.core.tracing import start_span
from healthclaw.integrations.openrouter import OpenRouterClient
from healthclaw.schemas.memory import MemoryMutation

logger = logging.getLogger(__name__)

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


async def extract_memory_mutations_enriched(content: str) -> list[MemoryMutation]:
    mutations: list[MemoryMutation] = []
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

    seen: set[tuple[str, str]] = set()
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
