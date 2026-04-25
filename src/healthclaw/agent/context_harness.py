from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from healthclaw.agent.time_context import TimeContext
from healthclaw.core.config import Settings

MemoryLike = dict[str, Any]
MessageLike = dict[str, Any]
OpenLoopLike = dict[str, Any]

ACTIVE_MEMORY_KINDS = {"goal", "routine", "friction", "commitment", "open_loop"}
RELATIONSHIP_MEMORY_KINDS = {"profile", "preference", "relationship"}
STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "been",
    "from",
    "have",
    "help",
    "into",
    "just",
    "keep",
    "like",
    "make",
    "need",
    "plan",
    "really",
    "restart",
    "short",
    "some",
    "still",
    "than",
    "that",
    "them",
    "then",
    "there",
    "they",
    "this",
    "today",
    "want",
    "with",
    "your",
}
KIND_BASE_SCORE = {
    "goal": 4.5,
    "routine": 4.1,
    "friction": 4.0,
    "commitment": 3.8,
    "open_loop": 3.6,
    "preference": 2.9,
    "profile": 2.7,
    "relationship": 2.6,
    "episode": 1.7,
    "policy": 1.0,
}
SECTION_KIND_HINTS = {
    "goals": {"goal"},
    "routines": {"routine"},
    "frictions": {"friction"},
    "commitments": {"commitment", "open_loop"},
    "recent episodes": {"episode"},
    "relationship context": {"relationship", "preference"},
    "profile notes": {"profile"},
    "stable profile": {"profile"},
    "interests and taste": {"preference", "relationship"},
    "tone": {"preference"},
    "response": {"preference"},
}


@dataclass(frozen=True)
class PromptContext:
    memories: list[MemoryLike]
    recent_messages: list[MessageLike]
    open_loops: list[OpenLoopLike]
    memory_documents: dict[str, str]
    thread_summary: str
    relationship_signals: list[str]
    budget_usage: dict[str, int]
    metadata: dict[str, Any]


