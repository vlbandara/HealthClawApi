from __future__ import annotations

from healthclaw.channels.telegram import TelegramAdapter
from healthclaw.core.config import Settings
from healthclaw.voice.transcription import TranscriptionResult


async def test_telegram_text_event() -> None:
    adapter = TelegramAdapter(Settings())
    event = await adapter.event_from_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "from": {"id": 123},
                "chat": {"id": 123},
                "text": "My goal is train consistently",
            },
        }
    )
    assert event is not None
    assert event.user_id == "telegram:123"
    assert event.channel == "telegram"


async def test_telegram_voice_event_uses_transcription(monkeypatch) -> None:
    async def fake_transcribe(self, voice_payload, telegram_bot_token):
        return TranscriptionResult(
            text="I want to sleep earlier tonight",
            confidence=0.8,
            provider="openrouter",
            model="mistralai/voxtral-small-24b-2507",
        )

    monkeypatch.setattr(
        "healthclaw.voice.transcription.TranscriptionService.transcribe_telegram_voice",
        fake_transcribe,
    )
    adapter = TelegramAdapter(Settings(telegram_bot_token="telegram-token"))
    event = await adapter.event_from_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "from": {"id": 123},
                "chat": {"id": 123},
                "voice": {"file_id": "voice-file"},
            },
        }
    )
    assert event is not None
    assert event.content == "I want to sleep earlier tonight"
    assert event.content_type == "voice_transcript"
    assert event.metadata["transcription"]["provider"] == "openrouter"
