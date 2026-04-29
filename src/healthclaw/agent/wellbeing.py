from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

from healthclaw.core.config import Settings
from healthclaw.core.tracing import start_span
from healthclaw.integrations.openrouter import OpenRouterClient

logger = logging.getLogger(__name__)

_PROMPT_MODULES = ("companion.md", "wellbeing_lens.md")
_WHEN_DELAY_RE = re.compile(r"^in_(\d{1,4})m$")
_REFLECTION_SYSTEM_PROMPT = """\
You are deciding whether a private wellbeing companion should reach out.

Return ONLY valid JSON with exactly these fields:
{
  "reach_out": true or false,
  "when": "now" | "hold" | "in_Nm",
  "message_seed": "short draft in the companion's voice",
  "rationale": "brief plain-language reason"
}

Rules:
- Use "hold" when no outbound message should be sent right now.
- Use "in_Nm" only for short delays that fit the timing context.
- If "reach_out" is false, set "when" to "hold" and "message_seed" to "".
- Keep "message_seed" low-pressure, specific, and under 240 characters.
- Keep "rationale" under 25 words.
"""


@dataclass(slots=True, frozen=True)
class WellbeingDecision:
    reach_out: bool
    when: str
    message_seed: str
    rationale: str
    model: str | None
    decision_input: dict[str, Any]


def build_wellbeing_input(
    *,
    user_id: str,
    source_kind: str,
    timezone: str,
    quiet_start: str,
    quiet_end: str,
    time_context: dict[str, Any],
    heartbeat_md: str,
    relationship: dict[str, Any] | None,
    open_loops: list[dict[str, Any]],
    recent_exchanges: list[dict[str, Any]],
    candidate: dict[str, Any],
    last_active_at: datetime | None,
    proactive_paused_until: datetime | None,
    outbound_count_24h: int,
    last_outbound_at: datetime | None,
    daily_cap: int,
    monthly_llm_tokens_used: int | None = None,
    monthly_llm_token_budget: int | None = None,
) -> dict[str, Any]:
    relationship_payload = dict(relationship or {})
    last_meaningful = relationship_payload.get("last_meaningful_exchange_at")
    if isinstance(last_meaningful, datetime):
        relationship_payload["last_meaningful_exchange_at"] = _iso(last_meaningful)

    return {
        "user_id": user_id,
        "source_kind": source_kind,
        "time_context": dict(time_context),
        "user_profile": {
            "timezone": timezone,
            "quiet_window": {"start": quiet_start, "end": quiet_end},
            "proactive_paused_until": _iso(proactive_paused_until),
            "last_active_at": _iso(last_active_at),
            "heartbeat_profile": heartbeat_md[:1200],
        },
        "relationship": relationship_payload,
        "open_loops": [dict(loop) for loop in open_loops[:5]],
        "recent_exchanges": [dict(exchange) for exchange in recent_exchanges[-3:]],
        "delivery_context": {
            "outbound_count_24h": outbound_count_24h,
            "last_outbound_at": _iso(last_outbound_at),
            "daily_cap": daily_cap,
            "monthly_llm_tokens_used": monthly_llm_tokens_used,
            "monthly_llm_token_budget": monthly_llm_token_budget,
        },
        "candidate": dict(candidate),
    }


async def reflect_on_wellbeing(
    *,
    settings: Settings,
    user_id: str,
    decision_input: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> WellbeingDecision:
    attributes = {"user_id": user_id, **(metadata or {})}
    async with start_span("wellbeing_reflection", attributes=attributes) as span:
        client = OpenRouterClient(settings)
        if not client.enabled:
            decision = WellbeingDecision(
                reach_out=False,
                when="hold",
                message_seed="",
                rationale="reflection unavailable",
                model=None,
                decision_input=decision_input,
            )
            _set_span_attributes(span, decision)
            return decision

        try:
            result = await client.chat_completion(
                messages=[
                    {"role": "system", "content": _system_prompt()},
                    {
                        "role": "user",
                        "content": (
                            "<observable_state>\n"
                            f"{json.dumps(decision_input, ensure_ascii=True)}\n"
                            "</observable_state>"
                        ),
                    },
                ],
                max_tokens=220,
                temperature=0.2,
                model=settings.openrouter_decision_model,
                metadata={"model_role": "wellbeing_reflection", **attributes},
            )
            parsed = json.loads(_strip_code_fences(result.content))
            decision = WellbeingDecision(
                reach_out=bool(parsed.get("reach_out")),
                when=_normalize_when(parsed.get("when"), reach_out=bool(parsed.get("reach_out"))),
                message_seed=str(parsed.get("message_seed") or "")[:240].strip(),
                rationale=str(parsed.get("rationale") or "reflection completed")[:160].strip()
                or "reflection completed",
                model=result.model,
                decision_input=decision_input,
            )
            if not decision.reach_out and decision.when != "hold":
                decision = WellbeingDecision(
                    reach_out=False,
                    when="hold",
                    message_seed="",
                    rationale=decision.rationale,
                    model=decision.model,
                    decision_input=decision_input,
                )
            _set_span_attributes(span, decision)
            return decision
        except Exception as exc:
            logger.warning("Wellbeing reflection failed for user %s: %s", user_id, exc)
            decision = WellbeingDecision(
                reach_out=False,
                when="hold",
                message_seed="",
                rationale="reflection error",
                model=None,
                decision_input=decision_input,
            )
            _set_span_attributes(span, decision)
            return decision


def parse_delay_minutes(when: str) -> int | None:
    match = _WHEN_DELAY_RE.match(when.strip())
    if match is None:
        return None
    minutes = int(match.group(1))
    return minutes if minutes > 0 else None


def _system_prompt() -> str:
    prompt_root = files("healthclaw.agent.prompts")
    modules = [
        prompt_root.joinpath(name).read_text(encoding="utf-8").strip()
        for name in _PROMPT_MODULES
    ]
    modules.append(_REFLECTION_SYSTEM_PROMPT.strip())
    return "\n\n---\n\n".join(modules)


def _strip_code_fences(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    parts = text.split("```")
    body = parts[1] if len(parts) > 1 else text
    if body.startswith("json"):
        body = body[4:]
    return body.strip()


def _normalize_when(value: object, *, reach_out: bool) -> str:
    raw = str(value or "").strip()
    if raw in {"now", "hold"}:
        return raw
    if parse_delay_minutes(raw) is not None:
        return raw
    return "now" if reach_out else "hold"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _set_span_attributes(span: Any, decision: WellbeingDecision) -> None:
    span.set_attribute("reach_out", decision.reach_out)
    span.set_attribute("when", decision.when)
    span.set_attribute("rationale", decision.rationale)
    if decision.model:
        span.set_attribute("model", decision.model)