class ContextHarness:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build(
        self,
        *,
        user_content: str,
        time_context: TimeContext,
        memories: list[MemoryLike],
        recent_messages: list[MessageLike],
        open_loops: list[OpenLoopLike],
        memory_documents: dict[str, str],
        user_context: dict[str, Any],
        thread_summary: str = "",
        mode: str = "active",
    ) -> PromptContext:
        query_tokens = _tokenize(user_content)
        selected_memories, dropped_memories, memory_chars = self._select_memories(
            memories,
            query_tokens=query_tokens,
        )
        selected_loops, dropped_loops, open_loop_chars = self._select_open_loops(
            open_loops,
            query_tokens=query_tokens,
        )
        packed_recent, digest, dropped_recent, recent_chars = self._pack_recent_messages(
            recent_messages,
            thread_summary=thread_summary,
        )
        selected_documents, doc_sections, dropped_sections, document_chars = self._select_documents(
            memory_documents,
            query_tokens=query_tokens,
            selected_memories=selected_memories,
        )
        relationship_signals = _relationship_signals(user_context, time_context)
        metadata = {
            "mode": mode,
            "selected_memory_keys": [
                f"{memory.get('kind')}:{memory.get('key')}" for memory in selected_memories
            ],
            "dropped_memories": dropped_memories[:8],
            "selected_open_loop_ids": [str(loop.get("id") or "") for loop in selected_loops],
            "dropped_open_loops": dropped_loops[:6],
            "selected_document_sections": doc_sections[:10],
            "dropped_document_sections": dropped_sections[:10],
            "recent_messages_selected": len(packed_recent),
            "recent_messages_dropped": dropped_recent,
            "thread_summary_included": bool(digest),
            "relationship_signals": relationship_signals,
            "budget_usage": {
                "memories": memory_chars,
                "open_loops": open_loop_chars,
                "recent_messages": recent_chars,
                "thread_summary": len(digest),
                "documents": document_chars,
            },
        }
        return PromptContext(
            memories=selected_memories,
            recent_messages=packed_recent,
            open_loops=selected_loops,
            memory_documents=selected_documents,
            thread_summary=digest,
            relationship_signals=relationship_signals,
            budget_usage=metadata["budget_usage"],
            metadata=metadata,
        )

    def _select_memories(
        self,
        memories: list[MemoryLike],
        *,
        query_tokens: set[str],
    ) -> tuple[list[MemoryLike], list[dict[str, Any]], int]:
        scored = [
            {
                "memory": memory,
                "score": self._memory_score(memory, query_tokens=query_tokens),
                "lane": self._memory_lane(memory),
                "chars": _memory_chars(memory),
                "reason": self._memory_reason(memory, query_tokens=query_tokens),
            }
            for memory in memories
        ]

        selected: list[MemoryLike] = []
        dropped: list[dict[str, Any]] = []
        total_chars = 0
        seen_text: set[str] = set()
        for lane in (0, 1, 2):
            lane_items = [item for item in scored if item["lane"] == lane]
            lane_items.sort(key=lambda item: item["score"], reverse=True)
            for item in lane_items:
                memory = item["memory"]
                key = f"{memory.get('kind')}:{memory.get('key')}"
                normalized_text = _normalize_memory_text(memory)
                if normalized_text and normalized_text in seen_text:
                    dropped.append({"key": key, "reason": "duplicate_text"})
                    continue
                next_total = total_chars + item["chars"]
                if selected and next_total > self.settings.context_harness_memory_chars:
                    dropped.append({"key": key, "reason": "memory_budget"})
                    continue
                if item["score"] <= 0.15:
                    dropped.append({"key": key, "reason": "low_relevance"})
                    continue
                selected.append(memory)
                total_chars = next_total
                if normalized_text:
                    seen_text.add(normalized_text)

        active_keys = {
            f"{memory.get('kind')}:{memory.get('key')}"
            for memory in selected
            if str(memory.get("kind") or "") in ACTIVE_MEMORY_KINDS
        }
        selected = [
            memory
            for memory in selected
            if not (
                str(memory.get("kind") or "") == "episode"
                and _lexical_overlap(memory, query_tokens) > 0
                and active_keys
                and self._memory_score(memory, query_tokens=query_tokens) < 5.0
            )
        ]
        selected.sort(
            key=lambda memory: (
                self._memory_lane(memory),
                -self._memory_score(memory, query_tokens=query_tokens),
            )
        )
        selected = selected[: self.settings.memory_retrieval_limit]
        return selected, dropped, sum(_memory_chars(memory) for memory in selected)

    def _select_open_loops(
        self,
        open_loops: list[OpenLoopLike],
        *,
        query_tokens: set[str],
    ) -> tuple[list[OpenLoopLike], list[dict[str, Any]], int]:
        scored: list[tuple[float, OpenLoopLike, int]] = []
        for loop in open_loops:
            title = str(loop.get("title") or "")
            lexical = _token_overlap(query_tokens, _tokenize(title))
            age_hours = float(loop.get("age_hours") or 0.0)
            score = lexical * 1.6 + min(age_hours / 24.0, 2.0)
            scored.append((score, loop, len(title)))
        scored.sort(key=lambda item: item[0], reverse=True)

        selected: list[OpenLoopLike] = []
        dropped: list[dict[str, Any]] = []
        total_chars = 0
        for score, loop, chars in scored:
            loop_id = str(loop.get("id") or "")
            if selected and total_chars + chars > self.settings.context_harness_open_loop_chars:
                dropped.append({"id": loop_id, "reason": "open_loop_budget"})
                continue
            if score <= 0.2 and selected:
                dropped.append({"id": loop_id, "reason": "low_relevance"})
                continue
            selected.append(loop)
            total_chars += chars
        return selected[:4], dropped, total_chars

    def _pack_recent_messages(
        self,
        recent_messages: list[MessageLike],
        *,
        thread_summary: str,
    ) -> tuple[list[MessageLike], str, int, int]:
        selected: list[MessageLike] = []
        total_chars = 0
        meaningful = [message for message in recent_messages if _message_content(message)]
        kept = meaningful[-self.settings.context_harness_recent_raw_turn_limit :]
        for message in kept:
            content = _message_content(message)[:800]
            next_total = total_chars + len(content)
            if selected and next_total > self.settings.context_harness_recent_chars:
                break
            selected.append({"role": message.get("role"), "content": content})
            total_chars = next_total

        dropped_count = max(0, len(meaningful) - len(selected))
        digest = ""
        if dropped_count or thread_summary.strip():
            digest = thread_summary.strip()[: self.settings.context_harness_thread_summary_chars]
        return selected, digest, dropped_count, total_chars

    def _select_documents(
        self,
        memory_documents: dict[str, str],
        *,
        query_tokens: set[str],
        selected_memories: list[MemoryLike],
    ) -> tuple[dict[str, str], list[dict[str, str]], list[dict[str, str]], int]:
        selected: dict[str, str] = {}
        picked_sections: list[dict[str, str]] = []
        dropped_sections: list[dict[str, str]] = []
        total_chars = 0
        selected_kinds = {str(memory.get("kind") or "") for memory in selected_memories}
        for kind, content in memory_documents.items():
            sections = _split_markdown_sections(content)
            if not sections:
                continue
            relevant_sections: list[tuple[str, str]] = []
            for title, body in sections:
                if not body.strip():
                    continue
                if self._document_section_relevant(
                    kind,
                    title,
                    body,
                    query_tokens=query_tokens,
                    selected_kinds=selected_kinds,
                ):
                    relevant_sections.append((title, body))
                else:
                    dropped_sections.append({"kind": kind, "section": title or "_root_"})

            if kind == "USER" and not relevant_sections:
                relevant_sections = sections[:1]
            if kind == "SOUL" and not relevant_sections and _has_custom_soul_overlay(content):
                relevant_sections = sections[:2]

            if not relevant_sections:
                continue

            excerpt_parts: list[str] = []
            for title, body in relevant_sections[:2]:
                excerpt = body.strip()[: self.settings.context_harness_doc_section_chars]
                header = f"## {title}\n\n" if title else ""
                candidate = f"{header}{excerpt}".strip()
                next_total = total_chars + len(candidate)
                if excerpt_parts and next_total > self.settings.context_harness_document_chars:
                    dropped_sections.append({"kind": kind, "section": title or "_root_"})
                    continue
                excerpt_parts.append(candidate)
                total_chars = next_total
                picked_sections.append({"kind": kind, "section": title or "_root_"})
            if excerpt_parts:
                selected[kind] = "\n\n".join(excerpt_parts)
        return selected, picked_sections, dropped_sections, total_chars

    def _document_section_relevant(
        self,
        kind: str,
        title: str,
        body: str,
        *,
        query_tokens: set[str],
        selected_kinds: set[str],
    ) -> bool:
        if kind == "USER":
            return title.lower() in {"stable profile", "profile notes", "_root_"}
        body_tokens = _tokenize(body)
        title_kinds = SECTION_KIND_HINTS.get(title.lower(), set())
        if title_kinds & selected_kinds:
            return True
        if _token_overlap(query_tokens, body_tokens) > 0:
            return True
        if kind == "INTERESTS" and {"preference", "relationship"} & selected_kinds:
            return True
        return False

    def _memory_score(self, memory: MemoryLike, *, query_tokens: set[str]) -> float:
        kind = str(memory.get("kind") or "")
        confidence = _float(memory.get("confidence"), default=0.4)
        freshness = _float(memory.get("freshness_score"), default=0.5)
        lexical = _lexical_overlap(memory, query_tokens)
        recency = _recency_score(
            memory.get("last_confirmed_at") or memory.get("updated_at") or memory.get("created_at")
        )
        access = _recency_score(memory.get("last_accessed_at"), window_days=14)
        score = (
            KIND_BASE_SCORE.get(kind, 1.0)
            + lexical * 3.0
            + confidence * 1.4
            + freshness * 1.2
            + recency * 0.9
            + access * 0.4
        )
        if kind == "episode" and lexical == 0:
            score -= 1.0
        if kind in ACTIVE_MEMORY_KINDS and lexical > 0:
            score += 0.8
        if _is_stale(memory):
            score -= 1.2
            if kind in ACTIVE_MEMORY_KINDS and lexical == 0:
                score -= 1.6
        return score

    @staticmethod
    def _memory_lane(memory: MemoryLike) -> int:
        kind = str(memory.get("kind") or "")
        if kind in ACTIVE_MEMORY_KINDS:
            return 0
        if kind in RELATIONSHIP_MEMORY_KINDS:
            return 1
        return 2

    @staticmethod
    def _memory_reason(memory: MemoryLike, *, query_tokens: set[str]) -> str:
        kind = str(memory.get("kind") or "")
        overlap = _lexical_overlap(memory, query_tokens)
        if kind in ACTIVE_MEMORY_KINDS and overlap > 0:
            return "active_behavior_match"
        if kind in RELATIONSHIP_MEMORY_KINDS and overlap > 0:
            return "relationship_match"
        if _is_stale(memory):
            return "stale"
        return "background"


