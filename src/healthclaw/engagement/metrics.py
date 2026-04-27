from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from healthclaw.db.models import UserEngagementState

TEXT_ALPHA = 0.25
VOICE_RATIO_ALPHA = 0.15
LATENCY_ALPHA = 0.20

POSITIVE_WORDS = {
    "better",
    "calm",
    "clear",
    "easier",
    "easy",
    "energized",
    "focused",
    "good",
    "great",
    "grounded",
    "okay",
    "ok",
    "proud",
    "relieved",
    "rested",
    "settled",
    "solid",
    "steady",
    "strong",
}

NEGATIVE_WORDS = {
    "anxious",
    "awful",
    "bad",
    "burned",
    "burnout",
    "drained",
    "exhausted",
    "frazzled",
    "hard",
    "hopeless",
    "miserable",
    "numb",
    "overwhelmed",
    "panicked",
    "rough",
    "sad",
    "spiraling",
    "stressed",
    "stuck",
    "tired",
    "worried",
}

TOKEN_RE = re.compile(r"[A-Za-z']+")


def is_meaningful_exchange(
    content: str,
    *,
    content_type: str,
    is_command: bool,
) -> bool:
    if is_command:
        return False
    stripped = content.strip()
    if not stripped:
        return False
    if content_type == "voice_transcript":
        return True
    tokens = TOKEN_RE.findall(stripped)
    compact_length = len(re.sub(r"\s+", "", stripped))
    return len(tokens) >= 4 and compact_length >= 20


def score_valence(content: str) -> float:
    tokens = [token.lower() for token in TOKEN_RE.findall(content)]
    if not tokens:
        return 0.0
    positive = sum(1 for token in tokens if token in POSITIVE_WORDS)
    negative = sum(1 for token in tokens if token in NEGATIVE_WORDS)
    if positive == 0 and negative == 0:
        return 0.0
    score = (positive - negative) / max(1, positive + negative)
    return max(-1.0, min(1.0, float(score)))


def update_meaningful_engagement(
    engagement: UserEngagementState,
    *,
    content: str,
    voice_note: bool,
    user_message_at: datetime,
    previous_assistant_message_at: datetime | None,
) -> None:
    engagement.sentiment_ema = _ema(engagement.sentiment_ema, score_valence(content), TEXT_ALPHA)
    engagement.voice_text_ratio = _ema(
        engagement.voice_text_ratio,
        1.0 if voice_note else 0.0,
        VOICE_RATIO_ALPHA,
    )
    engagement.last_meaningful_exchange_at = user_message_at

    if previous_assistant_message_at is not None:
        previous_assistant_message_at = _ensure_utc(previous_assistant_message_at)
        user_message_at = _ensure_utc(user_message_at)
        latency_seconds = max(
            0.0,
            (user_message_at - previous_assistant_message_at).total_seconds(),
        )
        engagement.reply_latency_seconds_ema = _ema(
            engagement.reply_latency_seconds_ema,
            latency_seconds,
            LATENCY_ALPHA,
        )


def build_relationship_context(
    engagement: UserEngagementState | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = _ensure_utc(now or datetime.now(UTC))
    if engagement is None:
        return {
            "sentiment_ema": 0.0,
            "voice_text_ratio": 0.0,
            "reply_latency_seconds_ema": None,
            "last_meaningful_exchange_at": None,
            "bands": {
                "low_pressure": False,
                "voice_heavy": False,
                "slow_reentry": False,
                "continuity_fresh": False,
            },
        }

    last_meaningful = engagement.last_meaningful_exchange_at
    if last_meaningful is not None:
        last_meaningful = _ensure_utc(last_meaningful)

    continuity_fresh = False
    if last_meaningful is not None:
        continuity_fresh = now - last_meaningful <= timedelta(hours=24)

    return {
        "sentiment_ema": float(engagement.sentiment_ema or 0.0),
        "voice_text_ratio": float(engagement.voice_text_ratio or 0.0),
        "reply_latency_seconds_ema": (
            float(engagement.reply_latency_seconds_ema)
            if engagement.reply_latency_seconds_ema is not None
            else None
        ),
        "last_meaningful_exchange_at": last_meaningful,
        "bands": {
            "low_pressure": float(engagement.sentiment_ema or 0.0) <= -0.35,
            "voice_heavy": float(engagement.voice_text_ratio or 0.0) >= 0.65,
            "slow_reentry": (
                engagement.reply_latency_seconds_ema is not None
                and float(engagement.reply_latency_seconds_ema) >= 43_200
            ),
            "continuity_fresh": continuity_fresh,
        },
    }


def _ema(previous: float | None, observed: float, alpha: float) -> float:
    if previous is None:
        return observed
    return float(previous + alpha * (observed - previous))


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
