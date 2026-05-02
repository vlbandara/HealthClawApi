from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.db.models import Memory
from healthclaw.memory.embeddings import EmbeddingClient

KIND_WEIGHT = {
    "goal": 7,
    "commitment": 6,
    "routine": 5,
    "friction": 5,
    "preference": 4,
    "profile": 3,
    "episode": 2,
    "policy": 1,
    # WS6: health-domain memory kinds
    "medication_schedule": 6,
    "sleep_protocol": 5,
    "movement_routine": 5,
    "nutrition_pattern": 4,
    "mood_pattern": 4,
    # WS6: self-model kinds (high priority — guide synthesis)
    "self_model": 6,
    "user_pattern": 5,
    "rhythm": 4,
}
LEXICAL_WEIGHT = 0.4
SEMANTIC_WEIGHT = 0.6


class HybridRetriever:
    """Combines lexical scoring with pgvector ANN for memory retrieval.

    Optionally post-processes results with a cross-encoder reranker (Cohere).
    Falls back to lexical-only when embeddings are unavailable or the
    embedding call fails. Falls back to hybrid order when reranker errors.
    """

    def __init__(
        self,
        session: AsyncSession,
        embedding_client: EmbeddingClient,
        reranker: Any | None = None,
    ) -> None:
        self.session = session
        self.embedding_client = embedding_client
        self.reranker = reranker  # RerankerClient | None

    async def retrieve(
        self,
        user_id: str,
        query: str,
        *,
        limit: int = 8,
        kinds: set[str] | None = None,
        rerank_top_k_multiplier: int = 3,
    ) -> list[Memory]:
        now = datetime.now(UTC)

        # Load active, non-expired memories
        q = (
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.is_active.is_(True),
                (Memory.expires_at.is_(None)) | (Memory.expires_at > now),
            )
        )
        if kinds:
            q = q.where(Memory.kind.in_(kinds))
        result = await self.session.execute(q)
        memories = list(result.scalars())

        if not memories:
            return []

        # Run lexical scoring and optional semantic scoring in parallel
        lexical_scores, semantic_scores = await asyncio.gather(
            self._lexical_scores(query, memories),
            self._semantic_scores(query, memories),
        )

        # Merge scores
        scored: list[tuple[float, float, Memory]] = []
        for i, memory in enumerate(memories):
            lex = lexical_scores[i]
            sem = semantic_scores[i]
            kind_w = KIND_WEIGHT.get(memory.kind, 0)
            combined = LEXICAL_WEIGHT * lex + SEMANTIC_WEIGHT * sem + kind_w * 0.1
            scored.append((combined, memory.confidence, memory))

        ranked = sorted(scored, key=lambda x: (x[0], x[1]), reverse=True)

        # Candidate pool for reranker: take top_k * multiplier before reranking
        candidate_limit = limit * rerank_top_k_multiplier
        candidates = [m for _, _, m in ranked[:candidate_limit]]

        # Optionally rerank with cross-encoder
        if self.reranker is not None and len(candidates) > limit:
            candidates = await self.reranker.rerank(query, candidates, top_n=limit)

        # Update last_accessed_at on returned memories
        top = candidates[:limit]
        for m in top:
            m.last_accessed_at = now
        return top

    async def _lexical_scores(self, query: str, memories: list[Memory]) -> list[float]:
        tokens = {t for t in query.lower().split() if len(t) >= 4}
        scores = []
        for memory in memories:
            haystack = f"{memory.kind} {memory.key} {memory.semantic_text}".lower()
            score = sum(1 for t in tokens if t in haystack)
            scores.append(float(score))
        # Normalize to 0-1
        max_score = max(scores) if scores else 1.0
        if max_score == 0:
            return [0.0] * len(scores)
        return [s / max_score for s in scores]

    async def _semantic_scores(self, query: str, memories: list[Memory]) -> list[float]:
        # Only attempt ANN if any memory has an embedding and client is enabled
        has_embeddings = any(m.has_embedding for m in memories)
        if not has_embeddings or not self.embedding_client.enabled:
            return [0.0] * len(memories)

        try:
            query_vec = await self.embedding_client.embed_text(query)
            if all(v == 0.0 for v in query_vec):
                return [0.0] * len(memories)

            # Use pgvector cosine similarity via raw SQL for memories that have embeddings
            memory_ids = [m.id for m in memories if m.has_embedding]
            if not memory_ids:
                return [0.0] * len(memories)

            # Build parameterized query — pgvector operator <=> is cosine distance
            vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
            rows = await self.session.execute(
                text(
                    "SELECT id, 1 - (embedding_vec <=> :vec) AS similarity "
                    "FROM memories WHERE id = ANY(:ids)"
                ),
                {"vec": vec_str, "ids": memory_ids},
            )
            sim_map = {row.id: float(row.similarity) for row in rows}

            return [sim_map.get(m.id, 0.0) for m in memories]
        except Exception:
            # Degrade gracefully to lexical-only
            return [0.0] * len(memories)
