from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any


class HealthDataProvider(ABC):
    """Integration boundary for Open Wearables and healthcare applications."""

    @abstractmethod
    async def get_sleep_summary(self, user_id: str, start: date, end: date) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_recovery_summary(self, user_id: str, start: date, end: date) -> dict[str, Any]:
        raise NotImplementedError

    async def get_latest_recovery(self, user_id: str) -> dict[str, Any]:
        """Convenience: return the most recent recovery snapshot without requiring date math."""
        from datetime import UTC, datetime, timedelta

        today = datetime.now(UTC).date()
        yesterday = today - timedelta(days=1)
        return await self.get_recovery_summary(user_id, yesterday, today)


class NullHealthDataProvider(HealthDataProvider):
    async def get_sleep_summary(self, user_id: str, start: date, end: date) -> dict[str, Any]:
        return {"available": False, "reason": "wearable_integration_not_enabled"}

    async def get_recovery_summary(self, user_id: str, start: date, end: date) -> dict[str, Any]:
        return {"available": False, "reason": "wearable_integration_not_enabled"}
