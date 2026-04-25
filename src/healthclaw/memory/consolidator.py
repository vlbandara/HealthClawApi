from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.core.config import Settings
from healthclaw.db.models import Message, UserMemoryCursor
from healthclaw.memory.service import MemoryService
from healthclaw.schemas.memory import MemoryMutation

logger = logging.getLogger(__name__)

BATCH_SIZE = 40
MAX_MESSAGES_PER_RUN = 200

CONSOLIDATION_SYSTEM_PROMPT = """\
You are a memory archivist. You will be given a window of conversation turns between a user and \
their health companion. Extract 1-3 compact episodic memories that capture meaningful patterns, \
commitments met or missed, emotional shifts, or notable milestones. Each memory should be useful \
weeks later when the companion needs context about this user's journey.

Return ONLY a JSON array of objects with exactly these fields:
  "key": short snake_case identifier (e.g. "week_of_2026-04-14_sleep_improvement")
  "summary": 1-2 sentence factual summary
  "themes": list of 1-3 theme tags (e.g. ["sleep", "consistency"])
  "sentiment": number from -1.0 (negative) to 1.0 (positive)

If no meaningful patterns exist, return an empty array: []
Do not include clinical assessments or diagnoses."""


class ConsolidatorService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        memory_service: MemoryService,
    ) -> None:
        self.session = session
        self.settings = settings
        self.memory_service = memory_service

    async def run_for_user(self, user_id: str) -> int:
        """Consolidate new messages into episode memories. Returns count of episodes created."""
        cursor = await self._get_or_create_cursor(user_id)

        # Load messages after the cursor
        q = (
            select(Message)
            .where(
                Message.user_id == user_id,
                Message.role.in_(["user", "assistant"]),
            )
            .order_by(Message.created_at.asc())
        )
        if cursor.consolidation_cursor_at:
            q = q.where(Message.created_at > cursor.consolidation_cursor_at)

        result = await self.session.execute(q.limit(MAX_MESSAGES_PER_RUN))
        messages = list(result.scalars())

        if len(messages) < BATCH_SIZE:
            # Not enough new content to consolidate yet
            logger.info(
                "Consolidator skipped for user %s: %s messages since cursor",
                user_id,
                len(messages),
            )
            return 0

        episodes_created = 0
        last_processed_msg: Message | None = None

        for batch_start in range(0, len(messages), BATCH_SIZE):
            batch = messages[batch_start : batch_start + BATCH_SIZE]
            if len(batch) < BATCH_SIZE:
                break
            created = await self._consolidate_batch(user_id, batch)
            episodes_created += created
            last_processed_msg = batch[-1]

        # Advance cursor to last processed message
        if last_processed_msg is not None:
            cursor.consolidation_cursor_msg_id = last_processed_msg.id
            cursor.consolidation_cursor_at = last_processed_msg.created_at
            self.session.add(cursor)
            await self.session.flush()

        logger.info(
            "Consolidator completed for user %s: episodes=%s, processed_messages=%s",
            user_id,
            episodes_created,
            len(messages),
        )
        return episodes_created

    async def _consolidate_batch(self, user_id: str, messages: list[Message]) -> int:
        from healthclaw.integrations.openrouter import OpenRouterClient

        if not messages:
            return 0

        # Format conversation for LLM
        conversation_text = self._format_messages(messages)
        date_range = self._date_range(messages)

        prompt = (
            f"Conversation window ({date_range}):\n\n{conversation_text}\n\n"
            "Extract episodic memories from this window."
        )

        client = OpenRouterClient(self.settings)
        if not client.enabled:
            return 0

        try:
            result = await client.chat_completion(
                messages=[
                    {"role": "system", "content": CONSOLIDATION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=600,
                temperature=0.2,
                model=self.settings.openrouter_dream_model,
            )
            raw = result.content.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            episodes = json.loads(raw)
        except Exception as exc:
            logger.warning("Consolidator LLM call failed: %s", exc)
            return 0

        if not isinstance(episodes, list):
            return 0

        created = 0
        source_ids = [m.id for m in messages]
        for ep in episodes[:3]:
            if not isinstance(ep, dict):
                continue
            key = ep.get("key")
            summary = ep.get("summary")
            if not key or not summary:
                continue
            try:
                mutation = MemoryMutation(
                    kind="episode",
                    key=str(key)[:128],
                    layer="episode",
                    value={
                        "text": str(summary)[:1000],
                        "themes": ep.get("themes", []),
                        "sentiment": float(ep.get("sentiment", 0.0)),
                        "date_range": date_range,
                    },
                    confidence=0.55,
                    reason="Consolidated from message batch by Consolidator.",
                    visibility="internal",
                    user_editable=False,
                )
                await self.memory_service.upsert_memory(user_id, mutation, source_ids)
                created += 1
            except Exception as exc:
                logger.warning("Failed to upsert episode memory: %s", exc)

        return created

    async def _get_or_create_cursor(self, user_id: str) -> UserMemoryCursor:
        result = await self.session.execute(
            select(UserMemoryCursor).where(UserMemoryCursor.user_id == user_id)
        )
        cursor = result.scalar_one_or_none()
        if cursor is None:
            cursor = UserMemoryCursor(user_id=user_id)
            self.session.add(cursor)
            await self.session.flush()
        return cursor

    @staticmethod
    def _format_messages(messages: list[Message]) -> str:
        lines = []
        for msg in messages:
            role = "User" if msg.role == "user" else "Companion"
            # Trim long messages
            content = msg.content[:300] if len(msg.content) > 300 else msg.content
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _date_range(messages: list[Message]) -> str:
        if not messages:
            return "unknown"
        first = messages[0].created_at
        last = messages[-1].created_at

        def fmt(dt: datetime) -> str:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.strftime("%Y-%m-%d")

        start = fmt(first)
        end = fmt(last)
        return start if start == end else f"{start} to {end}"
