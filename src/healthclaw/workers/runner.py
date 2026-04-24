from __future__ import annotations

import asyncio
import logging
import signal

from sqlalchemy import text

from healthclaw.db.session import engine
from healthclaw.workers.app import process_due_heartbeats, process_due_reminders

logger = logging.getLogger("worker")


async def wait_for_schema(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1 FROM reminders LIMIT 1"))
            return
        except Exception as exc:
            logger.warning("Worker waiting for database schema: %s", exc)

        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            continue


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    await wait_for_schema(stop)

    while not stop.is_set():
        try:
            await process_due_reminders()
            await process_due_heartbeats()
        except Exception as e:
            logger.error(f"Worker encountered error: {e}", exc_info=True)
            
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            continue


if __name__ == "__main__":
    asyncio.run(main())
