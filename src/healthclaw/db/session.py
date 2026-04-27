from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect
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

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


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


async def _ensure_postgres_schema(conn: AsyncConnection) -> None:
    from sqlalchemy import text

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
                pass
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
