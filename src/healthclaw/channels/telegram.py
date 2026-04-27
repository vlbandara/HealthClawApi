from __future__ import annotations

from typing import Any

import httpx

from healthclaw.channels.base import ChannelAdapter, DeliveryResult
from healthclaw.core.config import Settings
from healthclaw.schemas.events import ConversationEvent
from healthclaw.voice.transcription import TranscriptionService


class TelegramAdapter(ChannelAdapter):
    channel = "telegram"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.transcription = TranscriptionService(settings)

    def _resolve_token(self, bot_token: str | None) -> str | None:
        return bot_token or self.settings.telegram_bot_token

    async def event_from_payload(self, payload: dict[str, Any]) -> ConversationEvent | None:
        return await self.event_from_update(payload)

    async def event_from_update(
        self, update: dict[str, Any], *, bot_token: str | None = None
    ) -> ConversationEvent | None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return None
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        external_user_id = str(sender.get("id") or chat.get("id"))
        user_id = f"telegram:{external_user_id}"
        metadata = {
            "telegram_update_id": update.get("update_id"),
            "telegram_chat_id": chat.get("id"),
            "telegram_message_id": message.get("message_id"),
        }
        if text := message.get("text"):
            return ConversationEvent(
                user_id=user_id,
                external_user_id=external_user_id,
                channel="telegram",
                content=text,
                metadata=metadata,
                idempotency_key=f"telegram:{update.get('update_id')}",
            )
        if voice := message.get("voice"):
            transcript = await self.transcription.transcribe_telegram_voice(
                voice, self._resolve_token(bot_token)
            )
            return ConversationEvent(
                user_id=user_id,
                external_user_id=external_user_id,
                channel="telegram",
                content=transcript.text,
                content_type="voice_transcript",
                metadata={**metadata, "voice": voice, "transcription": transcript.model_dump()},
                idempotency_key=f"telegram:{update.get('update_id')}",
            )
        return None

    async def send_status(
        self, external_id: str, status: str, *, bot_token: str | None = None
    ) -> None:
        token = self._resolve_token(bot_token)
        if not token or status != "typing":
            return
        url = f"https://api.telegram.org/bot{token}/sendChatAction"
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"chat_id": external_id, "action": "typing"})

    async def send_message(
        self, external_id: str, text: str, *, bot_token: str | None = None
    ) -> DeliveryResult:
        token = self._resolve_token(bot_token)
        if not token:
            return DeliveryResult(delivered=False, error="telegram_bot_token_missing")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json={"chat_id": external_id, "text": text})
            response.raise_for_status()
        payload = response.json()
        provider_message_id = None
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, dict) and result.get("message_id") is not None:
                provider_message_id = str(result["message_id"])
        return DeliveryResult(delivered=True, provider_message_id=provider_message_id)
