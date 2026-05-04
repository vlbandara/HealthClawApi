from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
    '{"message": str, "actions": [Action], "memory_proposals": [MemoryMutation], '
    '"safety_category": "normal"|"distress"|"crisis_escalated"}. '
    'Each action must use {"type": str, "payload": {...}, "rationale": str | null}. '
    "Known capabilities right now:\n"
    "  create_reminder: "
    '{"type":"create_reminder","payload":{"text":"<label>",'
    '"due_at_iso":"<ISO 8601 with tz>"},"rationale":"<why>"}\n'
    "  create_open_loop: "
    '{"type":"create_open_loop","payload":{"title":"<title>",'
    '"kind":"commitment"},"rationale":"<why>"}\n'
    "  close_open_loop: "
    '{"type":"close_open_loop","payload":{"id":"<exact id>","summary":"<one line>",'
    '"outcome":"completed"|"dropped"|"reframed"},"rationale":"<why>"}\n'
    "  log_metric: "
    '{"type":"log_metric","payload":{"metric":"sleep_hours"|"mood_1_5"|"steps"|"water_ml"|"weight_kg",'
    '"value":0.0,"observed_at_iso":"<ISO 8601>","note":null},"rationale":"<why>"}\n'
    "  schedule_protocol: "
    '{"type":"schedule_protocol","payload":{"title":"<title>",'
    '"kind":"sleep_protocol"|"nutrition_pattern"|"movement_routine"|"medication_schedule",'
    '"cadence":"daily"|"weekly"|"weekdays","time_local":"HH:MM"},"rationale":"<why>"}\n'
    "  web_search: "
    '{"type":"web_search","payload":{"query":"<search query>","health_clinical":false},'
    '"rationale":"<why I need fresh info>"}\n'
    "  set_user_timezone: "
    '{"type":"set_user_timezone","payload":{"tz":"<IANA tz e.g. Asia/Colombo>",'
    '"lat":null,"lon":null,"source":"user_stated","confidence":0.9},'
    '"rationale":"<why>"} — emit when the user tells you their city/country/timezone\n'
    "  open_topic: "
    '{"type":"open_topic","payload":{"title":"<suggestion made>","kind":"nudge",'
    '"cooldown_hours":12,"max_surfaces":2},"rationale":"<why>"} — '
    "emit when you make a suggestion the user should follow up on; "
    "NEVER re-surface the same topic if it is already in Open Loops or marked cooled\n"
    '  none: {"type":"none","payload":{},"rationale":null}\n'
    "safety_category: Set to 'crisis_escalated' when you sense serious distress or crisis. "
    "When crisis_escalated, include crisis support resource in message and emit no other actions.\n"
    "If you want a capability that does not exist yet, "
    "still propose it with a clear type and payload. "
    "Only say you set, scheduled, or created something "
    "when the matching action appears in actions. "
    "If timing/details are uncertain, ask the user instead of guessing."
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


