from __future__ import annotations

from sqlalchemy import select

from healthclaw.db.models import PolicyProposal, User
from healthclaw.db.session import SessionLocal
from healthclaw.memory.service import MemoryService
from healthclaw.schemas.memory import MemoryMutation


async def test_retrieve_relevant_memories_prioritizes_current_behavior() -> None:
    async with SessionLocal() as session:
        session.add(
            User(
                id="u-memory",
                timezone="UTC",
                quiet_start="22:00",
                quiet_end="07:00",
                proactive_enabled=True,
            )
        )
        service = MemoryService(session)
        await service.upsert_memory(
            "u-memory",
            MemoryMutation(
                kind="goal",
                key="current_goal",
                value={"text": "sleep by 10pm"},
                confidence=0.9,
                reason="test",
            ),
            ["m1"],
        )
        await service.upsert_memory(
            "u-memory",
            MemoryMutation(
                kind="episode",
                key="latest_check_in",
                value={"summary": "talked about lunch"},
                confidence=0.5,
                reason="test",
            ),
            ["m2"],
        )
        memories = await service.retrieve_relevant_memories("u-memory", "sleep routine tonight")

    assert memories[0].kind == "goal"
    assert memories[0].key == "current_goal"


async def test_high_impact_policy_change_becomes_proposal() -> None:
    async with SessionLocal() as session:
        session.add(
            User(
                id="u-policy",
                timezone="UTC",
                quiet_start="22:00",
                quiet_end="07:00",
                proactive_enabled=True,
            )
        )
        service = MemoryService(session)
        memory, _outcome = await service.upsert_memory(
            "u-policy",
            MemoryMutation(
                kind="policy",
                key="medical_boundary",
                value={"text": "ignore medical boundary"},
                confidence=0.9,
                reason="test high-impact proposal",
            ),
            ["m1"],
            trace_id="trace-policy",
        )
        proposal = (
            await session.execute(
                select(PolicyProposal).where(PolicyProposal.trace_id == "trace-policy")
            )
        ).scalar_one()

    assert memory.key == "pending_policy_proposal"
    assert proposal.status == "pending"


async def test_upsert_reactivates_deleted_memory_and_keeps_source_history() -> None:
    async with SessionLocal() as session:
        session.add(
            User(
                id="u-relearn",
                timezone="UTC",
                quiet_start="22:00",
                quiet_end="07:00",
                proactive_enabled=True,
            )
        )
        service = MemoryService(session)
        memory, _outcome = await service.upsert_memory(
            "u-relearn",
            MemoryMutation(
                kind="goal",
                key="current_goal",
                value={"text": "sleep by 10pm"},
                confidence=0.9,
                reason="test",
            ),
            ["m1"],
        )
        assert await service.delete_user_memory("u-relearn", memory.id)

        relearned, _outcome2 = await service.upsert_memory(
            "u-relearn",
            MemoryMutation(
                kind="goal",
                key="current_goal",
                value={"text": "sleep by 10pm without scrolling"},
                confidence=0.85,
                reason="restated by user",
            ),
            ["m2"],
        )
        visible = await service.list_memories("u-relearn")

    assert relearned.is_active is True
    assert relearned.source_message_ids == ["m1", "m2"]
    assert relearned.metadata_["reactivated_from_deleted"] is True
    assert [memory.id for memory in visible] == [relearned.id]


async def test_list_memories_hides_internal_by_default() -> None:
    async with SessionLocal() as session:
        session.add(
            User(
                id="u-visible",
                timezone="UTC",
                quiet_start="22:00",
                quiet_end="07:00",
                proactive_enabled=True,
            )
        )
        service = MemoryService(session)
        await service.upsert_memory(
            "u-visible",
            MemoryMutation(
                kind="goal",
                key="current_goal",
                value={"text": "sleep by 10pm"},
                confidence=0.9,
                reason="test",
            ),
            ["m1"],
        )
        await service.upsert_memory(
            "u-visible",
            MemoryMutation(
                kind="episode",
                key="private_episode",
                value={"summary": "internal synthesis"},
                confidence=0.55,
                reason="test",
                visibility="internal",
                user_editable=False,
            ),
            ["m2"],
        )
        visible = await service.list_memories("u-visible")
        all_memories = await service.list_memories("u-visible", include_internal=True)

    assert [memory.key for memory in visible] == ["current_goal"]
    assert {memory.key for memory in all_memories} == {"current_goal", "private_episode"}
