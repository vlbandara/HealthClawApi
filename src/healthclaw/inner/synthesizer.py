"""Inner Synthesizer — LLM-powered deliberation that fuses signals + motives + self-model.

Replaces the single-signal reactive `reflect_on_wellbeing` call with a richer
context assembly that includes:
  - All recent signals (last 30 min)
  - Active motives with weights
  - Relevant self-model / user_pattern memories
  - Time context (circadian, anticipated events, engagement rhythm)
  - Open loops and recent exchanges

Returns an InnerIntent. Most intents will have kind="reflect_silently" or kind="wait".
The SpeechGate decides whether to emit the draft or defer.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.core.config import get_settings
from healthclaw.core.tracing import start_span
from healthclaw.db.models import Memory, Message, OpenLoop, User
from healthclaw.schemas.intents import InnerIntent

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "agent" / "prompts" / "inner_synthesizer.md"


def _load_synthesizer_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


class InnerSynthesizer:
    """Fuses signals + motives + self-model into a structured InnerIntent."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def synthesize(
        self,
        thought_id: str,
        user: User,
        signals: list[Any],
        motives: list[Any],
        time_ctx_dict: dict[str, Any],
    ) -> InnerIntent:
        """Run one deliberation cycle. Returns InnerIntent (may be silent/wait)."""
        settings = get_settings()

        from healthclaw.integrations.openrouter import OpenRouterClient
        client = OpenRouterClient(settings)
        if not client.enabled:
            return InnerIntent(
                kind="reflect_silently",
                why="openrouter_not_configured",
                thought_id=thought_id,
            )

        # Gather context
        self_model = await self._load_self_model(user.id)
        open_loops = await self._load_open_loops(user.id)
        recent = await self._load_recent_exchanges(user.id)

        context_payload = {
            "user_id": user.id,
            "timezone": user.timezone,
            "locale": user.locale,
            "time_context": time_ctx_dict,
            "signals": [
                {
                    "id": str(sig.id),
                    "kind": str(sig.kind),
                    "value": sig.value if isinstance(sig.value, dict) else {},
                }
                for sig in signals[:10]
            ],
            "motives": [
                {"name": m.name, "weight": m.weight, "rationale": m.rationale}
                for m in motives
            ],
            "self_model": self_model,
            "open_loops": open_loops,
            "recent_exchanges": recent,
        }

        system_prompt = _load_synthesizer_prompt()
        # Append crisis hotline map so LLM can pick the right number
        try:
            hotline_map = json.loads(settings.crisis_hotline_locale_map)
        except Exception:
            hotline_map = {}
        locale_code = (user.locale or "US").upper()[:2]
        hotline = hotline_map.get(locale_code, settings.crisis_hotline_default)
        system_prompt += f"\n\n## Crisis hotline for this user's locale ({locale_code}): {hotline}"

        async with start_span(
            "inner.synthesis",
            {"user_id": user.id, "thought_id": thought_id, "motive_count": len(motives)},
        ):
            try:
                result = await client.chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(context_payload)},
                    ],
                    max_tokens=settings.inner_synth_max_tokens,
                    temperature=settings.inner_synth_temperature,
                    model=settings.inner_synth_model,
                    metadata={"model_role": "inner_synthesis", "user_id": user.id},
                )
                raw = result.content.strip()
                if raw.startswith("```"):
                    parts = raw.split("```")
                    raw = parts[1] if len(parts) > 1 else raw
                    if raw.startswith("json"):
                        raw = raw[4:].strip()
                payload = json.loads(raw)
                intent = InnerIntent(**payload, thought_id=thought_id)
                logger.debug(
                    "InnerSynthesizer: user=%s kind=%s motive=%s confidence=%.2f",
                    user.id, intent.kind, intent.motive, intent.confidence,
                )
                return intent
            except Exception as exc:
                logger.warning("InnerSynthesizer failed for user %s: %s", user.id, exc)
                return InnerIntent(
                    kind="reflect_silently",
                    why=f"synthesis_error: {str(exc)[:100]}",
                    thought_id=thought_id,
                )

    async def _load_self_model(self, user_id: str) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.kind.in_(["self_model", "user_pattern", "rhythm"]),
                Memory.is_active.is_(True),
            ).limit(8)
        )
        return [
            {"kind": m.kind, "key": m.key, "value": m.value}
            for m in result.scalars()
        ]

    async def _load_open_loops(self, user_id: str) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(OpenLoop).where(
                OpenLoop.user_id == user_id,
                OpenLoop.status == "open",
            ).limit(5)
        )
        return [{"id": ol.id, "title": ol.title, "kind": ol.kind} for ol in result.scalars()]

    async def _load_recent_exchanges(self, user_id: str) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(Message)
            .where(Message.user_id == user_id, Message.role.in_(["user", "assistant"]))
            .order_by(Message.created_at.desc())
            .limit(6)
        )
        return [
            {"role": m.role, "content": m.content[:200]}
            for m in reversed(list(result.scalars()))
        ]
