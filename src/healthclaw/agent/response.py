from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from healthclaw.agent.safety import SafetyDecision
from healthclaw.agent.soul import system_prompt
from healthclaw.agent.time_context import TimeContext
from healthclaw.core.config import get_settings
from healthclaw.integrations.openrouter import OpenRouterClient

MemoryLike = dict[str, object]


@dataclass(frozen=True)
class GenerationResult:
    message: str
    actions: list[dict[str, object]]
    memory_proposals: list[dict[str, object]]


ACTION_OUTPUT_CONTRACT = (
    "Output a single JSON object with exactly these keys: "
    '{"message": str, "actions": [Action], "memory_proposals": [MemoryMutation]}. '
    "Allowed action types and their required fields:\n"
    "  create_reminder: "
    '{"type":"create_reminder","text":"<label>","due_at_iso":"<ISO 8601 with tz>"}\n'
    "  create_open_loop: "
    '{"type":"create_open_loop","title":"<title>","kind":"commitment"}\n'
    "  close_open_loop: "
    '{"type":"close_open_loop","id":"<exact id>","summary":"<one line>",'
    '"outcome":"completed"|"dropped"|"reframed"}\n'
    '  none: {"type":"none"}\n'
    "Only say you set, scheduled, or created something "
    "when the matching action appears in actions. "
    "If timing/details are uncertain, ask the user instead of guessing."
)


def _deterministic_companion_response(
    user_content: str,
    safety: SafetyDecision,
    time_context: TimeContext,
    memories: list[MemoryLike],
) -> str:
    if safety.category == "crisis":
        return (
            "I am really sorry this is where things are right now. I cannot be your "
            "emergency support, but this is urgent: contact local emergency services "
            "now, or reach a trusted person who can stay with you. If you are in the "
            "US, call or text 988. If you are elsewhere, use your local crisis line "
            "or emergency number. Send one short message now: 'I am not safe alone.'"
        )

    if safety.category == "medical_boundary":
        return (
            "I can support the behavior side, but I cannot diagnose, treat, or advise "
            "on medication or injury decisions. If this feels severe, sudden, or "
            "unusual, contact a qualified clinician or urgent care. For right now, "
            "keep the next step simple: pause, note what changed, and avoid pushing "
            "training or recovery decisions until you have proper guidance."
        )

    remembered_goal = next(
        (
            memory_value(memory).get("text")
            for memory in memories
            if memory.get("key") == "current_goal"
        ),
        None,
    )
    prefix = {
        "morning": "Good morning. Set the day up with one small move. ",
        "afternoon": "",
        "evening": "For this evening, keep the focus on review and wind-down. ",
        "night": "Since it is late, keep this low-effort and low-stimulation. ",
        "late_night": "This is a late-night check-in, so keep it light. ",
    }.get(time_context.part_of_day, "")
    lapse = (
        "It has been a while since the last check-in, so there is no need to recap everything. "
        if time_context.long_lapse
        else ""
    )
    goal_line = f"Keep the focus on {remembered_goal}. " if remembered_goal else ""
    quiet = (
        "I will avoid proactive follow-ups during your quiet hours. "
        if time_context.quiet_hours
        else ""
    )
    return (
        f"{prefix}{lapse}{goal_line}{quiet}"
        "Pick one next step that is small enough to do today. Make it concrete: set "
        "the next cutoff, prepare the room, choose the first 10 minutes of the "
        "routine, or scale training down if recovery is the priority."
    )


def memory_value(memory: MemoryLike) -> dict[str, object]:
    value = memory.get("value")
    return value if isinstance(value, dict) else {}


def _memory_lines(memories: list[MemoryLike]) -> list[str]:
    lines: list[str] = []
    for memory in memories[:12]:
        value = memory_value(memory)
        text = value.get("text") or value.get("summary") or value
        lines.append(f"- {memory.get('kind')}:{memory.get('key')} = {text}")
    return lines


def _recent_conversation_lines(
    recent_messages: list[dict[str, object]],
    *,
    limit: int,
    max_chars: int,
) -> list[str]:
    lines: list[str] = []
    total_chars = 0
    for message in recent_messages[-limit:]:
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        line = f"- {role}: {content[:800]}"
        next_total = total_chars + len(line)
        if lines and next_total > max_chars:
            break
        lines.append(line)
        total_chars = next_total
    return lines