def _relationship_signals(
    user_context: dict[str, Any],
    time_context: TimeContext,
) -> list[str]:
    lines: list[str] = []
    sentiment_ema = _float_or_none(user_context.get("sentiment_ema"))
    voice_text_ratio = _float_or_none(user_context.get("voice_text_ratio"))
    reply_latency = _float_or_none(user_context.get("reply_latency_seconds_ema"))
    if sentiment_ema is not None and sentiment_ema <= -0.35:
        lines.append("Use lower-pressure phrasing, no cheerleading, and smaller next steps.")
    if voice_text_ratio is not None and voice_text_ratio >= 0.65:
        lines.append("Favor concise spoken-style phrasing that reads naturally out loud.")
    if reply_latency is not None and reply_latency >= 43_200:
        lines.append("Do not frame slow re-entry or delayed replies as failure.")
    if _recent_meaningful_exchange(user_context.get("last_meaningful_exchange_at"), time_context):
        lines.append("Brief continuity references are safe without asking for a full recap.")
    return lines


def _tokenize(text: str) -> set[str]:
    tokens = {
        "".join(char for char in raw.lower() if char.isalnum())
        for raw in text.split()
    }
    return {token for token in tokens if len(token) >= 4 and token not in STOPWORDS}


def _token_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left))


