from __future__ import annotations

from healthclaw.integrations.health_data import HealthDataProvider, NullHealthDataProvider

__all__ = ["HealthDataProvider", "NullHealthDataProvider", "get_wearable_provider"]


def get_wearable_provider(user_id: str) -> HealthDataProvider:
    """Return the appropriate wearable provider for a user. Extend as integrations are added."""
    return NullHealthDataProvider()