def _lifecycle_stage(recent_messages: list[dict[str, object]]) -> str:
    message_count = len(recent_messages)
    if message_count < 8:
        return "onboarding"
    if message_count < 30:
        return "early"
    return "settling"


async def generate_companion_response(
    user_content: str,
    safety: SafetyDecision,
    time_context: TimeContext,
    memories: list[MemoryLike],
    soul_preferences: dict[str, object] | None = None,
    bridges: list[str] | None = None,
    open_loops: list[dict[str, object]] | None = None,
    streaks: list[dict[str, object]] | None = None,
    recent_messages: list[dict[str, object]] | None = None,
    memory_documents: dict[str, str] | None = None,
    user_context: dict[str, object] | None = None,
    safety_category: str | None = None,
    thread_summary: str | None = None,
    relationship_signals: list[str] | None = None,
) -> tuple[GenerationResult, dict[str, object]]:
    user_context = user_context or {}
    safety_category = safety_category or safety.category
    streaks = streaks or []
    streaks_surfaced = bool(streaks)

    if safety.category != "wellness":
        return (
            GenerationResult(
                message=_deterministic_companion_response(
                    user_content, safety, time_context, memories
                ),
                actions=[],
                memory_proposals=[],
            ),
            {
                "provider": "deterministic",
                "reason": safety.category,
                "streaks_surfaced": False,
            },
        )

    settings = get_settings()
    client = OpenRouterClient(settings)
    if not client.enabled:
        return (
            GenerationResult(
                message=_deterministic_companion_response(
                    user_content, safety, time_context, memories
                ),
                actions=[],
                memory_proposals=[],
            ),
            {
                "provider": "deterministic",
                "reason": "openrouter_not_configured",
                "streaks_surfaced": False,
            },
        )

    memory_context = "\n".join(_memory_lines(memories)) or "- none"

    # Continuity bridges as a tagged block in the user turn (keeps system prompt cacheable)
    bridge_block = ""
    if bridges:
        bridge_text = "\n".join(f"- {b}" for b in bridges)
        bridge_block = f"\n<recent_life_bridge>\n{bridge_text}\n</recent_life_bridge>"
    relationship_block = _relationship_signal_block(
        user_context,
        time_context,
        relationship_signals=relationship_signals,
    )
    open_loop_lines = []
    for loop in (open_loops or [])[:10]:
        if not loop.get("title"):
            continue
        open_loop_lines.append(
            "- "
            f"id={loop.get('id')} | "
            f"title={loop.get('title')} | "
            f"kind={loop.get('kind')} | "
            f"age_hours={loop.get('age_hours')}"
        )
    open_loop_context = "\n".join(open_loop_lines) or "- none"

    recent_messages = recent_messages or []
    recent_lines = _recent_conversation_lines(
        recent_messages,
        limit=settings.recent_message_context_limit,
        max_chars=settings.recent_message_context_max_chars,
    )
    recent_context = "\n".join(recent_lines) or "- none"
    conversation_digest = (thread_summary or "").strip()
    runtime_context = {
        "user_id": user_context.get("id", "unknown"),
        "timezone": user_context.get("timezone", "unknown"),
        "local_time": time_context.to_dict(),
    }

    messages = [
        {
            "role": "system",
            "content": system_prompt(
                soul_preferences,
                user_id=str(runtime_context["user_id"]),
                timezone=str(runtime_context["timezone"]),
                local_time=time_context.to_dict(),
                lifecycle_stage=_lifecycle_stage(recent_messages),
                recent_message_count=len(recent_messages),
                memory_documents=memory_documents,
                trust_level=_trust_level(user_context),
                sentiment_ema=_float_or_none(user_context.get("sentiment_ema")),
                voice_text_ratio=_float_or_none(user_context.get("voice_text_ratio")),
                reply_latency_seconds_ema=_float_or_none(
                    user_context.get("reply_latency_seconds_ema")
                ),
                streaks=streaks,
                open_loops=open_loops,
                safety_category=safety_category,
            )
            + "\n\n# Action Output Contract\n\n"
            + ACTION_OUTPUT_CONTRACT,
        },
        {
            "role": "user",
            "content": (
                "# Runtime Context\n\n"
                f"{runtime_context}\n\n"
                "# Retrieved Memory\n\n"
                f"{memory_context}\n\n"
                "# Conversation Digest\n\n"
                f"{conversation_digest or '- none'}\n\n"
                "# Recent Conversation\n\n"
                f"{recent_context}"
                f"{bridge_block}"
                f"{relationship_block}\n\n"
                "# Open Loops\n\n"
                f"{open_loop_context}\n\n"
                "# Current User Message\n\n"
                f"{user_content}"
            ),
        },
    ]
    metadata = {
        "model_role": "chat",
        "node": "companion_response",
        "user_id": str(runtime_context["user_id"]),
    }
    try:
        result = await client.chat_completion(
            messages,
            max_tokens=settings.openrouter_chat_max_tokens,
            temperature=settings.openrouter_chat_temperature,
            metadata=metadata,
        )
    except RuntimeError:
        return (
            GenerationResult(
                message=_deterministic_companion_response(
                    user_content, safety, time_context, memories
                ),
                actions=[],
                memory_proposals=[],
            ),
            {
                "provider": "deterministic",
                "reason": "openrouter_error",
                "streaks_surfaced": False,
            },
        )
    generation_result, parse_error = _parse_generation_payload(result.content)
    return (
        generation_result,
        {
            "provider": "openrouter",
            "model": result.model,
            "usage": result.usage,
            "bridges_used": len(bridges) if bridges else 0,
            "recent_messages_used": len(recent_lines),
            "conversation_digest_used": bool(conversation_digest),
            "streaks_surfaced": streaks_surfaced,
            "actions.parse_error": parse_error,
        },
    )


