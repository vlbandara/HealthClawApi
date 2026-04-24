from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from healthclaw.schemas.events import ConversationEvent


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    provider_message_id: str | None = None
    error: str | None = None


class ChannelAdapter(ABC):
    channel: str

    @abstractmethod
    async def event_from_payload(self, payload: dict[str, Any]) -> ConversationEvent | None:
        raise NotImplementedError

    def can_send_proactive(self) -> bool:
        return True

    async def send_status(self, external_id: str, status: str) -> None:
        return None

    @abstractmethod
    async def send_message(self, external_id: str, text: str) -> DeliveryResult:
        raise NotImplementedError
