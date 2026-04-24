from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from sqlmodel import select

from healthclaw.core.config import Settings
from healthclaw.db.session import SessionLocal
from healthclaw.memory.consolidator import ConsolidatorService
from healthclaw.memory.service import MemoryService
from tests.factories import make_message, make_thread, make_user


async def test_consolidator_creates_episodes_and_advances_cursor() -> None:
    async with SessionLocal() as session:
        session.add(make_user("u-consolidator"))
        session.add(make_thread(user_id="u-consolidator", thread_id="thread-consolidator"))
        
        # Add batch of messages (> BATCH_SIZE to trigger consolidation)
        # BATCH_SIZE is 40, so let's add 41 messages
        for i in range(41):
            session.add(
                make_message(
                    message_id=f"msg-{i}",
                    user_id="u-consolidator",
                    thread_id="thread-consolidator",
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"Message {i}"
                )
            )
        await session.flush()

        settings = Settings(openrouter_api_key="test-key")
        memory_service = MemoryService(session)
        consolidator = ConsolidatorService(session, settings, memory_service)

        mock_response_content = json.dumps([
            {
                "key": "test_episode_1",
                "summary": "This is a test summary 1.",
                "themes": ["test"],
                "sentiment": 0.8
            }
        ])

        # Patch OpenRouterClient to return our static JSON
        with patch(
            "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
            new_callable=AsyncMock,
        ) as mock_complete:
            mock_complete.return_value.content = mock_response_content
            
            # Action!
            created = await consolidator.run_for_user("u-consolidator")
            
            assert created == 1
            
            # Verify cursor advanced
            from healthclaw.db.models import UserMemoryCursor
            cursor = (await session.execute(
                select(UserMemoryCursor).where(UserMemoryCursor.user_id == "u-consolidator")
            )).scalar_one()

            assert cursor is not None
            assert cursor.consolidation_cursor_msg_id == "msg-39"
            assert cursor.consolidation_cursor_at is not None

            # Verify memory was created
            from healthclaw.db.models import Memory
            memories = (await session.execute(
                select(Memory).where(Memory.user_id == "u-consolidator")
            )).scalars().all()

            assert len(memories) == 1
            assert memories[0].kind == "episode"
            assert memories[0].key == "test_episode_1"
