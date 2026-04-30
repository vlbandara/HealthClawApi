from __future__ import annotations

from typing import Any, TypedDict

from healthclaw.schemas.memory import MemoryMutation


class AgentState(TypedDict, total=False):
    user: dict[str, Any]
    user_content: str
    channel: str
    user_message: dict[str, Any]
    assistant_message: dict[str, Any]
    memories: list[dict[str, Any]]
    soul_preferences: dict[str, Any]
    observable_signals: dict[str, Any]
    memory_mutations: list[MemoryMutation]
    memory_proposals: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    actions_taken: list[dict[str, Any]]
    time_context: dict[str, Any]
    response: str
    trace_metadata: dict[str, Any]
    open_loops: list[dict[str, Any]]
    streaks: list[dict[str, Any]]
    recent_messages: list[dict[str, Any]]
    memory_documents: dict[str, str]
    thread_summary: str
    relationship_signals: list[str]