async def generate_companion_response(
    user_content: str,
    time_context: TimeContext,
    memories: list[MemoryLike],
    soul_preferences: dict[str, object] | None = None,
    open_loops: list[dict[str, object]] | None = None,
    streaks: list[dict[str, object]] | None = None,
    recent_messages: list[dict[str, object]] | None = None,
    memory_documents: dict[str, str] | None = None,
    user_context: dict[str, object] | None = None,
    observable_signals: dict[str, object] | None = None,
    thread_summary: str | None = None,
    relationship_signals: list[str] | None = None,
    active_skills: list[object] | None = None,
    web_search_results: list[dict[str, object]] | None = None,
    motives: list[dict[str, object]] | None = None,
) -> tuple[GenerationResult, dict[str, object]]:
    user_context = user_context or {}
    observable_signals = observable_signals or {}
    streaks = streaks or []
    streaks_surfaced = bool(streaks)

    settings = get_settings()
    client = OpenRouterClient(settings)
    if not client.enabled:
        return _offline_generation("openrouter_not_configured")

    memory_context = "\n".join(_memory_lines(memories)) or "- none"
    observable_block = _observable_signals_block(
        user_context,
        time_context,
        observable_signals=observable_signals,
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

    # Build skill prompt block
    active_skills = active_skills or []
    skill_block = ""
    if active_skills:
        from healthclaw.agent.skills.base import load_prompt_module
        skill_sections = []
        for skill in active_skills:
            module_text = load_prompt_module(skill.prompt_module_path)
            if module_text:
                heading = f"## {skill.name.replace('_', ' ').title()} Skill"
                skill_sections.append(f"{heading}\n\n{module_text}")
        if skill_sections:
            skill_block = "\n\n# Active Health Skills\n\n" + "\n\n---\n\n".join(skill_sections)

    # Build web sources block
    web_search_results = web_search_results or []
    web_sources_block = ""
    if web_search_results:
        lines = []
        for i, src in enumerate(web_search_results[:5], 1):
            title = src.get("title", "")
            snippet = src.get("snippet", "")[:200]
            url = src.get("url", "")
            lines.append(f"[{i}] {title} — {snippet} ({url})")
        web_sources_block = (
            "\n\n# Web Sources\n\nUse inline [n] markers when citing these sources.\n\n"
            + "\n".join(lines)
        )

    messages = [
        {
            "role": "system",
            "content": system_prompt(
                soul_preferences,
                user_id=str(runtime_context["user_id"]),
                timezone=str(runtime_context["timezone"]),
                local_time=time_context.to_dict(),
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
                safety_category="model_managed",
                time_truth_block=time_context.time_truth_block() or None,
            )
            + skill_block
            + web_sources_block
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
                f"{observable_block}\n\n"
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
        return _offline_generation("openrouter_error")
    generation_result, parse_error = _parse_generation_payload(result.content)

    # WS7: Style guardrails — single regen if violations found
    style_violations: list[str] = []
    if settings.style_guardrails_enabled and generation_result.message:
        style_violations = _check_style_violations(
            generation_result.message,
            user_content,
            recent_messages or [],
        )
        if style_violations:
            regen_messages = _build_regen_messages(messages, style_violations)
            try:
                regen_result = await client.chat_completion(
                    regen_messages,
                    max_tokens=settings.openrouter_chat_max_tokens,
                    temperature=settings.openrouter_chat_temperature,
                    metadata={**metadata, "regen": "style_guardrail"},
                )
                regen_gen, regen_parse_error = _parse_generation_payload(regen_result.content)
                if regen_gen.message:
                    generation_result = regen_gen
                    parse_error = regen_parse_error
            except Exception:
                pass  # Keep original on regen failure

    return (
        generation_result,
        {
            "provider": "openrouter",
            "model": result.model,
            "usage": result.usage,
            "recent_messages_used": len(recent_lines),
            "conversation_digest_used": bool(conversation_digest),
            "streaks_surfaced": streaks_surfaced,
            "actions.parse_error": parse_error,
            "style_violations": style_violations,
        },
    )


# ── WS7: Style guardrails ─────────────────────────────────────────────────────

_BANNED_OPENERS = (
    "alright,",
    "okay,",
    "sure,",
    "got it,",
    "of course,",
    "good to hear from you",
    "good morning",
    "good evening",
    "good afternoon",
    "good night",
)

_BREVITY_REGEN_INSTRUCTION = (
    "\n\n[STYLE CORRECTION: The user's last message was very short. "
    "Please keep your reply to 1-2 short sentences. No questions.]"
)

_OPENER_REGEN_INSTRUCTION = (
    "\n\n[STYLE CORRECTION: Your reply started with a banned filler phrase. "
    "Rewrite it — start directly with the substance. "
    "Never begin with: Alright, Okay, Sure, Got it, Of course, Good to hear from you, "
    "Good morning, Good evening, Good afternoon.]"
)

_Q_STACK_REGEN_INSTRUCTION = (
    "\n\n[STYLE CORRECTION: Your reply contained more than one question mark. "
    "Keep at most one question per reply. Rewrite with at most one question.]"
)


def _check_style_violations(
    message: str,
    user_content: str,
    recent_messages: list[dict[str, object]],
) -> list[str]:
    """Return list of style violation codes found in the generated message."""
    violations: list[str] = []
    if not message:
        return violations

    # Check banned opener (startswith check, no regex)
    first_chars = message.lstrip()[:40].lower()
    for banned in _BANNED_OPENERS:
        if first_chars.startswith(banned):
            violations.append("banned_opener")
            break

    # Check Q-stacking (only mechanical ? count — not semantic)
    if message.count("?") > 1:
        violations.append("q_stack")

    # Check brevity: if user message is ≤ 3 words, reply should be ≤ 280 chars
    user_word_count = len(user_content.split())
    if user_word_count <= 3 and len(message) > 280:
        violations.append("brevity")

    return violations


def _build_regen_messages(
    original_messages: list[dict[str, object]],
    violations: list[str],
) -> list[dict[str, object]]:
    """Append correction instructions to the last user message for a single regen."""
    instruction_parts: list[str] = []
    if "banned_opener" in violations:
        instruction_parts.append(_OPENER_REGEN_INSTRUCTION.strip())
    if "q_stack" in violations:
        instruction_parts.append(_Q_STACK_REGEN_INSTRUCTION.strip())
    if "brevity" in violations:
        instruction_parts.append(_BREVITY_REGEN_INSTRUCTION.strip())

    if not instruction_parts:
        return original_messages

    regen = list(original_messages)
    last_user = next(
        (i for i in reversed(range(len(regen))) if regen[i]["role"] == "user"),
        None,
    )
    if last_user is not None:
        regen[last_user] = {
            **regen[last_user],
            "content": str(regen[last_user]["content"]) + "\n\n" + "\n".join(instruction_parts),
        }
    return regen


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


def _offline_generation(reason: str) -> tuple[GenerationResult, dict[str, object]]:
    return (
        GenerationResult(
            message="I'm offline for a moment — try again in a bit.",
            actions=[],
            memory_proposals=[],
        ),
        {
            "provider": "offline",
            "reason": reason,
            "streaks_surfaced": False,
        },
    )


def _observable_signals_block(
    user_context: dict[str, object],
    time_context: TimeContext,
    *,
    observable_signals: dict[str, object] | None = None,
    relationship_signals: list[str] | None = None,
) -> str:
    observable_signals = observable_signals or {}
    last_meaningful_hours = _hours_since_exchange(
        user_context.get("last_meaningful_exchange_at"),
        time_context,
    )
    lines = [
        "- sentiment_ema="
        + _format_signal_value(_float_or_none(user_context.get("sentiment_ema"))),
        "- voice_text_ratio="
        + _format_signal_value(_float_or_none(user_context.get("voice_text_ratio"))),
        (
            "- reply_latency_hours="
            f"{_format_signal_value(_seconds_to_hours(user_context.get('reply_latency_seconds_ema')))}"
        ),
        f"- last_meaningful_exchange_hours_ago={_format_signal_value(last_meaningful_hours)}",
        f"- part_of_day={time_context.part_of_day}",
        f"- quiet_hours={str(time_context.quiet_hours).lower()}",
        f"- interaction_gap_days={_format_signal_value(time_context.interaction_gap_days)}",
        f"- long_lapse={str(time_context.long_lapse).lower()}",
        f"- message_length={_format_signal_value(observable_signals.get('message_length'))}",
        f"- content_type={observable_signals.get('content_type', 'unknown')}",
        f"- is_voice={str(bool(observable_signals.get('is_voice'))).lower()}",
        f"- has_attachments={str(bool(observable_signals.get('has_attachments'))).lower()}",
        f"- attachment_count={_format_signal_value(observable_signals.get('attachment_count'))}",
        (
            "- transcription_uncertain="
            f"{str(bool(observable_signals.get('transcription_uncertain'))).lower()}"
        ),
    ]
    for signal in relationship_signals or []:
        lines.append(f"- note={signal}")
    return "\n<observable_signals>\n" + "\n".join(lines) + "\n</observable_signals>"


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _seconds_to_hours(value: object) -> float | None:
    seconds = _float_or_none(value)
    return None if seconds is None else round(seconds / 3600, 2)


def _format_signal_value(value: object) -> str:
    if value is None:
        return "unknown"
    return str(value)


def _hours_since_exchange(value: object, time_context: TimeContext) -> float | None:
    if not isinstance(value, datetime):
        return None
    meaningful_at = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    local_now = datetime.fromisoformat(time_context.local_datetime).astimezone(UTC)
    return round((local_now - meaningful_at.astimezone(UTC)) / timedelta(hours=1), 2)
