from __future__ import annotations

from datetime import UTC, datetime

from pydantic import TypeAdapter, ValidationError

from healthclaw.agent.response import generate_companion_response
from healthclaw.agent.state import AgentState
from healthclaw.agent.time_context import TimeContext, build_time_context
from healthclaw.core.tracing import traced_node
from healthclaw.engagement.metrics import is_meaningful_exchange
from healthclaw.memory.extractors import extract_memory_mutations_enriched
from healthclaw.schemas.actions import (
    Action,
    CloseOpenLoopPayload,
    CreateOpenLoopPayload,
    CreateReminderPayload,
)

ACTION_ADAPTER = TypeAdapter(Action)


@traced_node("input_normalization")
async def normalize_input(state: AgentState) -> AgentState:
    state["user_content"] = " ".join(state["user_content"].strip().split())
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "input_normalization"],
    }
    return state


@traced_node("assemble_signals")
async def assemble_signals(state: AgentState) -> AgentState:
    user_message = state.get("user_message", {})
    content_type = str(user_message.get("content_type") or "text")
    attachments = user_message.get("attachments")
    attachment_count = (
        len(attachments)
        if isinstance(attachments, list)
        else int(bool(attachments))
        if attachments is not None
        else 0
    )
    state["observable_signals"] = {
        "message_length": len(state["user_content"]),
        "content_type": content_type,
        "is_voice": content_type == "voice_transcript",
        "attachment_count": attachment_count,
        "has_attachments": attachment_count > 0,
        "transcription_uncertain": bool(user_message.get("transcription_uncertain")),
    }
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "assemble_signals"],
        "observable_signals": state["observable_signals"],
    }
    return state


@traced_node("time_context")
async def assemble_time_context(state: AgentState) -> AgentState:
    thread_last = state.get("trace_metadata", {}).get("last_interaction_at")

    # WS5: inject calendar events and rhythm memory when available in state
    calendar_events = state.get("calendar_events")
    rhythm_memory = state.get("rhythm_memory")

    context = build_time_context(
        state["user"],
        last_interaction_at=thread_last,
        calendar_events=calendar_events,
        rhythm_memory=rhythm_memory,
    )
    state["time_context"] = context.to_dict()
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "time_context"],
    }
    return state


@traced_node("memory_retrieval")
async def retrieve_memory(state: AgentState) -> AgentState:
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "memory_retrieval"],
        "memory_keys": [
            f"{memory['kind']}:{memory['key']}" for memory in state.get("memories", [])[:12]
        ],
    }
    return state


@traced_node("companion_response")
async def generate_response(state: AgentState) -> AgentState:
    time_ctx = TimeContext(**state["time_context"])

    generation, metadata = await generate_companion_response(
        user_content=state["user_content"],
        time_context=time_ctx,
        memories=state.get("memories", []),
        soul_preferences=state.get("soul_preferences", {}),
        open_loops=state.get("open_loops", []),
        streaks=state.get("streaks", []),
        recent_messages=state.get("recent_messages", []),
        memory_documents=state.get("memory_documents", {}),
        user_context=state.get("user", {}),
        observable_signals=state.get("observable_signals", {}),
        thread_summary=state.get("thread_summary"),
        relationship_signals=state.get("relationship_signals"),
    )
    state["response"] = generation.message
    state["actions"] = list(generation.actions)
    state["memory_proposals"] = list(generation.memory_proposals)
    state["trace_metadata"] = {**state.get("trace_metadata", {}), "generation": metadata}
    state["trace_metadata"]["nodes"] = [
        *state["trace_metadata"].get("nodes", []),
        "companion_response",
    ]
    return state


@traced_node("proactive_policy")
async def decide_proactivity(state: AgentState) -> AgentState:
    observable_signals = state.get("observable_signals", {})
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "wellbeing_signals": {
            "quiet_hours": state["time_context"]["quiet_hours"],
            "part_of_day": state["time_context"]["part_of_day"],
            "interaction_gap_days": state["time_context"].get("interaction_gap_days"),
            "message_length": observable_signals.get("message_length"),
            "content_type": observable_signals.get("content_type"),
            "has_attachments": observable_signals.get("has_attachments"),
        },
    }
    state["trace_metadata"]["nodes"] = [
        *state["trace_metadata"].get("nodes", []),
        "proactive_policy",
    ]
    return state


