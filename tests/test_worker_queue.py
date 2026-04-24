from __future__ import annotations

from healthclaw.workers.queue import WorkerSettings


def test_worker_settings_registers_crons() -> None:
    assert len(WorkerSettings.cron_jobs) == 4
    function_names = {getattr(fn, "__name__", str(fn)) for fn in WorkerSettings.functions}
    assert "run_dream_for_user" in function_names
    assert "run_consolidator_for_user" in function_names
