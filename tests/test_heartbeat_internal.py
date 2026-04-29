from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from healthclaw.db.models import HeartbeatJob, User
from healthclaw.db.session import SessionLocal
from healthclaw.heartbeat.service import HeartbeatService


def _make_user(user_id: str, quiet_start: str = "22:00", quiet_end: str = "07:00") -> User:
    return User(
        id=user_id,
        timezone="UTC",
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        proactive_enabled=True,
    )


# ---------------------------------------------------------------------------
# G1/G2: schedule_internal_jobs creates dream + consolidate idempotently
# ---------------------------------------------------------------------------


async def test_schedule_internal_jobs_creates_dream_and_consolidate() -> None:
    """G1: Running schedule_internal_jobs once creates one dream + one consolidate job."""
    user_id = "u-internal-jobs-create"
    # 23:00 UTC → inside quiet hours (22:00–07:00)
    now = datetime(2026, 4, 29, 23, 0, tzinfo=UTC)

    async with SessionLocal() as session:
        user = _make_user(user_id)
        session.add(user)
        await session.commit()

    async with SessionLocal() as session:
        heartbeat = HeartbeatService(session)
        user = await session.get(User, user_id)
        count = await heartbeat.schedule_internal_jobs(user, now=now)
        await session.commit()

    assert count == 2

    async with SessionLocal() as session:
        result = await session.execute(
            __import__("sqlalchemy", fromlist=["select"])
            .select(HeartbeatJob)
            .where(HeartbeatJob.user_id == user_id)
            .order_by(HeartbeatJob.kind)
        )
        jobs = list(result.scalars())

    kinds = {j.kind for j in jobs}
    assert kinds == {"consolidate", "dream"}
    for job in jobs:
        assert job.channel == "internal"
        assert job.status == "scheduled"
        assert f":{user_id}:" in job.idempotency_key


async def test_schedule_internal_jobs_idempotent() -> None:
    """G2: Calling schedule_internal_jobs twice on the same day creates no duplicates."""
    user_id = "u-internal-jobs-idem"
    now = datetime(2026, 4, 29, 23, 0, tzinfo=UTC)

    async with SessionLocal() as session:
        session.add(_make_user(user_id))
        await session.commit()

    async with SessionLocal() as session:
        heartbeat = HeartbeatService(session)
        user = await session.get(User, user_id)
        first = await heartbeat.schedule_internal_jobs(user, now=now)
        await session.commit()

    async with SessionLocal() as session:
        heartbeat = HeartbeatService(session)
        user = await session.get(User, user_id)
        second = await heartbeat.schedule_internal_jobs(user, now=now)
        await session.commit()

    assert first == 2
    assert second == 0  # idempotent — no duplicates

    async with SessionLocal() as session:
        result = await session.execute(
            __import__("sqlalchemy", fromlist=["select"])
            .select(HeartbeatJob)
            .where(HeartbeatJob.user_id == user_id)
        )
        jobs = list(result.scalars())

    assert len(jobs) == 2


async def test_schedule_internal_jobs_schedules_at_next_quiet_start_outside_quiet() -> None:
    """Jobs created outside quiet hours are scheduled at the next quiet-window start."""
    user_id = "u-internal-jobs-timing"
    # 12:00 UTC → outside quiet hours (22:00–07:00)
    now = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)

    async with SessionLocal() as session:
        session.add(_make_user(user_id))
        await session.commit()

    async with SessionLocal() as session:
        heartbeat = HeartbeatService(session)
        user = await session.get(User, user_id)
        await heartbeat.schedule_internal_jobs(user, now=now)
        await session.commit()

    async with SessionLocal() as session:
        result = await session.execute(
            __import__("sqlalchemy", fromlist=["select"])
            .select(HeartbeatJob)
            .where(HeartbeatJob.user_id == user_id)
            .limit(1)
        )
        job = result.scalar_one()

    # Should be scheduled at 22:00 UTC today (2026-04-29T22:00:00)
    assert job.due_at.hour == 22
    assert job.due_at.date().isoformat() == "2026-04-29"


# ---------------------------------------------------------------------------
# G3/G4: Worker dispatches DreamService / ConsolidatorService during quiet hours
# ---------------------------------------------------------------------------