@traced_node("execute_actions")
async def execute_actions(state: AgentState) -> AgentState:
    raw_actions = state.get("actions", [])
    actions_taken: list[dict] = []
    dropped: list[dict[str, str]] = []

    for raw_action in raw_actions:
        try:
            normalized = _normalize_action_input(raw_action)
            action = ACTION_ADAPTER.validate_python(normalized)
        except ValidationError:
            dropped.append({"reason": "validation_error"})
            continue

        action_data = action.model_dump(mode="json")
        action_type = str(action_data.get("type") or "")
        payload = action_data.get("payload") if isinstance(action_data.get("payload"), dict) else {}
        if action_type == "create_reminder":
            try:
                reminder = CreateReminderPayload.model_validate(payload)
            except ValidationError:
                dropped.append({"type": action_type, "reason": "payload_invalid"})
                continue
            due_at_iso = str(reminder.due_at_iso or "")
            try:
                parsed = datetime.fromisoformat(due_at_iso.replace("Z", "+00:00"))
                parsed = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            except ValueError:
                dropped.append({"type": action_type, "reason": "due_at_invalid"})
                continue
            payload = {
                **reminder.model_dump(mode="json"),
                "due_at_iso": parsed.astimezone(UTC).isoformat(),
            }
        elif action_type == "create_open_loop":
            try:
                open_loop = CreateOpenLoopPayload.model_validate(payload)
            except ValidationError:
                dropped.append({"type": action_type, "reason": "payload_invalid"})
                continue
            due_after_iso = open_loop.due_after_iso
            if isinstance(due_after_iso, str) and due_after_iso:
                try:
                    parsed = datetime.fromisoformat(due_after_iso.replace("Z", "+00:00"))
                    parsed = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
                    payload = {
                        **open_loop.model_dump(mode="json"),
                        "due_after_iso": parsed.astimezone(UTC).isoformat(),
                    }
                except ValueError:
                    payload = {**open_loop.model_dump(mode="json"), "due_after_iso": None}
            else:
                payload = open_loop.model_dump(mode="json")
        elif action_type == "close_open_loop":
            try:
                payload = CloseOpenLoopPayload.model_validate(payload).model_dump(mode="json")
            except ValidationError:
                dropped.append({"type": action_type, "reason": "payload_invalid"})
                continue

        if action_type != "none":
            normalized_action = {
                "type": action_type,
                "payload": payload,
                "rationale": action_data.get("rationale"),
            }
            actions_taken.append(normalized_action)
            if action_type in {"create_reminder", "create_open_loop", "close_open_loop"}:
                state.setdefault("memory_mutations", []).append(
                    {
                        "kind": "commitment",
                        "key": f"action:{action_type}",
                        "value": {"text": _action_memory_text(action_type, payload)},
                        "confidence": 0.72,
                        "reason": "Action channel execution.",
                        "layer": "open_loop",
                        "metadata": {"skip_open_loop_creation": True},
                    }
                )

    action_types = [str(a.get("type") or "") for a in actions_taken]

    state["actions_taken"] = actions_taken
    trace_metadata = {**state.get("trace_metadata", {})}
    trace_metadata["action_execution"] = {
        "action_count": len(actions_taken),
        "action_types": ",".join(action_types),
        "dropped": dropped,
    }
    state["trace_metadata"] = trace_metadata
    state["trace_metadata"]["nodes"] = [
        *state["trace_metadata"].get("nodes", []),
        "execute_actions",
    ]
    return state


@traced_node("memory_update")
async def update_memory(state: AgentState) -> AgentState:
    extracted_mutations = [
        mutation.model_dump(mode="json")
        for mutation in await extract_memory_mutations_enriched(state["user_content"])
    ]
    state["memory_mutations"] = [
        *state.get("memory_mutations", []),
        *state.get("memory_proposals", []),
        *extracted_mutations,
    ]
    content = state["user_content"]
    content_type = state.get("trace_metadata", {}).get("content_type", "text")
    is_command = state.get("user_message", {}).get("is_command", False)
    if len(content) >= 40 and is_meaningful_exchange(
        content, content_type=content_type, is_command=is_command
    ):
        prior_episode_prefix = None
        for mem in state.get("memories", []):
            if mem.get("kind") == "episode" and mem.get("key") == "latest_check_in":
                prior_summary = mem.get("value", {}).get("summary", "") or ""
                prior_episode_prefix = prior_summary[:200]
                break
        current_prefix = content[:200]
        if prior_episode_prefix != current_prefix:
            state["memory_mutations"].append(
                {
                    "kind": "episode",
                    "key": "latest_check_in",
                    "layer": "episode",
                    "value": {"summary": content[:500]},
                    "confidence": 0.55,
                    "reason": "Preserve recent episode for continuity.",
                    "visibility": "internal",
                    "user_editable": False,
                }
            )
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "memory_update"],
        "memory_mutation_count": len(state["memory_mutations"]),
    }
    return state


def _action_memory_text(action_type: str, payload: dict[str, object]) -> str:
    if action_type == "create_reminder":
        text = payload.get("text", "")
        due = payload.get("due_at_iso", "")
        return f"reminder: {text} at {due}".strip()
    if action_type == "create_open_loop":
        return f"open loop: {payload.get('title', '')}".strip()
    if action_type == "close_open_loop":
        loop_id = payload.get("id", "")
        summary = payload.get("summary", "")
        outcome = payload.get("outcome", "")
        return f"closed loop ({outcome}): {loop_id} - {summary}".strip()
    return action_type


def _normalize_action_input(raw_action: object) -> dict[str, object]:
    if not isinstance(raw_action, dict):
        return {}
    payload = raw_action.get("payload")
    if not isinstance(payload, dict):
        payload = {
            str(key): value
            for key, value in raw_action.items()
            if key not in {"type", "rationale", "payload"}
        }
    rationale = raw_action.get("rationale")
    return {
        "type": str(raw_action.get("type") or ""),
        "payload": payload,
        "rationale": str(rationale) if rationale is not None else None,
    }


@traced_node("trace_eval_logging")
async def log_trace(state: AgentState) -> AgentState:
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "trace_eval_logging"],
        "trace_logged": True,
    }
    return state
