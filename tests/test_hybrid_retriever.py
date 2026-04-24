from __future__ import annotations

from unittest.mock import AsyncMock

from healthclaw.core.config import Settings
from healthclaw.db.models import Memory
from healthclaw.db.session import SessionLocal
from healthclaw.memory.embeddings import EmbeddingClient
from healthclaw.memory.retrieval import HybridRetriever
from tests.factories import make_user


async def test_hybrid_retriever_fallback_to_lexical() -> None:
    async with SessionLocal() as session:
        session.add(make_user("u-retrieval"))
        
        # memory with some key lexical overlaps
        session.add(Memory(
            id="m-lexical",
            user_id="u-retrieval",
            kind="preference",
            key="likes_tea",
            value={"text": "User loves green tea"},
            semantic_text="User loves green tea preference",
            confidence=0.8,
            visibility="internal",
            has_embedding=False
        ))
        
        # memory with no overlap
        session.add(Memory(
            id="m-unrelated",
            user_id="u-retrieval",
            kind="preference",
            key="likes_coffee",
            value={"text": "User likes black coffee"},
            semantic_text="User likes black coffee preference",
            confidence=0.5,
            visibility="internal",
            has_embedding=False
        ))
        
        await session.flush()

        settings = Settings(openrouter_api_key="test-key")
        embedding_client = EmbeddingClient(settings)
        # Mock embed_text to ensure it is not called or returns 0
        embedding_client.embed_text = AsyncMock(return_value=[0.0] * 1536)

        retriever = HybridRetriever(session, embedding_client)
        
        # Query containing "green" and "tea"
        results = await retriever.retrieve("u-retrieval", "I want some green tea")

        assert len(results) == 2
        # The first result should be m-lexical because of lexical matches
        assert results[0].id == "m-lexical"
        assert results[1].id == "m-unrelated"
