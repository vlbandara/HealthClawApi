#!/usr/bin/env python3
"""
Healthclaw local test runner — Telegram polling + background workers + rich console.

No ngrok required. Uses Telegram's getUpdates long-polling so the bot works
entirely from your machine. Every inner-loop event is printed to the console
so you can observe salience, synthesis, skill activation, and memory mutations
in real time.

Usage:
    uv run python scripts/local_bot.py

Environment variables read from .env.local (falls back to .env).
Requires: TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY.
Database: SQLite (auto-created at ./healthclaw_local.db).

Keyboard shortcuts:
    Ctrl+C      — graceful shutdown
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

# ── Load .env.local before importing anything from healthclaw ─────────────────

_REPO_ROOT = Path(__file__).parent.parent
_ENV_LOCAL = _REPO_ROOT / ".env.local"
_ENV_FALLBACK = _REPO_ROOT / ".env"

_env_file = _ENV_LOCAL if _ENV_LOCAL.exists() else _ENV_FALLBACK
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

# Override DB to local SQLite and enable AUTO_CREATE_DB
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./healthclaw_local.db"
os.environ["AUTO_CREATE_DB"] = "true"

sys.path.insert(0, str(_REPO_ROOT / "src"))

# ── Colour helpers ─────────────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"
WHITE = "\033[37m"
BG_DARK = "\033[40m"


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _box(label: str, colour: str, content: str, width: int = 72) -> str:
    lines = []
    lines.append(f"{colour}{BOLD}┌─ {label} {'─' * max(0, width - len(label) - 4)}┐{RESET}")
    for line in content.splitlines():
        wrapped = textwrap.wrap(line, width - 4) or [""]
        for wl in wrapped:
            lines.append(f"{colour}│{RESET}  {wl}")
    lines.append(f"{colour}{'─' * (width + 2)}{RESET}")
    return "\n".join(lines)


# ── Structured logging interceptor ────────────────────────────────────────────


class ObserveHandler(logging.Handler):
    """Print selected log records in a rich format."""

    WATCH = {
        "healthclaw.inner.tick": (CYAN, "🧠 inner_tick"),
        "healthclaw.inner.salience": (YELLOW, "⚡ salience"),
        "healthclaw.inner.synthesizer": (MAGENTA, "✨ synthesizer"),
        "healthclaw.inner.speech_gate": (GREEN, "🔊 speech_gate"),
        "healthclaw.inner.deliberation": (CYAN, "💭 deliberation"),
        "healthclaw.memory.dream": (BLUE, "💤 dream"),
        "healthclaw.memory.consolidator": (BLUE, "📦 consolidator"),
        "healthclaw.agent.skill_activator": (YELLOW, "🏥 skills"),
        "healthclaw.integrations.tavily": (GREEN, "🌐 web_search"),
        "healthclaw.memory.reranker": (DIM + WHITE, "🔍 reranker"),
        "healthclaw.sensing.poller": (CYAN, "📡 sensing"),
        "healthclaw.services.conversation": (WHITE, "💬 convo"),
        "healthclaw.workers": (DIM + WHITE, "⚙️  worker"),
        "healthclaw.proactivity": (MAGENTA, "📣 proactive"),
    }

    def emit(self, record: logging.LogRecord) -> None:
        for prefix, (colour, icon) in self.WATCH.items():
            if record.name.startswith(prefix):
                msg = self.format(record)
                level = record.levelname[0]
                if record.levelno >= logging.WARNING:
                    colour = YELLOW if record.levelno == logging.WARNING else RED
                print(
                    f"{DIM}{_ts()}{RESET} {colour}{icon:<20}{RESET} "
                    f"{DIM}[{level}]{RESET} {msg}",
                    flush=True,
                )
                return


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    handler = ObserveHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    # Suppress noisy low-level loggers
    for noisy in ("aiosqlite", "sqlalchemy", "asyncio", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Telegram polling ───────────────────────────────────────────────────────────

TELEGRAM_API = "https://api.telegram.org"


class TelegramPoller:
    def __init__(self, token: str) -> None:
        self.token = token
        self._offset = 0
        self._base = f"{TELEGRAM_API}/bot{token}"

    async def get_me(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self._base}/getMe")
            r.raise_for_status()
            return r.json().get("result", {})

    async def delete_webhook(self) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{self._base}/deleteWebhook", json={"drop_pending_updates": False})

    async def get_updates(self, timeout: int = 20) -> list[dict[str, Any]]:
        params = {"timeout": timeout, "offset": self._offset, "allowed_updates": ["message"]}
        async with httpx.AsyncClient(timeout=timeout + 5) as client:
            try:
                r = await client.get(f"{self._base}/getUpdates", params=params)
                r.raise_for_status()
                updates = r.json().get("result", [])
                if updates:
                    self._offset = updates[-1]["update_id"] + 1
                return updates
            except (httpx.TimeoutException, httpx.ConnectError):
                return []

    async def send(self, chat_id: str | int, text: str) -> None:
        """Send a message directly (used for bootstrap / system messages)."""
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"{self._base}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )


# ── Background worker loops ────────────────────────────────────────────────────


async def _worker_loop(name: str, coro_fn, interval_s: int) -> None:
    """Run *coro_fn()* every *interval_s* seconds, logging errors."""
    log = logging.getLogger("healthclaw.workers")
    while True:
        try:
            await coro_fn()
        except Exception as exc:
            log.warning("%s error: %s", name, exc)
        await asyncio.sleep(interval_s)


async def _heartbeat_sweep() -> None:
    from healthclaw.workers.app import process_due_heartbeats, process_due_reminders
    await process_due_reminders()
    await process_due_heartbeats()


async def _sensing_sweep() -> None:
    from healthclaw.db.session import SessionLocal
    from healthclaw.sensing.poller import run_sensing_poll
    async with SessionLocal() as session:
        result = await run_sensing_poll(session)
    if any(v > 0 for k, v in result.items() if k != "users_polled"):
        logging.getLogger("healthclaw.sensing.poller").info(
            "sensing sweep: %s", result
        )


async def _inner_tick_sweep() -> None:
    from datetime import timedelta

    from sqlalchemy import select

    from healthclaw.db.models import Signal, User
    from healthclaw.db.session import SessionLocal
    from healthclaw.inner.tick import run_inner_tick

    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=15)
    async with SessionLocal() as session:
        result = await session.execute(
            select(User.id)
            .where(User.proactive_enabled.is_(True))
            .join(Signal, Signal.user_id == User.id)
            .where(Signal.observed_at >= cutoff)
            .distinct()
        )
        user_ids = [row[0] for row in result.all()]

    for user_id in user_ids:
        from healthclaw.db.session import SessionLocal as SL
        async with SL() as session:
            outcome = await run_inner_tick(user_id, session)
            await session.commit()
        if outcome.get("status") == "ticked":
            logging.getLogger("healthclaw.inner.tick").debug(
                "tick user=%s salience=%.3f status=%s",
                user_id,
                outcome.get("salience", 0),
                outcome.get("status"),
            )


# ── Bootstrap helpers ──────────────────────────────────────────────────────────


async def _ensure_db() -> None:
    """Create all tables if they don't exist yet."""
    from healthclaw.db.models import Base
    from healthclaw.db.session import engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _bootstrap_user(
    user_id: str,
    external_telegram_id: str,
    settings: Any,
    language_code: str | None = None,
) -> None:
    """Seed motives and apply locale hints for a newly seen user.

    Location is no longer hardcoded. The companion will ask the user's location
    naturally when timezone_confidence is low. If a Telegram language_code was
    captured we use it as a low-confidence hint to pre-populate timezone.
    """
    from healthclaw.db.models import User
    from healthclaw.db.session import SessionLocal

    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            return  # will be created by ConversationService on first message

        # Apply locale hint from Telegram language_code if TZ not yet confirmed
        tz_conf = getattr(user, "timezone_confidence", 0.0) or 0.0
        if language_code and tz_conf < 0.6:
            from healthclaw.integrations.locale_hints import infer_locale_hints
            hints = infer_locale_hints(language_code=language_code)
            if hints.confidence >= 0.6 and hints.tz_guess and hints.tz_guess != "UTC":
                try:
                    from zoneinfo import ZoneInfo
                    ZoneInfo(hints.tz_guess)  # validate before persisting
                    user.timezone = hints.tz_guess
                    user.timezone_confidence = hints.confidence
                    logging.getLogger("healthclaw.workers").info(
                        "bootstrap: tz=%s conf=%.2f from lang=%s for user %s",
                        hints.tz_guess, hints.confidence, language_code, user_id,
                    )
                except Exception:
                    pass  # Invalid IANA TZ — skip gracefully

        if settings.motives_enabled:
            from healthclaw.inner.motives import MotiveService
            seeded = await MotiveService(session).seed_defaults(
                user_id, seed_weight=settings.motive_seed_weight
            )
            if seeded:
                logging.getLogger("healthclaw.workers").info(
                    "bootstrap: seeded %d motives for user %s", seeded, user_id
                )

        await session.commit()


