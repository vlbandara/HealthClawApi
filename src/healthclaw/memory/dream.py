from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.core.config import Settings
from healthclaw.core.tracing import start_span
from healthclaw.db.models import (
    DreamChange,
    DreamRun,
    Memory,
    Message,
    User,
    UserEngagementState,
    UserMemoryCursor,
    UserSoulPreference,
    utc_now,
)
from healthclaw.heartbeat.profile import merge_dream_heartbeat_md
from healthclaw.memory.documents import MarkdownMemoryService
from healthclaw.memory.service import MemoryService
from healthclaw.schemas.memory import MemoryMutation

logger = logging.getLogger(__name__)

MAX_DREAM_MESSAGES = 120

DREAM_SYSTEM_PROMPT = """\
You are Healthclaw's Dream loop. Review recent conversation and durable memory, then propose
small source-of-truth updates that make the companion more continuous and less generic.

Return ONLY valid JSON with a top-level "changes" array. Each change must be one of:
{"target_type":"soul_preferences","target_key":"style","value":{"tone_preferences":{},"response_preferences":{}},"reason":"...","confidence":0.0-1.0}
{"target_type":"memory","target_key":"kind:key","value":{"kind":"<kind>","key":"snake_case","value":{"text":"..."},"confidence":0.0-1.0,"reason":"..."},"reason":"...","confidence":0.0-1.0}
{"target_type":"heartbeat_md","target_key":"standing_intents",
  "value":{"text":"wake: ...\nallow_long_silence: true|false\n(optional standing prose)"},
  "reason":"...","confidence":0.0-1.0}
{"target_type":"engagement","target_key":"trust_level","value":{"trust_level":0.0-1.0},"reason":"...","confidence":0.0-1.0}

Prefer no change unless evidence is clear."""