def _memory_text(memory: MemoryLike) -> str:
    value = memory.get("value")
    if isinstance(value, dict):
        text = value.get("text") or value.get("summary")
        if isinstance(text, str) and text.strip():
            return text.strip()
    semantic = memory.get("semantic_text")
    return str(semantic or "").strip()


def _normalize_memory_text(memory: MemoryLike) -> str:
    return " ".join(_memory_text(memory).lower().split())


def _memory_chars(memory: MemoryLike) -> int:
    return len(str(memory.get("kind") or "")) + len(str(memory.get("key") or "")) + len(
        _memory_text(memory)
    )


def _lexical_overlap(memory: MemoryLike, query_tokens: set[str]) -> float:
    return _token_overlap(query_tokens, _tokenize(_memory_text(memory)))


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _recency_score(value: object, *, window_days: int = 30) -> float:
    dt = _coerce_datetime(value)
    if dt is None:
        return 0.0
    age = datetime.now(UTC) - dt.astimezone(UTC)
    if age <= timedelta(0):
        return 1.0
    return max(0.0, 1.0 - (age / timedelta(days=window_days)))


def _is_stale(memory: MemoryLike) -> bool:
    freshness = _float(memory.get("freshness_score"), default=0.5)
    updated = _coerce_datetime(memory.get("updated_at") or memory.get("last_confirmed_at"))
    if freshness >= 0.65 or updated is None:
        return False
    return datetime.now(UTC) - updated.astimezone(UTC) > timedelta(days=45)


def _message_content(message: MessageLike) -> str:
    return str(message.get("content") or "").strip()


def _split_markdown_sections(content: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title = ""
    current_lines: list[str] = []
    for line in content.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            if current_title or current_lines:
                sections.append((current_title or "_root_", "\n".join(current_lines).strip()))
            current_title = line.lstrip("# ").strip()
            current_lines = []
            continue
        current_lines.append(line)
    if current_title or current_lines:
        sections.append((current_title or "_root_", "\n".join(current_lines).strip()))
    return sections


def _has_custom_soul_overlay(content: str) -> bool:
    normalized = content.lower()
    return "no durable user-specific voice overlay yet" not in normalized


def _recent_meaningful_exchange(value: object, time_context: TimeContext) -> bool:
    meaningful_at = _coerce_datetime(value)
    if meaningful_at is None:
        return False
    local_now = datetime.fromisoformat(time_context.local_datetime).astimezone(UTC)
    return local_now - meaningful_at.astimezone(UTC) <= timedelta(hours=24)


def _float(value: object, *, default: float) -> float:
    return float(value) if isinstance(value, int | float) else default


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None
