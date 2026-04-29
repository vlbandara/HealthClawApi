from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import TypeAdapter, ValidationError

from healthclaw.agent.continuity import build_bridges
from healthclaw.agent.response import generate_companion_response
from healthclaw.agent.safety import classify_safety
from healthclaw.agent.state import AgentState
from healthclaw.agent.time_context import TimeContext, build_time_context
from healthclaw.core.tracing import traced_node
from healthclaw.engagement.metrics import is_meaningful_exchange
from healthclaw.memory.extractors import extract_memory_mutations_enriched
from healthclaw.schemas.actions import Action

ACTION_ADAPTER = TypeAdapter(Action)
REMINDER_CLAIM_RE = re.compile(
    r"\b(reminder|alarm|set for|i'?ll remind|i set|scheduled"
    r"|i (?:created|made|added).*?(?:reminder|alarm))\b",
    flags=re.I,
)


@traced_node("input_normalization")
async def normalize_input(state: AgentState) -> AgentState:
    state["user_content"] = " ".join(state["user_content"].strip().split())
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "input_normalization"],
    }
    return state


@traced_node("safety_and_scope")
async def classify_scope_and_safety(state: AgentState) -> AgentState:
    safety = classify_safety(state["user_content"])
    state["safety"] = {
        "category": safety.category,
        "severity": safety.severity,
        "action": safety.action,
    }
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "safety_and_scope"],
        "safety_category": state["safety"]["category"],
    }
    return state


@traced_node("time_context")
async def assemble_time_context(state: AgentState) -> AgentState:
    thread_last = state.get("trace_metadata", {}).get("last_interaction_at")
    context = build_time_context(state["user"], last_interaction_at=thread_last)
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
    safety = classify_safety(state["user_content"])
    time_ctx = TimeContext(**state["time_context"])

    # Compute continuity bridges before calling the LLM
    bridges = build_bridges(
        time_context=time_ctx,
        memories=state.get("memories", []),
        open_loops=state.get("open_loops", []),
        safety_category=safety.category,
    )
    state["bridges"] = bridges

    generation, metadata = await generate_companion_response(
        user_content=state["user_content"],
        safety=safety,
        time_context=time_ctx,
        memories=state.get("memories", []),
        soul_preferences=state.get("soul_preferences", {}),
        bridges=bridges,
        open_loops=state.get("open_loops", []),
        streaks=state.get("streaks", []),
        recent_messages=state.get("recent_messages", []),
        memory_documents=state.get("memory_documents", {}),
        user_context=state.get("user", {}),
        safety_category=state.get("safety", {}).get("category"),
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
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "proactive_candidate": not state["time_context"]["quiet_hours"]
        and state["safety"]["category"] == "wellness",
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
            action = ACTION_ADAPTER.validate_python(raw_action)
        except ValidationError:
            dropped.append({"reason": "validation_error"})
            continue

        action_data = action.model_dump(mode="json")
        action_type = str(action_data.get("type") or "")
        if action_type == "create_reminder":
            due_at_iso = str(action_data.get("due_at_iso") or "")
            try:
                parsed = datetime.fromisoformat(due_at_iso.replace("Z", "+00:00"))
                parsed = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            except ValueError:
                dropped.append({"type": "create_reminder", "reason": "due_at_invalid"})
                continue
            action_data["due_at_iso"] = parsed.astimezone(UTC).isoformat()
        elif action_type == "create_open_loop":
            due_after_iso = action_data.get("due_after_iso")
            if isinstance(due_after_iso, str) and due_after_iso:
                try:
                    parsed = datetime.fromisoformat(due_after_iso.replace("Z", "+00:00"))
                    parsed = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
                    action_data["due_after_iso"] = parsed.astimezone(UTC).isoformat()
                except ValueError:
                    action_data["due_after_iso"] = None

        if action_type != "none":
            actions_taken.append(action_data)
            state.setdefault("memory_mutations", []).append(
                {
                    "kind": "commitment",
                    "key": f"action:{action_type}",
                    "value": {"text": _action_memory_text(action_data)},
                    "confidence": 0.72,
                    "reason": "Action channel execution.",
                    "layer": "open_loop",
                    "metadata": {"skip_open_loop_creation": True},
                }
            )

    action_types = [str(a.get("type") or "") for a in actions_taken]
    consistency = "ok"
    safety_category = str(state.get("safety", {}).get("category") or "")
    if (
        REMINDER_CLAIM_RE.search(state.get("response", ""))
        and "create_reminder" not in action_types
        and safety_category not in {"crisis", "medical", "medical_boundary"}
    ):
        state["response"] = (
            f"{state.get('response', '').strip()} Want me to actually set a reminder for that?"
        )
        consistency = "patched"

    state["actions_taken"] = actions_taken
    trace_metadata = {**state.get("trace_metadata", {})}
    trace_metadata["action_execution"] = {
        "action_count": len(actions_taken),
        "action_types": ",".join(action_types),
        "action.consistency": consistency,
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
    if state["safety"]["category"] == "wellness":
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


def _action_memory_text(action_data: dict) -> str:
    action_type = str(action_data.get("type") or "")
    if action_type == "create_reminder":
        text = action_data.get("text", "")
        due = action_data.get("due_at_iso", "")
        return f"reminder: {text} at {due}".strip()
    if action_type == "create_open_loop":
        return f"open loop: {action_data.get('title', '')}".strip()
    if action_type == "close_open_loop":
        loop_id = action_data.get("id", "")
        summary = action_data.get("summary", "")
        outcome = action_data.get("outcome", "")
        return f"closed loop ({outcome}): {loop_id} - {summary}".strip()
    return action_type


@traced_node("trace_eval_logging")
async def log_trace(state: AgentState) -> AgentState:
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "trace_eval_logging"],
        "trace_logged": True,
    }
    return state