class DreamService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        memory_service: MemoryService | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.memory_service = memory_service or MemoryService(session)

    async def run_for_user(self, user_id: str) -> dict[str, int | str]:
        user = await self.session.get(User, user_id)
        if user is None:
            return {"status": "skipped", "applied": 0, "rejected": 0}

        cursor = await self._get_or_create_cursor(user.id)
        messages = await self._recent_messages(user.id, cursor)
        if not messages:
            logger.info("Dream skipped for user %s: no new messages", user.id)
            return {"status": "skipped", "applied": 0, "rejected": 0}

        run = DreamRun(
            user_id=user.id,
            model=self.settings.openrouter_dream_model,
            input_summary=f"{len(messages)} messages since dream cursor",
            status="started",
            usage={},
        )
        self.session.add(run)
        await self.session.flush()

        try:
            raw_changes, model, usage = await self._ask_dream(user, messages)
            run.model = model or self.settings.openrouter_dream_model
            run.usage = usage or {}
            applied = 0
            rejected = 0
            for raw_change in raw_changes:
                did_apply = await self._apply_change(run, user, raw_change)
                if did_apply:
                    applied += 1
                else:
                    rejected += 1
            cursor.dream_cursor_msg_id = messages[-1].id
            cursor.dream_cursor_at = messages[-1].created_at
            await MarkdownMemoryService(self.session).dream_refresh_for_user(
                user,
                recent_messages=[
                    {"role": message.role, "content": message.content[:500]}
                    for message in messages[-30:]
                ],
            )
            await self._learn_engagement_rhythm(user)
            run.status = "completed"
            run.completed_at = utc_now()
            await self.session.flush()
            result = {"status": "completed", "applied": applied, "rejected": rejected}
            logger.info("Dream completed for user %s: %s", user.id, result)
            return result
        except Exception as exc:
            logger.warning("Dream run failed for user %s: %s", user.id, exc)
            run.status = "failed"
            run.error = str(exc)[:2000]
            run.completed_at = utc_now()
            await self.session.flush()
            return {"status": "failed", "applied": 0, "rejected": 0}

    async def _ask_dream(self, user: User, messages: list[Message]) -> tuple[list[dict], str, dict]:
        from healthclaw.integrations.openrouter import OpenRouterClient

        client = OpenRouterClient(self.settings)
        if not client.enabled:
            return [], "", {}

        docs = await MarkdownMemoryService(self.session).documents_for_prompt(user)
        prompt = {
            "user": {
                "id": user.id,
                "timezone": user.timezone,
                "heartbeat_md": user.heartbeat_md[:1200],
            },
            "documents": docs,
            "recent_messages": [
                {"role": message.role, "content": message.content[:800]}
                for message in messages[-MAX_DREAM_MESSAGES:]
            ],
        }
        async with start_span(
                "openrouter.chat",
                attributes={
                    "model_role": "dream",
                    "user_id": user.id,
                },
            ):
                result = await client.chat_completion(
                    messages=[
                        {"role": "system", "content": DREAM_SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(prompt)},
                    ],
                    max_tokens=1000,
                    temperature=0.1,
                    model=self.settings.openrouter_dream_model,
                    metadata={
                        "model_role": "dream",
                        "user_id": user.id,
                    },
                )
        raw = result.content.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:].strip()
        parsed = json.loads(raw)
        changes = parsed.get("changes", []) if isinstance(parsed, dict) else []
        valid_changes = [change for change in changes if isinstance(change, dict)]
        return valid_changes, result.model, result.usage

    async def _apply_change(self, run: DreamRun, user: User, raw: dict[str, Any]) -> bool:
        target_type = str(raw.get("target_type") or "")[:48]
        target_key = str(raw.get("target_key") or "")[:160]
        value = raw.get("value") if isinstance(raw.get("value"), dict) else {}
        reason = str(raw.get("reason") or "Dream update.")[:2000]
        confidence = _clamp_float(raw.get("confidence"), default=0.5)
        audit = self._change_audit(target_type, target_key, value)
        previous: dict[str, Any] | None = None
        applied = False

        try:
            if target_type == "soul_preferences":
                previous, applied = await self._apply_soul_preferences(user.id, value)
            elif target_type == "memory":
                previous, applied = await self._apply_memory(user.id, value, reason, confidence)
            elif target_type == "heartbeat_md":
                previous, applied = await self._apply_heartbeat_md(user, value)
            elif target_type == "engagement":
                previous, applied = await self._apply_engagement(user.id, value)
        except Exception as exc:
            audit = {**audit, "error": str(exc)[:500]}

        self.session.add(
            DreamChange(
                run_id=run.id,
                user_id=user.id,
                target_type=target_type or "unknown",
                target_key=target_key or "unknown",
                previous_value=previous,
                new_value=value,
                reason=reason,
                confidence=confidence,
                protected_policy_check=audit,
                applied=applied,
            )
        )
        return applied

    async def _apply_soul_preferences(
        self,
        user_id: str,
        value: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        tone_preferences = _normalized_preferences(value.get("tone_preferences"))
        response_preferences = _normalized_preferences(value.get("response_preferences"))
        if not tone_preferences and not response_preferences:
            return None, False
        result = await self.session.execute(
            select(UserSoulPreference).where(UserSoulPreference.user_id == user_id)
        )
        preferences = result.scalar_one_or_none()
        previous = None
        if preferences is None:
            preferences = UserSoulPreference(
                user_id=user_id,
                version=1,
                tone_preferences=tone_preferences,
                response_preferences=response_preferences,
                blocked_policy_keys=[],
            )
            self.session.add(preferences)
        else:
            previous = {
                "tone_preferences": preferences.tone_preferences,
                "response_preferences": preferences.response_preferences,
            }
            preferences.version += 1
            preferences.tone_preferences = {
                **preferences.tone_preferences,
                **tone_preferences,
            }
            preferences.response_preferences = {
                **preferences.response_preferences,
                **response_preferences,
            }
            preferences.blocked_policy_keys = []
        return previous, True

    async def _apply_memory(
        self,
        user_id: str,
        value: dict[str, Any],
        reason: str,
        confidence: float,
    ) -> tuple[dict[str, Any] | None, bool]:
        kind = str(value.get("kind") or "")
        key = str(value.get("key") or "")[:128]
        memory_value = value.get("value") if isinstance(value.get("value"), dict) else {}
        if not kind or not key or not memory_value:
            return None, False
        result = await self.session.execute(
            select(Memory).where(Memory.user_id == user_id, Memory.kind == kind, Memory.key == key)
        )
        existing = result.scalar_one_or_none()
        previous = existing.value if existing is not None else None
        mutation = MemoryMutation(
            kind=kind,  # type: ignore[arg-type]
            key=key,
            value=memory_value,
            confidence=_clamp_float(value.get("confidence"), default=confidence),
            reason=str(value.get("reason") or reason),
            visibility="internal" if kind in {"relationship", "episode"} else "user_visible",
            user_editable=kind not in {"relationship", "episode"},
            metadata={"source": "dream"},
        )
        await self.memory_service.upsert_memory(user_id, mutation, [])
        return previous, True

    @staticmethod
    async def _apply_heartbeat_md(
        user: User,
        value: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        text = str(value.get("text") or "").strip()
        if not text:
            return None, False
        previous = {"heartbeat_md": user.heartbeat_md}
        user.heartbeat_md = merge_dream_heartbeat_md(user.heartbeat_md, text)
        user.heartbeat_md_updated_at = utc_now()
        return previous, True

    async def _apply_engagement(
        self,
        user_id: str,
        value: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        if "trust_level" not in value:
            return None, False
        result = await self.session.execute(
            select(UserEngagementState).where(UserEngagementState.user_id == user_id)
        )
        engagement = result.scalar_one_or_none()
        if engagement is None:
            engagement = UserEngagementState(user_id=user_id, metadata_={})
            self.session.add(engagement)
        previous = {"trust_level": engagement.trust_level}
        engagement.trust_level = _clamp_float(
            value.get("trust_level"),
            default=engagement.trust_level,
        )
        return previous, True

    async def _learn_engagement_rhythm(self, user: User) -> None:
        """Bucket the last 30 days of user-sent messages by local hour and write a rhythm Memory.

        This is a pure statistical pass — no LLM. The result is stored as
        Memory(kind="rhythm", key="engagement_pattern") and injected into TimeContext
        by build_time_context when available.
        """
        from collections import Counter
        from datetime import timedelta
        from zoneinfo import ZoneInfo

        cutoff = utc_now() - timedelta(days=30)
        result = await self.session.execute(
            select(Message).where(
                Message.user_id == user.id,
                Message.role == "user",
                Message.created_at >= cutoff,
            )
        )
        messages = list(result.scalars())
        if len(messages) < 10:
            return

        try:
            tz = ZoneInfo(user.timezone)
        except Exception:
            return

        hour_counts: Counter[int] = Counter()
        for msg in messages:
            ts = msg.created_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            local_hour = ts.astimezone(tz).hour
            hour_counts[local_hour] += 1

        total = sum(hour_counts.values())
        if total == 0:
            return

        # Top-3 engagement hours
        top_hours = [h for h, _ in hour_counts.most_common(3)]
        top_hours.sort()

        # Compute typical gap deviation (how irregular are their check-ins)
        sorted_msgs = sorted(
            messages, key=lambda m: m.created_at if m.created_at.tzinfo else m.created_at.replace(tzinfo=UTC)
        )
        gaps = []
        for i in range(1, len(sorted_msgs)):
            prev = sorted_msgs[i - 1].created_at
            curr = sorted_msgs[i].created_at
            if prev.tzinfo is None:
                prev = prev.replace(tzinfo=UTC)
            if curr.tzinfo is None:
                curr = curr.replace(tzinfo=UTC)
            gap_min = int((curr - prev).total_seconds() / 60)
            if gap_min < 1440:  # only gaps within a day
                gaps.append(gap_min)
        deviation_min = int(sum(gaps) / len(gaps)) if gaps else 0

        rhythm_value = {
            "typical_engage_hours": top_hours,
            "deviation_from_pattern_min": deviation_min,
            "sample_size": total,
            "computed_at": utc_now().isoformat(),
        }

        mutation = MemoryMutation(
            kind="rhythm",  # type: ignore[arg-type]
            key="engagement_pattern",
            value=rhythm_value,
            confidence=0.7,
            reason="Learned from 30-day message history during Dream.",
            visibility="internal",
            user_editable=False,
            metadata={"source": "dream_rhythm"},
        )
        await self.memory_service.upsert_memory(user.id, mutation, [])
        logger.debug("Rhythm memory written for user %s: %s", user.id, top_hours)

    async def _recent_messages(
        self,
        user_id: str,
        cursor: UserMemoryCursor,
    ) -> list[Message]:
        q = (
            select(Message)
            .where(Message.user_id == user_id, Message.role.in_(["user", "assistant"]))
            .order_by(Message.created_at.asc())
            .limit(MAX_DREAM_MESSAGES)
        )
        if cursor.dream_cursor_at is not None:
            q = q.where(Message.created_at > cursor.dream_cursor_at)
        result = await self.session.execute(q)
        return list(result.scalars())

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
    def _change_audit(target_type: str, target_key: str, value: dict[str, Any]) -> dict[str, Any]:
        return {
            "blocked": False,
            "audit_only": True,
            "target_type": target_type,
            "target_key": target_key,
            "value_keys": sorted(str(key) for key in value.keys())[:12],
        }


def _clamp_float(value: Any, *, default: float) -> float:
    if not isinstance(value, int | float):
        return default
    return max(0.0, min(1.0, float(value)))


def _normalized_preferences(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, raw_value in value.items():
        normalized_key = str(key).strip()[:80]
        if not normalized_key:
            continue
        if isinstance(raw_value, (str, int, float, bool, list, dict)):
            normalized[normalized_key] = raw_value
        else:
            normalized[normalized_key] = str(raw_value)
    return normalized
