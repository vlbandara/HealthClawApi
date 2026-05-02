"""Cross-encoder reranker for memory retrieval.

Provider: Cohere Rerank v3.5 API (default) or none (pass-through).
Degrades gracefully on timeout or API error — falls back to the
original hybrid-ranked order.

Usage::

    client = RerankerClient(settings)
    reranked = await client.rerank(query="need water", memories=memories, top_n=8)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class RerankerClient:
    """Async Cohere reranker wrapper with graceful degradation."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.provider: str = getattr(settings, "reranker_provider", "none")
        self.model: str = getattr(settings, "reranker_model", "rerank-v3.5")
        self.api_key: str | None = getattr(settings, "cohere_api_key", None)
        self.timeout_s: float = getattr(settings, "reranker_timeout_ms", 400) / 1000.0

    @property
    def enabled(self) -> bool:
        return self.provider == "cohere" and bool(self.api_key)

    async def rerank(
        self,
        query: str,
        memories: list[Any],  # ORM Memory or dict
        *,
        top_n: int = 8,
    ) -> list[Any]:
        """Return up to *top_n* memories reranked by Cohere.

        Falls back to the original order on any error or if disabled.
        """
        if not self.enabled or len(memories) <= top_n:
            return memories[:top_n]

        try:
            return await asyncio.wait_for(
                self._cohere_rerank(query, memories, top_n=top_n),
                timeout=self.timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Reranker timed out after %.1fs — falling back to hybrid order",
                self.timeout_s,
            )
        except Exception as exc:
            logger.warning("Reranker error — falling back to hybrid order: %s", exc)
        return memories[:top_n]

    async def _cohere_rerank(
        self,
        query: str,
        memories: list[Any],
        *,
        top_n: int,
    ) -> list[Any]:
        import cohere  # type: ignore[import-untyped]  # noqa: PLC0415

        client = cohere.AsyncClientV2(api_key=self.api_key)
        documents = [_memory_to_doc(m) for m in memories]
        response = await client.rerank(
            model=self.model,
            query=query,
            documents=documents,
            top_n=top_n,
        )
        reranked = [memories[r.index] for r in response.results]
        logger.debug(
            "Reranker: %d candidates → %d results (model=%s)",
            len(memories),
            len(reranked),
            self.model,
        )
        return reranked


def _memory_to_doc(memory: Any) -> str:
    """Convert ORM Memory or dict to a plain-text doc for reranking."""
    if hasattr(memory, "kind"):
        # ORM row
        kind = memory.kind or ""
        key = memory.key or ""
        semantic = memory.semantic_text or ""
        value = memory.value or {}
    else:
        kind = memory.get("kind") or ""
        key = memory.get("key") or ""
        semantic = memory.get("semantic_text") or ""
        value = memory.get("value") or {}

    text = ""
    if isinstance(value, dict):
        text = str(value.get("text") or value.get("summary") or "")
    if not text:
        text = semantic
    return f"{kind}:{key} — {text}".strip()
