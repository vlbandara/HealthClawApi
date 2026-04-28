from __future__ import annotations

from typing import Any

from healthclaw.core.config import get_settings
from healthclaw.integrations.openrouter import OpenRouterClient


async def compact_thread_summary(
    *,
    prior_summary: str,
    recent_turns: list[dict[str, str]],
    user_id: str,
    thread_id: str,
) -> str:
    settings = get_settings()

    turns_block = "\n".join(
        f"{turn.get('role', 'user').capitalize()}: {turn.get('content', '')[:300]}"
        for turn in recent_turns
    )

    prior_block = (
        f"Prior summary:\n{prior_summary[:500]}\n" if prior_summary else "Prior summary: (none)\n"
    )

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Compress the prior summary plus the last 6 turns into a 5-8 line first-person "
                "digest of the companion covering: open loops, recent goals, emotional tone, "
                "current topic. Stay under 1200 chars. Output only the digest, no preamble."
            ),
        },
        {
            "role": "user",
            "content": f"{prior_block}\nRecent turns:\n{turns_block}",
        },
    ]

    client = OpenRouterClient(settings)
    result = await client.chat_completion(
        messages,
        model=settings.openrouter_dream_model,
        temperature=0.1,
        max_tokens=300,
        metadata={"model_role": "thread_digest", "user_id": user_id, "thread_id": thread_id},
    )

    return result.content.strip()[: settings.thread_summary_max_chars]
