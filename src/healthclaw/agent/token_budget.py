"""Token-aware context budget using tiktoken (cl100k_base proxy).

cl100k_base overcounts by ~5-10% vs Claude/Llama tokenizers — intentionally safe.
Provides a shared budget across system prompt, skill modules, web sources,
memories, open loops, and recent messages.

Eviction order when over budget:
  1. Drop episode-kind memories (lowest information density)
  2. Trim oldest recent_messages one-by-one
  3. Extractive fallback: keep only first sentence of remaining recent_messages
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_ENCODER: Any = None  # lazily initialised


def _get_encoder() -> Any:
    global _ENCODER  # noqa: PLW0603
    if _ENCODER is None:
        try:
            import tiktoken  # type: ignore[import-untyped]
            _ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:
            logger.warning("tiktoken unavailable, falling back to char/4 heuristic: %s", exc)
            _ENCODER = _CharEncoder()
    return _ENCODER


class _CharEncoder:
    """Fallback when tiktoken is not installed: chars/4 approximation."""

    def encode(self, text: str) -> list[int]:
        return [0] * max(1, len(text) // 4)


def count_tokens(text: str) -> int:
    """Count tokens in *text* using cl100k_base (or char/4 fallback)."""
    if not text:
        return 0
    enc = _get_encoder()
    return len(enc.encode(text))


class TokenBudget:
    """Tracks and enforces a total token budget across prompt sections.

    Usage::

        budget = TokenBudget(max_tokens=16000, reserve_system=2500, reserve_output=1500)
        available = budget.available                       # 12 000
        memories = budget.fit_memories(memories)          # evicts low-rank first
        recent   = budget.fit_recent_messages(messages)   # trims oldest first
    """

    def __init__(
        self,
        max_tokens: int = 16000,
        reserve_system: int = 2500,
        reserve_output: int = 1500,
    ) -> None:
        self.max_tokens = max_tokens
        self.reserve_system = reserve_system
        self.reserve_output = reserve_output
        self._used: dict[str, int] = {}

    @property
    def available(self) -> int:
        return max(0, self.max_tokens - self.reserve_system - self.reserve_output)

    @property
    def used(self) -> int:
        return sum(self._used.values())

    @property
    def remaining(self) -> int:
        return max(0, self.available - self.used)

    def charge(self, section: str, text: str) -> int:
        """Record *text* as belonging to *section*. Returns tokens charged."""
        tokens = count_tokens(text)
        self._used[section] = self._used.get(section, 0) + tokens
        return tokens

    def can_fit(self, text: str) -> bool:
        return count_tokens(text) <= self.remaining

    def budget_usage(self) -> dict[str, int]:
        return {**self._used, "_total": self.used, "_remaining": self.remaining}

    def fit_memories(
        self,
        memories: list[dict[str, Any]],
        *,
        section: str = "memories",
    ) -> list[dict[str, Any]]:
        """Return as many memories as fit, preferring non-episode kinds."""
        # Sort: episodes last
        ordered = sorted(
            memories,
            key=lambda m: (1 if str(m.get("kind") or "") == "episode" else 0),
        )
        kept: list[dict[str, Any]] = []
        for memory in ordered:
            text = _memory_token_text(memory)
            if self.can_fit(text):
                self.charge(section, text)
                kept.append(memory)
            else:
                logger.debug(
                    "TokenBudget evicted memory %s:%s (episodes first)",
                    memory.get("kind"),
                    memory.get("key"),
                )
        return kept

    def fit_recent_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        section: str = "recent_messages",
    ) -> list[dict[str, Any]]:
        """Return most-recent messages that fit. Drops oldest first.
        As a last resort applies extractive (first-sentence) compression."""
        kept: list[dict[str, Any]] = []
        for msg in reversed(messages):  # newest first
            content = str(msg.get("content") or "")
            if self.can_fit(content):
                self.charge(section, content)
                kept.insert(0, msg)
            else:
                # Try extractive compression: first sentence only
                compressed = _first_sentence(content)
                if compressed and self.can_fit(compressed):
                    self.charge(section, compressed)
                    kept.insert(0, {**msg, "content": compressed, "_compressed": True})
                # else: drop this message entirely
        return kept


# ── helpers ──────────────────────────────────────────────────────────────────


def _memory_token_text(memory: dict[str, Any]) -> str:
    value = memory.get("value")
    if isinstance(value, dict):
        text = value.get("text") or value.get("summary")
        if isinstance(text, str) and text:
            return f"{memory.get('kind')}:{memory.get('key')} {text}"
    semantic = memory.get("semantic_text") or ""
    return f"{memory.get('kind')}:{memory.get('key')} {semantic}"


def _first_sentence(text: str) -> str:
    """Return the first sentence (up to first '.', '!', '?' or 120 chars)."""
    for sep in (".", "!", "?"):
        idx = text.find(sep)
        if 0 < idx <= 200:
            return text[: idx + 1].strip()
    return text[:120].strip()
