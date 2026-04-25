from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


# ── Cron tasks (called on schedule by ARQ) ─────────────────────────────────


async def heartbeat_sweep_cron(ctx: dict) -> dict:
    """Every-60s sweep: schedule due work and process heartbeats + reminders."""
    results = {}
    try:
        from healthclaw.workers.app import process_due_heartbeats, process_due_reminders

        results["reminders"] = await process_due_reminders()
        results["heartbeats"] = await process_due_heartbeats()
        logger.info("heartbeat_sweep_cron completed: %s", results)
    except Exception as exc:
        logger.error("heartbeat_sweep_cron failed: %s", exc)
        results["error"] = str(exc)
    return results


async def consolidator_sweep_cron(ctx: dict) -> dict:
    """Daily 04:00 UTC: consolidate recent messages into episode memories for active users."""
    from datetime import timedelta

    from sqlalchemy import select

    from healthclaw.core.config import get_settings
    from healthclaw.db.models import User
    from healthclaw.db.session import SessionLocal
    from healthclaw.memory.consolidator import ConsolidatorService
    from healthclaw.memory.service import MemoryService

    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=30)

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.last_active_at.is_not(None),
                User.last_active_at >= cutoff,
            )
        )
        users = list(result.scalars())

    total_episodes = 0
    for user in users:
        try:
            async with SessionLocal() as session:
                memory_service = MemoryService(session)
                consolidator = ConsolidatorService(session, settings, memory_service)
                count = await consolidator.run_for_user(user.id)
                await session.commit()
                total_episodes += count
        except Exception as exc:
            logger.warning("Consolidator failed for user %s: %s", user.id, exc)

    result = {"users_processed": len(users), "episodes_created": total_episodes}
    logger.info("consolidator_sweep_cron completed: %s", result)
    return result


async def dream_sweep_cron(ctx: dict) -> dict:
    """Daily Dream loop: evolve source-of-truth memory and regenerate prompt docs."""
    from datetime import timedelta

    from sqlalchemy import select

    from healthclaw.core.config import get_settings
    from healthclaw.db.models import User
    from healthclaw.db.session import SessionLocal
    from healthclaw.memory.dream import DreamService
    from healthclaw.memory.service import MemoryService

    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=14)

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.last_active_at.is_not(None),
                User.last_active_at >= cutoff,
            )
        )
        users = list(result.scalars())

    completed = 0
    applied = 0
    for user in users:
        try:
            async with SessionLocal() as session:
                service = DreamService(session, settings, MemoryService(session))
                outcome = await service.run_for_user(user.id)
                await session.commit()
                if outcome.get("status") == "completed":
                    completed += 1
                    applied += int(outcome.get("applied", 0))
        except Exception as exc:
            logger.warning("Dream failed for user %s: %s", user.id, exc)

    result = {"users_processed": len(users), "completed": completed, "changes_applied": applied}
    logger.info("dream_sweep_cron completed: %s", result)
    return result


async def autonomous_wake_sweep(ctx: dict) -> dict:
    """Every 15 minutes: create autonomous heartbeat candidates, then process due jobs."""
    try:
        from healthclaw.db.session import SessionLocal
        from healthclaw.heartbeat.service import HeartbeatService
        from healthclaw.workers.app import process_due_heartbeats

        async with SessionLocal() as session:
            heartbeat = HeartbeatService(session)
            scheduled = await heartbeat.schedule_autonomous_wake(datetime.now(UTC))
            await session.commit()
        processed = await process_due_heartbeats()
        result = {"scheduled": scheduled, "processed": processed}
        logger.info("autonomous_wake_sweep completed: %s", result)
        return result
    except Exception as exc:
        logger.error("autonomous_wake_sweep failed: %s", exc)
        return {"error": str(exc)}


# ── Enqueueable tasks ──────────────────────────────────────────────────────


async def process_due_reminders_task(ctx: dict) -> dict:
    from healthclaw.workers.app import process_due_reminders

    return await process_due_reminders()


async def process_due_heartbeats_task(ctx: dict) -> dict:
    from healthclaw.workers.app import process_due_heartbeats

    return await process_due_heartbeats()


async def run_consolidator_for_user(ctx: dict, user_id: str) -> dict:
    from healthclaw.core.config import get_settings
    from healthclaw.db.session import SessionLocal
    from healthclaw.memory.consolidator import ConsolidatorService
    from healthclaw.memory.service import MemoryService

    settings = get_settings()
    async with SessionLocal() as session:
        memory_service = MemoryService(session)
        consolidator = ConsolidatorService(session, settings, memory_service)
        count = await consolidator.run_for_user(user_id)
        await session.commit()
    result = {"user_id": user_id, "episodes_created": count}
    logger.info("run_consolidator_for_user completed: %s", result)
    return result


async def run_dream_for_user(ctx: dict, user_id: str) -> dict:
    from healthclaw.core.config import get_settings
    from healthclaw.db.session import SessionLocal
    from healthclaw.memory.dream import DreamService
    from healthclaw.memory.service import MemoryService

    settings = get_settings()
    async with SessionLocal() as session:
        service = DreamService(session, settings, MemoryService(session))
        outcome = await service.run_for_user(user_id)
        await session.commit()
    result = {"user_id": user_id, **outcome}
    logger.info("run_dream_for_user completed: %s", result)
    return result


async def embed_memory_batch(ctx: dict, memory_ids: list[str]) -> dict:
    """Re-embed a batch of memories. Used for backfill or re-embedding after model change."""
    from sqlalchemy import select

    from healthclaw.core.config import get_settings
    from healthclaw.db.models import Memory
    from healthclaw.db.session import SessionLocal
    from healthclaw.memory.embeddings import EmbeddingClient
    from healthclaw.memory.service import MemoryService

    settings = get_settings()
    embedding_client = EmbeddingClient(settings)
    updated = 0

    async with SessionLocal() as session:
        result = await session.execute(select(Memory).where(Memory.id.in_(memory_ids)))
        memories = list(result.scalars())
        memory_service = MemoryService(session, embedding_client)
        for memory in memories:
            try:
                await memory_service._try_embed_memory(memory)
                updated += 1
            except Exception as exc:
                logger.warning("Failed to embed memory %s: %s", memory.id, exc)
        await session.commit()

    result = {"requested": len(memory_ids), "updated": updated}
    logger.info("embed_memory_batch completed: %s", result)
    return result
