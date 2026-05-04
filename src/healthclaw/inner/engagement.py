"""Engagement sensor — LLM judge for open topic follow-through.

Per user turn, we check any open topics that are in their first surface window
against the user's latest message. If the user didn't engage, we increment
disengage_count. Two consecutive disengages → status="cooled".

No regex. The LLM judges whether the user engaged with the topic.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.core.config import get_settings
from healthclaw.db.models import OpenLoop

logger = logging.getLogger(__name__)


async def score_open_topic_engagement(
    user_id: str,
    user_message: str,
    session: AsyncSession,
) -> list[dict[str, object]]:
    """Score engagement for all recently surfaced open topics.

    Called after each user message. Only evaluates topics that were surfaced
    recently (surface_count >= 1, cooldown not yet expired — meaning the agent
    referenced the topic in recent context but the user replied).

    Returns list of {topic_id, title, score, cooled} dicts for tracing.
    """
    settings = get_settings()
    if not user_message.strip():
        return []

    now = datetime.now(UTC)

    # Find open topics that have been surfaced at least once and aren't yet cooled
    result = await session.execute(
        select(OpenLoop).where(
            OpenLoop.user_id == user_id,
            OpenLoop.status == "open",
            OpenLoop.surface_count >= 1,
            OpenLoop.last_surfaced_at.isnot(None),
        ).limit(5)
    )
    topics = list(result.scalars())

    if not topics:
        return []

    scored: list[dict[str, object]] = []
    for topic in topics:
        score = await _judge_engagement(topic.title, user_message, settings)
        topic.engagement_score = score

        if score <= 0.2:
            topic.disengage_count = (topic.disengage_count or 0) + 1
        else:
            # Positive engagement — record it
            topic.engaged_at = now
            topic.disengage_count = 0

        # Two consecutive disengages → cool the topic
        cooled = False
        if (topic.disengage_count or 0) >= 2:
            topic.status = "cooled"
            cooled = True
            logger.debug(
                "engagement: cooled topic '%s' (user=%s, score=%.2f)",
                topic.title, user_id, score,
            )
        scored.append({
            "topic_id": topic.id,
            "title": topic.title,
            "score": score,
            "cooled": cooled,
        })

    return scored


async def _judge_engagement(
    topic_title: str,
    user_message: str,
    settings,
) -> float:
    """Ask the LLM if the user's message engages with the topic. Returns 0..1."""
    from healthclaw.integrations.openrouter import OpenRouterClient
    client = OpenRouterClient(settings)
    if not client.enabled:
        return 0.5  # neutral when offline

    prompt = (
        f"Open topic: \"{topic_title}\"\n"
        f"User reply: \"{user_message[:300]}\"\n\n"
        "Did the user engage with or respond to this topic? "
        "Reply with a single number from 0 to 1 only. "
        "0 = completely ignored / unrelated, "
        "0.5 = tangentially related, "
        "1 = directly responded to the topic."
    )

    try:
        result = await client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8,
            temperature=0.0,
            model=settings.engagement_sensor_model,
            metadata={"model_role": "engagement_sensor"},
        )
        raw = result.content.strip().split()[0]
        score = float(raw)
        return max(0.0, min(1.0, score))
    except Exception as exc:
        logger.debug("engagement sensor failed for topic '%s': %s", topic_title, exc)
        return 0.5  # neutral on failure


def filter_surfaceable_open_loops(
    open_loops: list[dict],
    now: datetime | None = None,
) -> list[dict]:
    """Filter open_loops to only those eligible for surfacing.

    Used by response.py and synthesizer.py to build the Open Loops context block.
    A topic is eligible when:
      - status == "open"
      - surface_count < max_surfaces
      - cooldown_until is None or has passed
    """
    if now is None:
        now = datetime.now(UTC)
    eligible = []
    for loop in open_loops:
        status = loop.get("status", "open")
        if status not in ("open", "pending"):
            continue
        surface_count = int(loop.get("surface_count") or 0)
        max_surfaces = int(loop.get("max_surfaces") or 2)
        if surface_count >= max_surfaces:
            continue
        cooldown_until = loop.get("cooldown_until")
        if cooldown_until:
            try:
                cu = datetime.fromisoformat(str(cooldown_until))
                if cu.tzinfo is None:
                    cu = cu.replace(tzinfo=UTC)
                if now < cu:
                    continue  # still in cooldown
            except Exception:
                pass
        eligible.append(loop)
    return eligible
