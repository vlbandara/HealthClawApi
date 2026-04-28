from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx

from healthclaw.core.config import Settings
from healthclaw.core.tracing import start_span

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass(frozen=True)
class OpenRouterResult:
    content: str
    model: str
    usage: dict[str, Any]


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openrouter_api_key)

    def chat_models(self) -> list[str]:
        fallbacks = [
            model.strip()
            for model in self.settings.openrouter_chat_fallback_models.split(",")
            if model.strip()
        ]
        return [self.settings.openrouter_chat_model, *fallbacks]

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        if self.settings.openrouter_site_url:
            headers["HTTP-Referer"] = self.settings.openrouter_site_url
        if self.settings.openrouter_app_name:
            headers["X-Title"] = self.settings.openrouter_app_name
        return headers

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 180,
        temperature: float = 0.4,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OpenRouterResult:
        """Call a chat model. Pass `model` to use a specific model instead of the fallback chain."""
        if not self.enabled:
            raise RuntimeError("OpenRouter API key is not configured")

        model_list = [model] if model else self.chat_models()
        last_error: Exception | None = None
        attempt_index = 0
        async with start_span("openrouter.chat", attributes=metadata) as span:
            async with httpx.AsyncClient(timeout=30) as client:
                for candidate_model in model_list:
                    attempt_index += 1
                    try:
                        response = await client.post(
                            OPENROUTER_CHAT_URL,
                            headers=self._headers(),
                            json={
                                "model": candidate_model,
                                "messages": messages,
                                "max_tokens": max_tokens,
                                "temperature": temperature,
                            },
                        )
                        response.raise_for_status()
                        payload = response.json()
                        content = payload["choices"][0]["message"].get("content") or ""
                        if content.strip():
                            usage = payload.get("usage") or {}
                            span.set_attribute(
                                "usage.prompt_tokens",
                                usage.get("prompt_tokens", 0),
                            )
                            span.set_attribute(
                                "usage.completion_tokens",
                                usage.get("completion_tokens", 0),
                            )
                            span.set_attribute(
                                "model_resolved",
                                payload.get("model") or candidate_model,
                            )
                            span.set_attribute("attempt_index", attempt_index)
                            return OpenRouterResult(
                                content=content.strip(),
                                model=payload.get("model") or candidate_model,
                                usage=usage,
                            )
                        last_error = RuntimeError(
                            f"OpenRouter returned empty content for {candidate_model}"
                        )
                    except (KeyError, IndexError, httpx.HTTPError, RuntimeError) as exc:
                        last_error = exc
                        continue

            raise RuntimeError("OpenRouter chat completion failed") from last_error

    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        audio_format: str,
        *,
        prompt: str = "Transcribe this Telegram wellness check-in voice note.",
    ) -> OpenRouterResult:
        if not self.enabled:
            raise RuntimeError("OpenRouter API key is not configured")

        encoded = base64.b64encode(audio_bytes).decode("ascii")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                OPENROUTER_CHAT_URL,
                headers=self._headers(),
                json={
                    "model": self.settings.openrouter_transcribe_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "input_audio",
                                    "input_audio": {
                                        "data": encoded,
                                        "format": audio_format,
                                    },
                                },
                            ],
                        }
                    ],
                    "max_tokens": 1200,
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            payload = response.json()
        content = payload["choices"][0]["message"].get("content") or ""
        if not content.strip():
            raise RuntimeError("OpenRouter returned empty transcription")
        return OpenRouterResult(
            content=content.strip(),
            model=payload.get("model") or self.settings.openrouter_transcribe_model,
            usage=payload.get("usage") or {},
        )
