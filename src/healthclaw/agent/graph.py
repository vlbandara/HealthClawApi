from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from healthclaw.agent.nodes import (
    assemble_time_context,
    classify_scope_and_safety,
    decide_proactivity,
    generate_response,
    log_trace,
    normalize_input,
    retrieve_memory,
    update_memory,
)
from healthclaw.agent.state import AgentState


def build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("input_normalization", normalize_input)
    graph.add_node("safety_and_scope", classify_scope_and_safety)
    graph.add_node("time_context", assemble_time_context)
    graph.add_node("memory_retrieval", retrieve_memory)
    graph.add_node("companion_response", generate_response)
    graph.add_node("proactive_policy", decide_proactivity)
    graph.add_node("memory_update", update_memory)
    graph.add_node("trace_eval_logging", log_trace)

    graph.add_edge(START, "input_normalization")
    graph.add_edge("input_normalization", "safety_and_scope")
    graph.add_edge("safety_and_scope", "time_context")
    graph.add_edge("time_context", "memory_retrieval")
    graph.add_edge("memory_retrieval", "companion_response")
    graph.add_edge("companion_response", "proactive_policy")
    graph.add_edge("proactive_policy", "memory_update")
    graph.add_edge("memory_update", "trace_eval_logging")
    graph.add_edge("trace_eval_logging", END)
    return graph.compile()


agent_graph = build_agent_graph()
