from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from healthclaw.core.config import get_settings
from healthclaw.workers.tasks import (
    autonomous_wake_sweep,
    consolidator_sweep_cron,
    dream_sweep_cron,
    embed_memory_batch,
    heartbeat_sweep_cron,
    inner_tick_cron,
    process_due_heartbeats_task,
    process_due_reminders_task,
    run_consolidator_for_user,
    run_dream_for_user,
    sensing_poll_cron,
)


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    url = settings.redis_url
    url = url.removeprefix("redis://")
    db = 0
    if "/" in url:
        url, db_str = url.rsplit("/", 1)
        try:
            db = int(db_str)
        except ValueError:
            db = 0
    port = 6379
    if ":" in url:
        host, port_str = url.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 6379
    else:
        host = url
    return RedisSettings(host=host, port=port, database=db)


async def startup(ctx: dict) -> None:
    pass


async def shutdown(ctx: dict) -> None:
    pass


class WorkerSettings:
    """ARQ WorkerSettings. Run with: arq healthclaw.workers.queue.WorkerSettings"""

    functions = [
        process_due_reminders_task,
        process_due_heartbeats_task,
        run_consolidator_for_user,
        run_dream_for_user,
        embed_memory_batch,
    ]

    cron_jobs = [
        cron(heartbeat_sweep_cron, second={0}, run_at_startup=False),
        cron(autonomous_wake_sweep, minute={0, 15, 30, 45}, second={0}, run_at_startup=False),
        cron(sensing_poll_cron, minute={0, 10, 20, 30, 40, 50}, second={0}, run_at_startup=False),
        cron(
            inner_tick_cron,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            second={0},
            run_at_startup=False,
        ),
        cron(consolidator_sweep_cron, hour={4}, minute={0}, second={0}),
        cron(dream_sweep_cron, hour={4}, minute={15}, second={0}),
    ]

    redis_settings = _redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 50
    job_timeout = 300
    keep_result = 600
    queue_name = "arq:healthclaw"