# ── Main loop ──────────────────────────────────────────────────────────────────


async def main() -> None:
    _setup_logging()

    from healthclaw.core.config import get_settings
    settings = get_settings()

    token = settings.telegram_bot_token
    if not token:
        print(f"{RED}ERROR: TELEGRAM_BOT_TOKEN not set in .env.local{RESET}")
        sys.exit(1)

    if not settings.openrouter_api_key:
        print(f"{YELLOW}WARNING: OPENROUTER_API_KEY not set — LLM calls will fail{RESET}")

    # Create DB tables
    await _ensure_db()

    poller = TelegramPoller(token)

    # Delete any existing webhook so polling works
    await poller.delete_webhook()

    me = await poller.get_me()
    bot_name = me.get("username", "unknown")
    bot_id = me.get("id", "?")

    print()
    print(_box(
        "Healthclaw Local Bot",
        GREEN,
        f"Bot:    @{bot_name}  (id {bot_id})\n"
        f"DB:     healthclaw_local.db (SQLite)\n"
        f"Model:  {settings.openrouter_chat_model}\n"
        f"Skills: {'ON' if settings.health_skills_enabled else 'off'} | "
        f"Synth: {'ON' if settings.inner_synthesizer_enabled else 'off'} | "
        f"Motives: {'ON' if settings.motives_enabled else 'off'}\n"
        f"Web:    {'ON' if settings.web_search_enabled else 'off'}\n"
        "\nStart a conversation on Telegram. Press Ctrl+C to stop.",
    ))
    print()

    # Start background workers
    asyncio.create_task(_worker_loop("heartbeat", _heartbeat_sweep, 30))
    asyncio.create_task(_worker_loop("sensing", _sensing_sweep, 60 * 5))
    asyncio.create_task(_worker_loop("inner_tick", _inner_tick_sweep, 60))

    from healthclaw.channels.telegram import TelegramAdapter
    from healthclaw.db.session import SessionLocal
    from healthclaw.services.conversation import ConversationService

    adapter = TelegramAdapter(settings)
    seen_user_ids: set[str] = set()

    print(f"{DIM}Polling for messages…{RESET}\n")

    while True:
        updates = await poller.get_updates(timeout=20)

        for update in updates:
            event = await adapter.event_from_update(update)
            if event is None:
                continue

            chat_id = (
                update.get("message", {}).get("chat", {}).get("id")
                or event.external_user_id
            )

            # Print incoming message
            user_text = event.content or "(voice)"
            print(
                f"{_ts()} {GREEN}▶ IN{RESET}  "
                f"{BOLD}[{event.user_id}]{RESET}  {user_text[:120]}"
            )

            t0 = time.monotonic()
            try:
                async with SessionLocal() as session:
                    svc = ConversationService(session)
                    response = await svc.handle_event(event)
                    await session.commit()

                elapsed_ms = int((time.monotonic() - t0) * 1000)

                # Print outgoing response
                reply = response.response or ""
                print(
                    f"{_ts()} {CYAN}◀ OUT{RESET} "
                    f"{DIM}({elapsed_ms}ms){RESET}  {reply[:200]}"
                )
                if len(reply) > 200:
                    print(f"      {DIM}…[{len(reply)} chars total]{RESET}")

                # Send back to Telegram
                if not response.idempotent_replay and reply and chat_id:
                    await adapter.send_message(str(chat_id), reply)

                # Bootstrap user on first message — pass language_code for TZ hint
                if event.user_id not in seen_user_ids:
                    seen_user_ids.add(event.user_id)
                    lang_code = (
                        update.get("message", {})
                        .get("from", {})
                        .get("language_code")
                    )
                    asyncio.create_task(
                        _bootstrap_user(
                            event.user_id, event.external_user_id, settings,
                            language_code=lang_code,
                        )
                    )

            except Exception as exc:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                print(f"{_ts()} {RED}✗ ERR{RESET} ({elapsed_ms}ms)  {exc}")
                logging.getLogger("healthclaw.workers").exception(
                    "handle_event failed: %s", exc
                )

        # Brief pause between empty poll cycles
        if not updates:
            await asyncio.sleep(0.1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Shutting down.{RESET}")
