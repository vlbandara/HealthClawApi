from __future__ import annotations

import httpx

from healthclaw.core.config import Settings

OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
EMBEDDING_DIM = 1536


class EmbeddingClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openrouter_api_key)

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

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed up to 32 texts in a single call. Returns zero vectors on failure."""
        if not self.enabled or not texts:
            return [self._zero_vector() for _ in texts]

        # Chunk into batches of 32
        results: list[list[float]] = []
        for batch_start in range(0, len(texts), 32):
            batch = texts[batch_start : batch_start + 32]
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        OPENROUTER_EMBEDDINGS_URL,
                        headers=self._headers(),
                        json={
                            "model": self.settings.openrouter_embedding_model,
                            "input": batch,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    # Sort by index to preserve order
                    sorted_data = sorted(payload["data"], key=lambda x: x["index"])
                    results.extend(item["embedding"] for item in sorted_data)
            except Exception:
                results.extend(self._zero_vector() for _ in batch)

        return results

    async def embed_text(self, text: str) -> list[float]:
        results = await self.embed_texts([text])
        return results[0]

    @staticmethod
    def _zero_vector() -> list[float]:
        return [0.0] * EMBEDDING_DIM

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