async def test_process_due_heartbeats_calls_dream_service_for_dream_job() -> None:
    """G3: When a dream job is due during quiet hours, DreamService.run_for_user is called."""
    from healthclaw.workers.app import process_due_heartbeats

    user_id = "u-dream-worker"
    # 23:30 UTC → inside quiet hours (22:00–07:00)
    now_quiet = datetime(2026, 4, 29, 23, 30, tzinfo=UTC)

    async with SessionLocal() as session:
        session.add(_make_user(user_id))
        session.add(
            HeartbeatJob(
                user_id=user_id,
                kind="dream",
                due_at=now_quiet,
                channel="internal",
                payload={"reason": "scheduled_dream"},
                idempotency_key=f"dream:{user_id}:2026-04-29",
            )
        )
        await session.commit()

    with patch(
        "healthclaw.memory.dream.DreamService.run_for_user",
        new_callable=AsyncMock,
        return_value={"status": "ok", "applied": 1, "rejected": 0},
    ) as mock_dream, patch(
        "healthclaw.workers.app.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = now_quiet
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await process_due_heartbeats()

    mock_dream.assert_called_once_with(user_id)
    assert result["sent"] >= 1

    async with SessionLocal() as session:
        result_job = await session.execute(
            __import__("sqlalchemy", fromlist=["select"])
            .select(HeartbeatJob)
            .where(HeartbeatJob.idempotency_key == f"dream:{user_id}:2026-04-29")
        )
        job = result_job.scalar_one()

    assert job.status == "sent"


async def test_process_due_heartbeats_calls_consolidator_for_consolidate_job() -> None:
    """G4: consolidate job due during quiet hours calls ConsolidatorService.run_for_user."""
    from healthclaw.workers.app import process_due_heartbeats

    user_id = "u-consolidate-worker"
    now_quiet = datetime(2026, 4, 29, 23, 45, tzinfo=UTC)

    async with SessionLocal() as session:
        session.add(_make_user(user_id))
        session.add(
            HeartbeatJob(
                user_id=user_id,
                kind="consolidate",
                due_at=now_quiet,
                channel="internal",
                payload={"reason": "scheduled_consolidate"},
                idempotency_key=f"consolidate:{user_id}:2026-04-29",
            )
        )
        await session.commit()

    with patch(
        "healthclaw.memory.consolidator.ConsolidatorService.run_for_user",
        new_callable=AsyncMock,
        return_value=0,
    ) as mock_consolidate, patch(
        "healthclaw.workers.app.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = now_quiet
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await process_due_heartbeats()

    mock_consolidate.assert_called_once_with(user_id)
    assert result["sent"] >= 1

    async with SessionLocal() as session:
        result_job = await session.execute(
            __import__("sqlalchemy", fromlist=["select"])
            .select(HeartbeatJob)
            .where(HeartbeatJob.idempotency_key == f"consolidate:{user_id}:2026-04-29")
        )
        job = result_job.scalar_one()

    assert job.status == "sent"


# ---------------------------------------------------------------------------
# G5: Dream job outside quiet hours is deferred, not run
# ---------------------------------------------------------------------------


async def test_dream_job_outside_quiet_hours_is_deferred() -> None:
    """G5: A dream job processed outside quiet hours is deferred (+30 min), not executed."""
    from healthclaw.workers.app import process_due_heartbeats

    user_id = "u-dream-defer"
    # 12:00 UTC → outside quiet hours
    now_day = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)

    async with SessionLocal() as session:
        session.add(_make_user(user_id))
        session.add(
            HeartbeatJob(
                user_id=user_id,
                kind="dream",
                due_at=now_day,
                channel="internal",
                payload={"reason": "scheduled_dream"},
                idempotency_key=f"dream:{user_id}:2026-04-29-defer",
            )
        )
        await session.commit()

    with patch(
        "healthclaw.memory.dream.DreamService.run_for_user",
        new_callable=AsyncMock,
    ) as mock_dream, patch(
        "healthclaw.workers.app.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = now_day
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await process_due_heartbeats()

    mock_dream.assert_not_called()
    assert result["deferred"] >= 1

    async with SessionLocal() as session:
        result_job = await session.execute(
            __import__("sqlalchemy", fromlist=["select"])
            .select(HeartbeatJob)
            .where(HeartbeatJob.idempotency_key == f"dream:{user_id}:2026-04-29-defer")
        )
        job = result_job.scalar_one()

    assert job.status == "scheduled"  # still scheduled (deferred, not suppressed)
    # DB returns naive UTC datetime; compare with naive equivalent of now_day
    now_naive = now_day.replace(tzinfo=None)
    assert job.due_at > now_naive  # pushed forward


# ---------------------------------------------------------------------------
# G7: loop.close.execute span
# ---------------------------------------------------------------------------


async def test_loop_close_execute_span_emitted(client) -> None:
    """G7: close_open_loop action emits a loop.close.execute span with decided_by=llm."""
    import asyncio

    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter

    from healthclaw.core.config import get_settings
    from healthclaw.db.models import ConversationThread, OpenLoop
    from healthclaw.integrations.openrouter import OpenRouterResult

    class CapturingExporter(SpanExporter):
        def __init__(self):
            self.spans = []

        def export(self, spans):
            self.spans.extend(spans)

        def shutdown(self):
            pass

    exporter = CapturingExporter()
    provider = otel_trace.get_tracer_provider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    get_settings.cache_clear()

    async with SessionLocal() as session:
        user = _make_user("u-span-close")
        thread = ConversationThread(
            id="thread-span-close",
            user_id=user.id,
            channel="web",
            is_primary=True,
            open_loop_count=1,
        )
        loop = OpenLoop(
            id="loop-span-close-1",
            user_id=user.id,
            thread_id=thread.id,
            title="finish the project",
            kind="commitment",
            status="open",
        )
        session.add_all([user, thread, loop])
        await session.commit()

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
        get_settings.cache_clear()

        async def fake_llm(self, messages, **kwargs):
            return OpenRouterResult(
                content=(
                    '{"message":"Great, marking that done.",'
                    '"actions":[{"type":"close_open_loop","id":"loop-span-close-1",'
                    '"summary":"finished it","outcome":"completed"}],'
                    '"memory_proposals":[]}'
                ),
                model="moonshotai/kimi-k2.6",
                usage={"total_tokens": 20},
            )

        with patch(
            "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
            fake_llm,
        ):
            resp = await client.post(
                "/v1/conversations/u-span-close/messages",
                json={"content": "I finished the project!"},
            )
            assert resp.status_code == 200

        get_settings.cache_clear()

    provider.force_flush()
    await asyncio.sleep(0.05)
    provider.force_flush()

    close_spans = [s for s in exporter.spans if s.name == "loop.close.execute"]
    assert len(close_spans) == 1
    attrs = dict(close_spans[0].attributes)
    assert attrs.get("open_loop_id") == "loop-span-close-1"
    assert attrs.get("decided_by") == "llm"
    assert attrs.get("outcome") == "completed"
    assert attrs.get("closed") is True
