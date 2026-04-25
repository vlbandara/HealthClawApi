from __future__ import annotations

from healthclaw.core.config import get_settings
from healthclaw.workers.queue import WorkerSettings, _redis_settings
from healthclaw.workers.tasks import heartbeat_sweep_cron


def test_worker_settings_registers_crons() -> None:
    assert len(WorkerSettings.cron_jobs) == 4
    function_names = {getattr(fn, "__name__", str(fn)) for fn in WorkerSettings.functions}
    assert "run_dream_for_user" in function_names
    assert "run_consolidator_for_user" in function_names


def test_worker_redis_settings_parse_url(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("REDIS_URL", "redis://cache.internal:6380/3")
    redis_settings = _redis_settings()
    assert redis_settings.host == "cache.internal"
    assert redis_settings.port == 6380
    assert redis_settings.database == 3
    get_settings.cache_clear()


async def test_heartbeat_sweep_cron_reports_combined_results(monkeypatch) -> None:
    async def fake_process_due_reminders() -> dict[str, int]:
        return {"due": 2, "sent": 1, "suppressed": 0, "deferred": 1, "failed": 0}

    async def fake_process_due_heartbeats() -> dict[str, int]:
        return {
            "ritual_jobs": 1,
            "refresh_jobs": 2,
            "open_loop_jobs": 1,
            "due": 3,
            "sent": 1,
            "suppressed": 1,
            "deferred": 0,
            "soft_skipped": 1,
            "failed": 0,
        }

    monkeypatch.setattr(
        "healthclaw.workers.app.process_due_reminders",
        fake_process_due_reminders,
    )
    monkeypatch.setattr(
        "healthclaw.workers.app.process_due_heartbeats",
        fake_process_due_heartbeats,
    )

    result = await heartbeat_sweep_cron({})
    assert result["reminders"]["sent"] == 1
    assert result["heartbeats"]["ritual_jobs"] == 1
    assert result["heartbeats"]["soft_skipped"] == 1
