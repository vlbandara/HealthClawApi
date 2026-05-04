"""Bootstrap user_pattern rows from purely observable signals.

Runs after the first N turns of a new user. No LLM call — uses signal statistics only.
Writes low-confidence (0.3) user_pattern rows so the synthesizer has *something* to read
before the Dream sweep accumulates enough data for its full LLM pattern-extraction pass.

Pattern rows written:
  user_pattern:reply_style  — avg message length, brevity signal, response lag
"""
from __future__ import annotations

import logging
from statistics import mean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.db.models import Memory, Message, utc_now

logger = logging.getLogger(__name__)

_REPLY_STYLE_KEY = "reply_style"
_CONFIDENCE = 0.3
_EXPIRES_DAYS = 30


async def seed_observable_patterns(
    user_id: str,
    session: AsyncSession,
    min_turns: int = 5,
) -> bool:
    """Write user_pattern:reply_style from observable message statistics.

    Returns True if a new row was written; False if skipped (too few turns, or row exists).
    Only writes if user_pattern:reply_style does not already exist at confidence >= 0.3.
    """
    # Don't overwrite an existing row with equal or higher confidence
    existing = await session.execute(
        select(Memory).where(
            Memory.user_id == user_id,
            Memory.kind == "user_pattern",
            Memory.key == _REPLY_STYLE_KEY,
            Memory.is_active.is_(True),
        ).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return False  # already have a row — let dream sweep update it

    # Load user messages
    result = await session.execute(
        select(Message)
        .where(Message.user_id == user_id, Message.role == "user")
        .order_by(Message.created_at.asc())
        .limit(50)
    )
    user_msgs = list(result.scalars())

    if len(user_msgs) < min_turns:
        return False  # not enough data

    # Compute basic stats
    lengths = [len(m.content or "") for m in user_msgs]
    avg_length = round(mean(lengths), 1)
    brevity_ratio = sum(1 for ln in lengths if ln <= 20) / len(lengths)
    # Estimate reply cadence (seconds between consecutive user messages)
    lags: list[float] = []
    for i in range(1, len(user_msgs)):
        if user_msgs[i].created_at and user_msgs[i - 1].created_at:
            diff = (user_msgs[i].created_at - user_msgs[i - 1].created_at).total_seconds()
            if 5 < diff < 3600:  # ignore near-instant or very stale
                lags.append(diff)
    avg_lag_seconds = round(mean(lags), 0) if lags else None

    value = {
        "avg_message_length": avg_length,
        "brevity_ratio": round(brevity_ratio, 2),
        "avg_reply_lag_seconds": avg_lag_seconds,
        "style": "brief" if brevity_ratio >= 0.6 else ("verbose" if avg_length > 120 else "mixed"),
        "source": "bootstrap_heuristic",
        "message_count": len(user_msgs),
    }

    from datetime import timedelta
    now = utc_now()
    mem = Memory(
        user_id=user_id,
        kind="user_pattern",
        key=_REPLY_STYLE_KEY,
        value=value,
        confidence=_CONFIDENCE,
        freshness_score=0.5,
        is_active=True,
        expires_at=now + timedelta(days=_EXPIRES_DAYS),
        source="bootstrap_patterns",
        created_at=now,
        updated_at=now,
    )
    session.add(mem)
    await session.flush()
    logger.info(
        "bootstrap_patterns: wrote user_pattern:reply_style for user=%s style=%s",
        user_id, value["style"],
    )
    return True
