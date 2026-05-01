from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    kind: str
    value: dict[str, Any]
    source: str
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    dedup_key: str = ""

    def __post_init__(self) -> None:
        if not self.dedup_key:
            self.dedup_key = f"{self.kind}:{self.source}:{self.observed_at.date().isoformat()}"


class SignalBus:
    """Write afferent perception events to the signals table.

    Deduplication: if a row with the same dedup_key already exists, we skip the insert
    and return the existing row's id. This prevents the inner tick from re-processing
    unchanged data (e.g., polling the same hot weather all afternoon).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def publish(self, user_id: str, signal: Signal) -> tuple[str, bool]:
        """Insert the signal if new. Returns (signal_id, is_new)."""
        from sqlalchemy import select

        from healthclaw.db.models import Signal as SignalModel, new_id

        result = await self.session.execute(
            select(SignalModel).where(
                SignalModel.user_id == user_id,
                SignalModel.dedup_key == signal.dedup_key,
            ).limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing.id, False

        row_id = new_id()
        row = SignalModel(
            id=row_id,
            user_id=user_id,
            kind=signal.kind,
            value=signal.value,
            observed_at=signal.observed_at,
            source=signal.source,
            dedup_key=signal.dedup_key,
        )
        self.session.add(row)
        await self.session.flush()
        logger.debug("SignalBus published %s for user %s (%s)", signal.kind, user_id, row_id)
        return row_id, True

    async def recent_signals(
        self,
        user_id: str,
        *,
        window_minutes: int = 30,
        kinds: list[str] | None = None,
    ) -> list[Any]:
        from datetime import timedelta

        from sqlalchemy import select

        from healthclaw.db.models import Signal as SignalModel

        cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
        q = select(SignalModel).where(
            SignalModel.user_id == user_id,
            SignalModel.observed_at >= cutoff,
        )
        if kinds:
            q = q.where(SignalModel.kind.in_(kinds))
        q = q.order_by(SignalModel.observed_at.desc())
        result = await self.session.execute(q)
        return list(result.scalars())
