from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.agent.soul import normalize_soul_preferences
from healthclaw.db.models import (
    Memory,
    Message,
    OpenLoop,
    User,
    UserMemoryDocument,
    UserSoulPreference,
    utc_now,
)

DOCUMENT_KINDS = ("SOUL", "USER", "MEMORY", "INTERESTS")


class MarkdownMemoryService:
    """Renders governed structured memory into prompt-facing markdown documents."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_documents(self, user_id: str) -> list[UserMemoryDocument]:
        result = await self.session.execute(
            select(UserMemoryDocument)
            .where(UserMemoryDocument.user_id == user_id)
            .order_by(UserMemoryDocument.kind)
        )
        return list(result.scalars())

    async def documents_for_prompt(self, user: User) -> dict[str, str]:
        documents = await self.refresh_for_user(user)
        return {document.kind: document.content for document in documents}

    async def refresh_for_user(self, user: User) -> list[UserMemoryDocument]:
        memories = await self._active_memories(user.id)
        soul_preferences = await self._soul_preferences(user.id)
        open_loops = await self._open_loops(user.id)
        generated = {
            "SOUL": _build_soul_doc(soul_preferences),
            "USER": _build_user_doc(user, memories),
            "MEMORY": _build_memory_doc(memories, open_loops),
            "INTERESTS": _build_interests_doc(memories),
        }
        return await self._upsert_documents(user.id, generated, source="generated")

    async def dream_refresh_for_user(
        self,
        user: User,
        *,
        recent_messages: list[dict[str, object]] | None = None,
    ) -> list[UserMemoryDocument]:
        memories = await self._active_memories(user.id)
        soul_preferences = await self._soul_preferences(user.id)
        open_loops = await self._open_loops(user.id)
        generated = {
            "SOUL": _build_soul_doc(soul_preferences),
            "USER": _build_user_doc(user, memories),
            "MEMORY": _build_memory_doc(memories, open_loops, recent_messages=recent_messages),
            "INTERESTS": _build_interests_doc(memories),
        }
        return await self._upsert_documents(user.id, generated, source="dream")

    async def _active_memories(self, user_id: str) -> list[Memory]:
        now = utc_now()
        result = await self.session.execute(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.is_active.is_(True),
                ((Memory.expires_at.is_(None)) | (Memory.expires_at > now)),
            )
            .order_by(Memory.updated_at.desc())
            .limit(80)
        )
        return list(result.scalars())

    async def _soul_preferences(self, user_id: str) -> dict[str, Any]:
        result = await self.session.execute(
            select(UserSoulPreference).where(UserSoulPreference.user_id == user_id)
        )
        preferences = result.scalar_one_or_none()
        if preferences is None:
            return normalize_soul_preferences({})
        return normalize_soul_preferences(
            {
                "tone_preferences": preferences.tone_preferences,
                "response_preferences": preferences.response_preferences,
            }
        )

    async def _open_loops(self, user_id: str) -> list[OpenLoop]:
        result = await self.session.execute(
            select(OpenLoop)
            .where(OpenLoop.user_id == user_id, OpenLoop.status == "open")
            .order_by(OpenLoop.created_at.desc())
            .limit(10)
        )
        return list(result.scalars())

    async def _upsert_documents(
        self,
        user_id: str,
        generated: dict[str, str],
        *,
        source: str,
    ) -> list[UserMemoryDocument]:
        existing = {
            document.kind: document
            for document in await self.list_documents(user_id)
            if document.kind in DOCUMENT_KINDS
        }
        documents: list[UserMemoryDocument] = []
        now = utc_now()
        for kind in DOCUMENT_KINDS:
            content = generated.get(kind, "").strip()
            document = existing.get(kind)
            if document is None:
                document = UserMemoryDocument(
                    user_id=user_id,
                    kind=kind,
                    content=content,
                    source=source,
                    metadata_={},
                )
                self.session.add(document)
            elif document.content != content:
                document.content = content
                document.source = source
                document.version += 1
                document.updated_at = now
            documents.append(document)
        await self.session.flush()
        return documents

    async def recent_message_digest(self, user_id: str, limit: int = 30) -> list[dict[str, object]]:
        result = await self.session.execute(
            select(Message)
            .where(Message.user_id == user_id, Message.role.in_(["user", "assistant"]))
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return [
            {"role": message.role, "content": message.content}
            for message in reversed(list(result.scalars()))
        ]


def _build_soul_doc(soul_preferences: dict[str, Any]) -> str:
    lines = [
        "## User Style Overlay",
        "",
        "Protected safety, medical, consent, crisis, and quiet-hour policy is not editable.",
    ]
    tone = soul_preferences.get("tone_preferences") or {}
    response = soul_preferences.get("response_preferences") or {}
    if not tone and not response:
        lines.append("No durable user-specific voice overlay yet.")
    else:
        if tone:
            lines.append("")
            lines.append("### Tone")
            lines.extend(f"- {key}: {value}" for key, value in sorted(tone.items()))
        if response:
            lines.append("")
            lines.append("### Response")
            lines.extend(f"- {key}: {value}" for key, value in sorted(response.items()))
    return "\n".join(lines)


def _build_user_doc(user: User, memories: list[Memory]) -> str:
    by_kind = _group_memories(memories)
    lines = [
        "## Stable Profile",
        "",
        f"- User id: {user.id}",
        f"- Timezone: {user.timezone}",
        f"- Onboarding status: {user.onboarding_status}",
    ]
    name = _profile_name(by_kind.get("profile", []))
    if name:
        lines.append(f"- Preferred name: {name}")

    profile_items = [
        _memory_text(memory)
        for memory in by_kind.get("profile", [])
        if memory.key != "preferred_name" and _memory_text(memory)
    ]
    if profile_items:
        lines.append("")
        lines.append("## Profile Notes")
        lines.extend(f"- {item}" for item in profile_items[:8])
    return "\n".join(lines)


def _build_memory_doc(
    memories: list[Memory],
    open_loops: list[OpenLoop],
    *,
    recent_messages: list[dict[str, object]] | None = None,
) -> str:
    by_kind = _group_memories(memories)
    lines = ["## Durable Memory"]
    sections = [
        ("Goals", by_kind.get("goal", []), 8),
        ("Routines", by_kind.get("routine", []), 8),
        ("Frictions", by_kind.get("friction", []), 8),
        ("Commitments", by_kind.get("commitment", []) + by_kind.get("open_loop", []), 8),
        ("Recent Episodes", by_kind.get("episode", []), 5),
        ("Relationship Context", by_kind.get("relationship", []), 6),
    ]
    wrote_section = False
    for title, items, limit in sections:
        bullets = [_memory_text(memory) for memory in items if _memory_text(memory)]
        if not bullets:
            continue
        wrote_section = True
        lines.append("")
        lines.append(f"### {title}")
        lines.extend(f"- {bullet}" for bullet in bullets[:limit])

    if open_loops:
        wrote_section = True
        lines.append("")
        lines.append("### Open Loops")
        lines.extend(f"- {loop.title}" for loop in open_loops[:10])

    recent_bullets = _recent_bullets(recent_messages or [])
    if recent_bullets:
        wrote_section = True
        lines.append("")
        lines.append("### Recent Conversation Digest")
        lines.extend(f"- {bullet}" for bullet in recent_bullets[:6])

    if not wrote_section:
        lines.append("")
        lines.append("No durable memory yet. Stay casual and learn the user naturally.")
    return "\n".join(lines)


def _build_interests_doc(memories: list[Memory]) -> str:
    by_kind = _group_memories(memories)
    items = by_kind.get("preference", []) + by_kind.get("relationship", [])
    bullets = [_memory_text(memory) for memory in items if _memory_text(memory)]
    lines = ["## Interests and Taste"]
    if not bullets:
        lines.append("")
        lines.append("No durable interests or taste markers yet.")
    else:
        lines.append("")
        lines.extend(f"- {bullet}" for bullet in bullets[:14])
    return "\n".join(lines)


def _group_memories(memories: list[Memory]) -> dict[str, list[Memory]]:
    grouped: dict[str, list[Memory]] = defaultdict(list)
    for memory in memories:
        grouped[memory.kind].append(memory)
    return grouped


def _profile_name(memories: list[Memory]) -> str | None:
    for memory in memories:
        if memory.key not in {"preferred_name", "name", "full_name"}:
            continue
        text = _memory_text(memory).strip()
        if text:
            return text[:80]
    return None


def _memory_text(memory: Memory) -> str:
    value = memory.value if isinstance(memory.value, dict) else {}
    text = value.get("text") or value.get("summary") or value.get("name")
    if text:
        return str(text).strip()
    if memory.semantic_text:
        return memory.semantic_text.strip()
    return str(value).strip() if value else ""


def _recent_bullets(messages: list[dict[str, object]]) -> list[str]:
    bullets: list[str] = []
    for message in messages[-10:]:
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        bullets.append(f"{role}: {content[:180]}")
    return bullets
