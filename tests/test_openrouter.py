from __future__ import annotations

import httpx

from healthclaw.core.config import Settings
from healthclaw.integrations.openrouter import OpenRouterClient


def test_default_chat_models_use_natural_companion_tier(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_CHAT_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_CHAT_FALLBACK_MODELS", raising=False)
    client = OpenRouterClient(Settings(_env_file=None))

    assert client.chat_models() == [
        "moonshotai/kimi-k2.6",
        "minimax/minimax-m2.7",
        "openai/gpt-5.4-mini",
    ]


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    def json(self) -> dict:
        return self.payload


async def test_openrouter_chat_falls_back_to_next_model(monkeypatch) -> None:
    calls: list[str] = []

    class FakeAsyncClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, headers: dict, json: dict) -> FakeResponse:
            calls.append(json["model"])
            if json["model"] == "primary/model":
                return FakeResponse({}, status_code=503)
            return FakeResponse(
                {
                    "model": json["model"],
                    "choices": [{"message": {"content": "fallback reply"}}],
                    "usage": {"total_tokens": 12},
                }
            )

    monkeypatch.setattr("healthclaw.integrations.openrouter.httpx.AsyncClient", FakeAsyncClient)
    client = OpenRouterClient(
        Settings(
            openrouter_api_key="test-key",
            openrouter_chat_model="primary/model",
            openrouter_chat_fallback_models="fallback/model",
        )
    )

    result = await client.chat_completion([{"role": "user", "content": "hello"}])

    assert calls == ["primary/model", "fallback/model"]
    assert result.content == "fallback reply"
    assert result.model == "fallback/model"
    assert result.usage["total_tokens"] == 12


async def test_openrouter_transcription_encodes_audio(monkeypatch) -> None:
    request_payloads: list[dict] = []

    class FakeAsyncClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, headers: dict, json: dict) -> FakeResponse:
            request_payloads.append(json)
            return FakeResponse(
                {
                    "model": "mistralai/voxtral-small-24b-2507",
                    "choices": [{"message": {"content": "transcribed text"}}],
                    "usage": {"total_tokens": 20},
                }
            )

    monkeypatch.setattr("healthclaw.integrations.openrouter.httpx.AsyncClient", FakeAsyncClient)
    client = OpenRouterClient(Settings(openrouter_api_key="test-key"))

    result = await client.transcribe_audio(b"audio", "ogg")

    audio = request_payloads[0]["messages"][0]["content"][1]["input_audio"]
    assert audio["format"] == "ogg"
    assert audio["data"] == "YXVkaW8="
    assert result.content == "transcribed text"
