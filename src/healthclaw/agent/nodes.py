from __future__ import annotations

from healthclaw.agent.continuity import build_bridges
from healthclaw.agent.response import generate_companion_response
from healthclaw.agent.safety import classify_safety
from healthclaw.agent.state import AgentState
from healthclaw.agent.time_context import TimeContext, build_time_context
from healthclaw.memory.extractors import extract_memory_mutations_enriched


async def normalize_input(state: AgentState) -> AgentState:
    state["user_content"] = " ".join(state["user_content"].strip().split())
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "input_normalization"],
    }
    return state


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


async def assemble_time_context(state: AgentState) -> AgentState:
    thread_last = state.get("trace_metadata", {}).get("last_interaction_at")
    context = build_time_context(state["user"], last_interaction_at=thread_last)
    state["time_context"] = context.to_dict()
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "time_context"],
    }
    return state


async def retrieve_memory(state: AgentState) -> AgentState:
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "memory_retrieval"],
        "memory_keys": [
            f"{memory['kind']}:{memory['key']}" for memory in state.get("memories", [])[:12]
        ],
    }
    return state


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

    response, metadata = await generate_companion_response(
        user_content=state["user_content"],
        safety=safety,
        time_context=time_ctx,
        memories=state.get("memories", []),
        soul_preferences=state.get("soul_preferences", {}),
        bridges=bridges,
        open_loops=state.get("open_loops", []),
        recent_messages=state.get("recent_messages", []),
        memory_documents=state.get("memory_documents", {}),
        user_context=state.get("user", {}),
    )
    state["response"] = response
    state["trace_metadata"] = {**state.get("trace_metadata", {}), "generation": metadata}
    state["trace_metadata"]["nodes"] = [
        *state["trace_metadata"].get("nodes", []),
        "companion_response",
    ]
    return state


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


async def update_memory(state: AgentState) -> AgentState:
    state["memory_mutations"] = [
        mutation.model_dump(mode="json")
        for mutation in await extract_memory_mutations_enriched(state["user_content"])
    ]
    if state["safety"]["category"] == "wellness":
        state["memory_mutations"].append(
            {
                "kind": "episode",
                "key": "latest_check_in",
                "layer": "episode",
                "value": {"summary": state["user_content"][:500]},
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


async def log_trace(state: AgentState) -> AgentState:
    state["trace_metadata"] = {
        **state.get("trace_metadata", {}),
        "nodes": [*state.get("trace_metadata", {}).get("nodes", []), "trace_eval_logging"],
        "trace_logged": True,
    }
    return state
