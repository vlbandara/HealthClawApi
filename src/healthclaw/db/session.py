from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from healthclaw.agent.soul import HEALTHCLAW_IDENTITY, identity_config
from healthclaw.core.config import get_settings
from healthclaw.db.models import Base, Identity

logger = logging.getLogger(__name__)

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

_ALEMBIC_REVISION_ALIASES = {
    "0013_natural_onboarding": "0013_ws7_naturalness_pass",
}


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def _sync_database_url() -> str:
    return settings.database_url


def _alembic_config() -> Config:
    candidates = [
        Path.cwd() / "alembic.ini",
        Path("/app/alembic.ini"),
        Path(__file__).resolve().parents[3] / "alembic.ini",
    ]
    config_path = next((path for path in candidates if path.exists()), None)
    if config_path is None:
        raise FileNotFoundError("Could not locate alembic.ini for database bootstrap")

    config = Config(str(config_path))
    config.set_main_option("sqlalchemy.url", _sync_database_url())
    config.set_main_option("script_location", str(config_path.parent / "alembic"))
    return config


async def _run_alembic(action: str) -> None:
    config = _alembic_config()
    if action == "upgrade":
        await asyncio.to_thread(command.upgrade, config, "head")
        return
    if action == "stamp":
        await asyncio.to_thread(command.stamp, config, "head")
        return
    raise ValueError(f"Unsupported alembic action: {action}")


async def _has_table(conn: AsyncConnection, table_name: str) -> bool:
    return await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table(table_name))


async def _has_column(conn: AsyncConnection, table_name: str, column_name: str) -> bool:
    return await conn.run_sync(
        lambda sync_conn: any(
            column["name"] == column_name for column in inspect(sync_conn).get_columns(table_name)
        )
    )


async def _reconcile_revision_alias(conn: AsyncConnection) -> str | None:
    current_revision = await conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    if not current_revision:
        return None

    replacement = _ALEMBIC_REVISION_ALIASES.get(str(current_revision))
    if replacement is None:
        return str(current_revision)

    has_ws7_schema = await _has_column(conn, "users", "timezone_confidence") and await _has_column(
        conn, "open_loops", "surface_count"
    )
    if not has_ws7_schema:
        return str(current_revision)

    await conn.execute(
        text("UPDATE alembic_version SET version_num = :version_num"),
        {"version_num": replacement},
    )
    logger.warning(
        "Reconciled Alembic revision alias from %s to %s for existing schema",
        current_revision,
        replacement,
    )
    return replacement


async def _ensure_postgres_schema(conn: AsyncConnection) -> None:
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await conn.execute(
        text("ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding_vec vector(1536)")
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_memories_embedding_user "
            "ON memories USING ivfflat (embedding_vec vector_cosine_ops) WITH (lists = 100)"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE user_engagement_states "
            "ADD COLUMN IF NOT EXISTS sentiment_ema DOUBLE PRECISION NOT NULL DEFAULT 0.0"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE user_engagement_states "
            "ADD COLUMN IF NOT EXISTS voice_text_ratio DOUBLE PRECISION NOT NULL DEFAULT 0.0"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE user_engagement_states "
            "ADD COLUMN IF NOT EXISTS reply_latency_seconds_ema DOUBLE PRECISION"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE user_engagement_states "
            "ADD COLUMN IF NOT EXISTS last_meaningful_exchange_at TIMESTAMPTZ"
        )
    )


async def init_models() -> None:
    async with engine.begin() as conn:
        is_postgres = conn.dialect.name == "postgresql"
        if not is_postgres:
            await conn.run_sync(Base.metadata.create_all)
        else:
            has_version_table = await _has_table(conn, "alembic_version")
            has_app_tables = await _has_table(conn, "users")
            if has_version_table:
                await _reconcile_revision_alias(conn)
            elif has_app_tables:
                await conn.run_sync(Base.metadata.create_all)
                await _ensure_postgres_schema(conn)
            else:
                pass

    if is_postgres:
        if has_version_table:
            await _run_alembic("upgrade")
        elif has_app_tables:
            await _run_alembic("stamp")
        else:
            await _run_alembic("upgrade")
        async with engine.begin() as conn:
            await _ensure_postgres_schema(conn)
    async with SessionLocal() as session:
        identity = await session.get(Identity, f"healthclaw-v{HEALTHCLAW_IDENTITY['version']}")
        if identity is None:
            session.add(
                Identity(
                    id=f"healthclaw-v{HEALTHCLAW_IDENTITY['version']}",
                    name="Healthclaw",
                    version=HEALTHCLAW_IDENTITY["version"],
                    config=identity_config(),
                    status="active",
                )
            )
            await session.commit()
