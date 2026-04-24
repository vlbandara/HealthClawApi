from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import httpx
from pydantic import BaseModel, Field

from healthclaw.core.config import Settings, get_settings
from healthclaw.integrations.openrouter import OpenRouterClient


class TranscriptionResult(BaseModel):
    text: str
    confidence: float
    provider: str
    model: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class TranscriptionService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def transcribe_telegram_voice(
        self,
        voice_payload: dict,
        telegram_bot_token: str | None,
    ) -> TranscriptionResult:
        if not telegram_bot_token:
            return TranscriptionResult(
                text="[Voice note received. Transcription provider is not configured.]",
                confidence=0.0,
                provider="none",
            )
        if not self.settings.openrouter_api_key:
            return TranscriptionResult(
                text="[Voice note received. OpenRouter transcription is not configured.]",
                confidence=0.0,
                provider="none",
            )

        file_id = voice_payload.get("file_id")
        if not file_id:
            return TranscriptionResult(
                text="[Voice note received, but Telegram did not include a file id.]",
                confidence=0.0,
                provider="telegram",
            )

        try:
            audio_bytes, audio_format = await self._download_telegram_file(
                telegram_bot_token, file_id
            )
            result = await OpenRouterClient(self.settings).transcribe_audio(
                audio_bytes,
                audio_format,
            )
        except (httpx.HTTPError, KeyError, RuntimeError):
            return TranscriptionResult(
                text="[Voice note received, but transcription failed. Please send it as text.]",
                confidence=0.0,
                provider="openrouter",
                model=self.settings.openrouter_transcribe_model,
            )

        return TranscriptionResult(
            text=result.content,
            confidence=0.8,
            provider="openrouter",
            model=result.model,
            usage=result.usage,
        )

    async def _download_telegram_file(
        self,
        telegram_bot_token: str,
        file_id: str,
    ) -> tuple[bytes, str]:
        async with httpx.AsyncClient(timeout=30) as client:
            metadata_response = await client.get(
                f"https://api.telegram.org/bot{telegram_bot_token}/getFile",
                params={"file_id": file_id},
            )
            metadata_response.raise_for_status()
            file_path = metadata_response.json()["result"]["file_path"]
            file_response = await client.get(
                f"https://api.telegram.org/file/bot{telegram_bot_token}/{file_path}"
            )
            file_response.raise_for_status()
        return file_response.content, _audio_format_from_path(file_path)


def _audio_format_from_path(file_path: str) -> str:
    suffix = PurePosixPath(file_path).suffix.lower().lstrip(".")
    if suffix == "oga":
        return "ogg"
    return suffix or "ogg"
