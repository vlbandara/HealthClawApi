from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.core.tracing import start_span
from healthclaw.db.models import Memory, MemoryRevision, PolicyProposal
from healthclaw.memory.embeddings import EmbeddingClient
from healthclaw.schemas.memory import MemoryMutation

logger = logging.getLogger(__name__)

HIGH_IMPACT_POLICY_KEYS = {
    "medical_boundary",
    "crisis_escalation",
    "quiet_hour_enforcement",
    "consent_rules",
}


class MemoryService:
    def __init__(
        self, session: AsyncSession, embedding_client: EmbeddingClient | None = None
    ) -> None:
        self.session = session
        self._embedding_client = embedding_client

    async def list_memories(
        self,
        user_id: str,
        *,
        include_internal: bool = False,
    ) -> list[Memory]:
        kind_priority = case(
            (Memory.kind == "goal", 0),
            (Memory.kind == "commitment", 1),
            (Memory.kind == "routine", 2),
            (Memory.kind == "friction", 3),
            (Memory.kind == "profile", 4),
            (Memory.kind == "preference", 5),
            (Memory.kind == "episode", 6),
            (Memory.kind == "policy", 7),
            else_=3,
        )
        now = datetime.now(UTC)
        filters = [
            Memory.user_id == user_id,
            Memory.is_active.is_(True),
            ((Memory.expires_at.is_(None)) | (Memory.expires_at > now)),
        ]
        if not include_internal:
            filters.append(Memory.visibility == "user_visible")
        result = await self.session.execute(
            select(Memory).where(*filters).order_by(kind_priority, Memory.key)
        )
        memories = list(result.scalars())
        for memory in memories:
            self._refresh_freshness(memory, now)
            memory.last_accessed_at = now
        return memories

    async def retrieve_relevant_memories(
        self,
        user_id: str,
        query: str,
        *,
        limit: int = 8,
        kinds: set[str] | None = None,
    ) -> list[Memory]:
        if self._embedding_client is not None:
            from healthclaw.memory.retrieval import HybridRetriever

            retriever = HybridRetriever(self.session, self._embedding_client)
            async with start_span(
                "memory.retrieve",
                attributes={
                    "user_id": user_id,
                    "query_len": len(query),
                    "limit": limit,
                },
            ) as span:
                result = await retriever.retrieve(user_id, query, limit=limit, kinds=kinds)
                span.set_attribute("result_count", len(result))
                return result

        # Lexical-only fallback (used when no embedding client is wired in)
        memories = [
            memory
            for memory in await self.list_memories(user_id, include_internal=True)
            if kinds is None or memory.kind in kinds
        ]
        tokens = {token for token in query.lower().split() if len(token) >= 4}

        def score(memory: Memory) -> tuple[int, float]:
            haystack = f"{memory.kind} {memory.key} {memory.semantic_text}".lower()
            lexical = sum(1 for token in tokens if token in haystack)
            kind_weight = {
                "goal": 7,
                "commitment": 6,
                "routine": 5,
                "friction": 5,
                "preference": 4,
                "profile": 3,
                "episode": 2,
                "policy": 1,
            }.get(memory.kind, 0)
            return (lexical + kind_weight, memory.confidence)

        ranked = sorted(memories, key=score, reverse=True)
        return ranked[:limit]

    async def memories_due_for_refresh(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
        limit: int = 5,
    ) -> list[Memory]:
        now = now or datetime.now(UTC)
        result = await self.session.execute(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.is_active.is_(True),
                Memory.user_editable.is_(True),
                Memory.refresh_after.is_not(None),
                Memory.refresh_after <= now,
            )
            .order_by(Memory.refresh_after.asc())
            .limit(limit)
        )
        return list(result.scalars())

    async def patch_user_memory(
        self,
        user_id: str,
        memory_id: str,
        *,
        value: dict[str, Any] | None = None,
        confidence: float | None = None,
        refresh_after: datetime | None = None,
        expires_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> Memory | None:
        memory = await self._get_user_editable_memory(user_id, memory_id)
        if memory is None:
            return None
        previous = memory.value
        if value is not None:
            memory.value = value
            memory.semantic_text = self._semantic_text(value)
        if confidence is not None:
            memory.confidence = confidence
        memory.refresh_after = refresh_after
        memory.expires_at = expires_at
        if metadata is not None:
            memory.metadata_ = metadata
        memory.last_confirmed_at = datetime.now(UTC)
        memory.freshness_score = 1.0
        self.session.add(
            MemoryRevision(
                memory_id=memory.id,
                previous_value=previous,
                new_value=memory.value,
                reason="User edited memory.",
                confidence=memory.confidence,
                source_message_ids=memory.source_message_ids,
                trace_id=trace_id,
            )
        )
        return memory

    async def delete_user_memory(
        self,
        user_id: str,
        memory_id: str,
        *,
        trace_id: str | None = None,
    ) -> bool:
        memory = await self._get_user_editable_memory(user_id, memory_id)
        if memory is None:
            return False
        memory.is_active = False
        memory.metadata_ = {**memory.metadata_, "deleted_by_user": True}
        self.session.add(
            MemoryRevision(
                memory_id=memory.id,
                previous_value=memory.value,
                new_value={"deleted": True},
                reason="User deleted memory.",
                confidence=memory.confidence,
                source_message_ids=memory.source_message_ids,
                trace_id=trace_id,
            )
        )
        return True

    async def deactivate_matching_memories(self, user_id: str, query: str) -> int:
        query = query.strip().lower()
        if not query:
            return 0
        count = 0
        for memory in await self.list_memories(user_id):
            haystack = f"{memory.kind} {memory.key} {memory.semantic_text}".lower()
            if query in haystack and memory.user_editable:
                memory.is_active = False
                memory.metadata_ = {**memory.metadata_, "deleted_by_user": True}
                count += 1
        return count

    async def summarize_user_memory(self, user_id: str, *, limit: int = 12) -> str:
        memories = await self.list_memories(user_id)
        lines = []
        for memory in memories[:limit]:
            value = memory.value.get("text") or memory.value.get("summary") or memory.semantic_text
            lines.append(f"{memory.kind}:{memory.key} - {value}")
        return "\n".join(lines) if lines else "No active user-visible memory yet."

    async def upsert_memory(
        self,
        user_id: str,
        mutation: MemoryMutation,
        source_message_ids: list[str],
        *,
        trace_id: str | None = None,
    ) -> tuple[Memory, str]:
        outcome = "skipped"
        async with start_span(
            "memory.upsert",
            attributes={
                "kind": mutation.kind,
                "key": mutation.key,
                "outcome": outcome,
            },
        ) as span:
            result = await self._upsert_memory_impl(user_id, mutation, source_message_ids, trace_id)
            outcome = "created" if result.revision_count == 1 else "updated"
            span.set_attribute("outcome", outcome)
            return result, outcome

    async def _upsert_memory_impl(
        self,
        user_id: str,
        mutation: MemoryMutation,
        source_message_ids: list[str],
        trace_id: str | None = None,
    ) -> Memory:
        if mutation.kind == "policy" and mutation.key in HIGH_IMPACT_POLICY_KEYS:
            self.session.add(
                PolicyProposal(
                    user_id=user_id,
                    key=mutation.key,
                    proposed_value=mutation.value,
                    reason=mutation.reason,
                    status="pending",
                    trace_id=trace_id,
                )
            )
            await self.session.flush()
            return await self._policy_proposal_shadow_memory(user_id, mutation)

        result = await self.session.execute(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.kind == mutation.kind,
                Memory.key == mutation.key,
            )
        )
        memory = result.scalar_one_or_none()
        previous = None if memory is None else memory.value
        now = datetime.now(UTC)
        semantic_text = self._semantic_text(mutation.value)
        if memory is None:
            memory = Memory(
                user_id=user_id,
                kind=mutation.kind,
                key=mutation.key,
                layer=mutation.layer,
                value=mutation.value,
                semantic_text=semantic_text,
                confidence=mutation.confidence,
                freshness_score=1.0,
                source_message_ids=source_message_ids,
                last_confirmed_at=now,
                refresh_after=mutation.refresh_after,
                expires_at=mutation.expires_at,
                visibility=mutation.visibility,
                user_editable=mutation.user_editable,
                metadata_=mutation.metadata,
                is_active=True,
            )
            self.session.add(memory)
            await self.session.flush()
        else:
            conflict = previous is not None and previous != mutation.value
            memory.value = mutation.value
            memory.semantic_text = semantic_text
            memory.confidence = mutation.confidence
            memory.freshness_score = 1.0
            memory.source_message_ids = self._merge_source_message_ids(
                memory.source_message_ids,
                source_message_ids,
            )
            memory.last_confirmed_at = now
            memory.refresh_after = mutation.refresh_after
            memory.expires_at = mutation.expires_at
            memory.layer = mutation.layer
            memory.visibility = mutation.visibility
            memory.user_editable = mutation.user_editable
            memory.is_active = True
            reactivated = bool(memory.metadata_.get("deleted_by_user"))
            memory.metadata_ = {
                **mutation.metadata,
                **({"conflict_replaced_previous": previous} if conflict else {}),
                **({"reactivated_from_deleted": True} if reactivated else {}),
            }

        self.session.add(
            MemoryRevision(
                memory_id=memory.id,
                previous_value=previous,
                new_value=mutation.value,
                reason=mutation.reason,
                confidence=mutation.confidence,
                source_message_ids=source_message_ids,
                trace_id=trace_id,
            )
        )

        # Inline embedding — runs after flush so memory.id is available
        await self._try_embed_memory(memory)

        return memory

    async def _try_embed_memory(self, memory: Memory) -> None:
        if self._embedding_client is None or not self._embedding_client.enabled:
            return
        if not memory.semantic_text:
            return
        try:
            vec = await self._embedding_client.embed_text(memory.semantic_text)
            if any(v != 0.0 for v in vec):
                vec_str = "[" + ",".join(str(v) for v in vec) + "]"
                async with self.session.begin_nested():
                    await self.session.execute(
                        text(
                            "UPDATE memories SET embedding_vec = CAST(:vec AS vector), "
                            "has_embedding = true WHERE id = :id"
                        ),
                        {"vec": vec_str, "id": memory.id},
                    )
                memory.has_embedding = True
        except Exception as exc:
            logger.warning("Embedding failed for memory %s: %s", memory.id, exc)

    async def _policy_proposal_shadow_memory(
        self,
        user_id: str,
        mutation: MemoryMutation,
    ) -> Memory:
        result = await self.session.execute(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.kind == "policy",
                Memory.key == "pending_policy_proposal",
            )
        )
        memory = result.scalar_one_or_none()
        value: dict[str, Any] = {
            "key": mutation.key,
            "status": "pending_approval",
            "reason": mutation.reason,
        }
        if memory is None:
            memory = Memory(
                user_id=user_id,
                kind="policy",
                key="pending_policy_proposal",
                value=value,
                semantic_text=self._semantic_text(value),
                confidence=mutation.confidence,
                source_message_ids=[],
                visibility="internal",
                user_editable=False,
                metadata_={"proposal_only": True},
                is_active=True,
            )
            self.session.add(memory)
            await self.session.flush()
        return memory

    @staticmethod
    def _semantic_text(value: dict[str, Any]) -> str:
        parts: list[str] = []
        for item in value.values():
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, (int, float, bool)):
                parts.append(str(item))
            elif isinstance(item, list):
                parts.extend(str(child) for child in item if isinstance(child, str))
        return " ".join(parts)[:2000]

    @staticmethod
    def _merge_source_message_ids(existing: list[str], incoming: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for message_id in [*existing, *incoming]:
            if message_id in seen:
                continue
            seen.add(message_id)
            merged.append(message_id)
        return merged[-50:]

    async def _get_user_editable_memory(self, user_id: str, memory_id: str) -> Memory | None:
        result = await self.session.execute(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id == user_id,
                Memory.is_active.is_(True),
                Memory.user_editable.is_(True),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _refresh_freshness(memory: Memory, now: datetime) -> None:
        if memory.last_confirmed_at is None:
            memory.freshness_score = min(memory.freshness_score or 1.0, 0.7)
            return
        last_confirmed_at = memory.last_confirmed_at
        if last_confirmed_at.tzinfo is None:
            last_confirmed_at = last_confirmed_at.replace(tzinfo=UTC)
        age_days = max((now - last_confirmed_at).days, 0)
        half_life_days = {
            "commitment": 7,
            "open_loop": 7,
            "episode": 14,
            "routine": 45,
            "friction": 45,
            "goal": 60,
            "preference": 180,
            "profile": 365,
            "policy": 365,
            "relationship": 90,
        }.get(memory.kind, 60)
        memory.freshness_score = max(0.2, 1.0 - (age_days / (half_life_days * 2)))
        if memory.refresh_after is None and memory.kind in {"goal", "routine", "friction"}:
            memory.refresh_after = last_confirmed_at + timedelta(days=half_life_days)