def _parse_generation_payload(raw_content: str) -> tuple[GenerationResult, bool]:
    normalized = _strip_json_fence(raw_content.strip())
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return GenerationResult(message=raw_content.strip(), actions=[], memory_proposals=[]), True
    if not isinstance(payload, dict):
        return GenerationResult(message=raw_content.strip(), actions=[], memory_proposals=[]), True
    message = payload.get("message")
    actions = payload.get("actions")
    memory_proposals = payload.get("memory_proposals")
    return (
        GenerationResult(
            message=str(message or raw_content).strip(),
            actions=actions if isinstance(actions, list) else [],
            memory_proposals=memory_proposals if isinstance(memory_proposals, list) else [],
        ),
        False,
    )


def _strip_json_fence(content: str) -> str:
    if not content.startswith("```"):
        return content
    body = content
    if body.startswith("```json"):
        body = body[len("```json") :]
    elif body.startswith("```JSON"):
        body = body[len("```JSON") :]
    else:
        body = body[3:]
    if body.endswith("```"):
        body = body[:-3]
    return body.strip()


def _trust_level(user_context: dict[str, object]) -> float | None:
    value = user_context.get("trust_level")
    return float(value) if isinstance(value, int | float) else None


def _relationship_signal_block(
    user_context: dict[str, object],
    time_context: TimeContext,
    *,
    relationship_signals: list[str] | None = None,
) -> str:
    lines = list(relationship_signals or [])
    if not lines:
        sentiment_ema = _float_or_none(user_context.get("sentiment_ema"))
        voice_text_ratio = _float_or_none(user_context.get("voice_text_ratio"))
        reply_latency = _float_or_none(user_context.get("reply_latency_seconds_ema"))
        if sentiment_ema is not None and sentiment_ema <= -0.35:
            lines.append("Use lower-pressure phrasing, no cheerleading, and smaller next steps.")
        if voice_text_ratio is not None and voice_text_ratio >= 0.65:
            lines.append("Favor concise spoken-style phrasing that reads naturally out loud.")
        if reply_latency is not None and reply_latency >= 43_200:
            lines.append("Do not frame slow re-entry or delayed replies as failure.")
        if _is_recent_meaningful_exchange(
            user_context.get("last_meaningful_exchange_at"),
            time_context,
        ):
            lines.append("Brief continuity references are safe without asking for a full recap.")
    if not lines:
        return ""
    relationship_text = "\n".join(f"- {line}" for line in lines)
    return f"\n<relationship_signals>\n{relationship_text}\n</relationship_signals>"


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _is_recent_meaningful_exchange(value: object, time_context: TimeContext) -> bool:
    if not isinstance(value, datetime):
        return False
    meaningful_at = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    local_now = datetime.fromisoformat(time_context.local_datetime).astimezone(UTC)
    return local_now - meaningful_at.astimezone(UTC) <= timedelta(hours=24)
