from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from healthclaw.agent.soul import normalize_soul_preferences
from healthclaw.api.deps import SessionDep
from healthclaw.core.security import require_api_key
from healthclaw.core.tracing import new_trace_id
from healthclaw.db.models import (
    ConversationThread,
    Message,
    OpenLoop,
    User,
    UserSoulPreference,
    utc_now,
)
from healthclaw.heartbeat.profile import canonicalize_heartbeat_md
from healthclaw.memory.documents import MarkdownMemoryService
from healthclaw.memory.service import MemoryService
from healthclaw.schemas.memory import (
    MemoryDocumentRead,
    MemoryPatch,
    MemoryRead,
    UserMemoryResponse,
)
from healthclaw.schemas.users import (
    HeartbeatProfilePatch,
    HeartbeatProfileRead,
    OpenLoopRead,
    SoulPreferencesPatch,
    SoulPreferencesRead,
    TimelineMessage,
    UserPreferencesPatch,
    UserRead,
    UserTimelineResponse,
)
from healthclaw.services.conversation import ConversationService

router = APIRouter(prefix="/v1/users", tags=["users"], dependencies=[Depends(require_api_key)])


@router.get("/{user_id}/memory", response_model=UserMemoryResponse)
async def get_memory(user_id: str, session: SessionDep) -> UserMemoryResponse:
    user = await ConversationService(session).ensure_user(user_id)
    memories = await MemoryService(session).list_memories(user_id)
    documents = await MarkdownMemoryService(session).refresh_for_user(user)
    return UserMemoryResponse(
        memories=[
            MemoryRead(
                id=m.id,
                kind=m.kind,
                key=m.key,
                layer=m.layer,
                value=m.value,
                confidence=m.confidence,
                freshness_score=m.freshness_score,
                source_message_ids=m.source_message_ids,
                last_confirmed_at=m.last_confirmed_at,
                last_accessed_at=m.last_accessed_at,
                refresh_after=m.refresh_after,
                expires_at=m.expires_at,
                visibility=m.visibility,
                user_editable=m.user_editable,
                metadata=m.metadata_,
            )
            for m in memories
        ],
        documents=[
            MemoryDocumentRead(
                kind=document.kind,
                content=document.content,
                source=document.source,
                version=document.version,
                updated_at=document.updated_at,
            )
            for document in documents
        ],
    )


@router.patch("/{user_id}/preferences", response_model=UserRead)
async def patch_preferences(
    user_id: str, payload: UserPreferencesPatch, session: SessionDep
) -> UserRead:
    user = await ConversationService(session).ensure_user(user_id)
    if payload.timezone is not None:
        user.timezone = payload.timezone
    if payload.quiet_start is not None:
        user.quiet_start = payload.quiet_start
    if payload.quiet_end is not None:
        user.quiet_end = payload.quiet_end
    if payload.proactive_enabled is not None:
        user.proactive_enabled = payload.proactive_enabled
    if payload.proactive_max_per_day is not None:
        user.proactive_max_per_day = payload.proactive_max_per_day
    if payload.proactive_cooldown_minutes is not None:
        user.proactive_cooldown_minutes = payload.proactive_cooldown_minutes
    await session.commit()
    refreshed = await session.get(User, user_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserRead(
        id=refreshed.id,
        timezone=refreshed.timezone,
        quiet_start=refreshed.quiet_start,
        quiet_end=refreshed.quiet_end,
        onboarding_status=refreshed.onboarding_status,
        consent_version=refreshed.consent_version,
        locale=refreshed.locale,
        notification_channel=refreshed.notification_channel,
        last_active_at=refreshed.last_active_at,
        proactive_enabled=refreshed.proactive_enabled,
        proactive_max_per_day=refreshed.proactive_max_per_day,
        proactive_cooldown_minutes=refreshed.proactive_cooldown_minutes,
        proactive_paused_until=refreshed.proactive_paused_until,
        monthly_llm_token_budget=refreshed.monthly_llm_token_budget,
        monthly_llm_tokens_used=refreshed.monthly_llm_tokens_used,
    )


@router.get("/{user_id}/soul-preferences", response_model=SoulPreferencesRead)
async def get_soul_preferences(user_id: str, session: SessionDep) -> SoulPreferencesRead:
    await ConversationService(session).ensure_user(user_id)
    result = await session.execute(
        select(UserSoulPreference).where(UserSoulPreference.user_id == user_id)
    )
    preferences = result.scalar_one_or_none()
    if preferences is None:
        return SoulPreferencesRead(user_id=user_id, version=1)
    return SoulPreferencesRead(
        user_id=user_id,
        version=preferences.version,
        tone_preferences=preferences.tone_preferences,
        response_preferences=preferences.response_preferences,
    )


@router.patch("/{user_id}/soul-preferences", response_model=SoulPreferencesRead)
async def patch_soul_preferences(
    user_id: str, payload: SoulPreferencesPatch, session: SessionDep
) -> SoulPreferencesRead:
    await ConversationService(session).ensure_user(user_id)
    result = await session.execute(
        select(UserSoulPreference).where(UserSoulPreference.user_id == user_id)
    )
    preferences = result.scalar_one_or_none()
    merged = {
        "tone_preferences": payload.tone_preferences or {},
        "response_preferences": payload.response_preferences or {},
    }
    normalized = normalize_soul_preferences(merged)
    if preferences is None:
        preferences = UserSoulPreference(
            user_id=user_id,
            version=1,
            tone_preferences=normalized["tone_preferences"],
            response_preferences=normalized["response_preferences"],
            blocked_policy_keys=[],
        )
        session.add(preferences)
    else:
        preferences.version += 1
        preferences.tone_preferences = {
            **preferences.tone_preferences,
            **normalized["tone_preferences"],
        }
        preferences.response_preferences = {
            **preferences.response_preferences,
            **normalized["response_preferences"],
        }
        preferences.blocked_policy_keys = []
    await session.commit()
    return SoulPreferencesRead(
        user_id=user_id,
        version=preferences.version,
        tone_preferences=preferences.tone_preferences,
        response_preferences=preferences.response_preferences,
    )


@router.get("/{user_id}/heartbeat", response_model=HeartbeatProfileRead)
async def get_heartbeat_profile(user_id: str, session: SessionDep) -> HeartbeatProfileRead:
    user = await ConversationService(session).ensure_user(user_id)
    return HeartbeatProfileRead(
        user_id=user.id,
        heartbeat_md=user.heartbeat_md,
        heartbeat_md_updated_at=user.heartbeat_md_updated_at,
    )


@router.patch("/{user_id}/heartbeat", response_model=HeartbeatProfileRead)
async def patch_heartbeat_profile(
    user_id: str,
    payload: HeartbeatProfilePatch,
    session: SessionDep,
) -> HeartbeatProfileRead:
    user = await ConversationService(session).ensure_user(user_id)
    if payload.heartbeat_md is not None:
        user.heartbeat_md = canonicalize_heartbeat_md(payload.heartbeat_md)
        user.heartbeat_md_updated_at = utc_now() if user.heartbeat_md else None
    await session.commit()
    refreshed = await session.get(User, user_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="User not found")
    return HeartbeatProfileRead(
        user_id=refreshed.id,
        heartbeat_md=refreshed.heartbeat_md,
        heartbeat_md_updated_at=refreshed.heartbeat_md_updated_at,
    )


@router.patch("/{user_id}/memory/{memory_id}", response_model=MemoryRead)
async def patch_memory(
    user_id: str,
    memory_id: str,
    payload: MemoryPatch,
    session: SessionDep,
) -> MemoryRead:
    await ConversationService(session).ensure_user(user_id)
    memory = await MemoryService(session).patch_user_memory(
        user_id,
        memory_id,
        value=payload.value,
        confidence=payload.confidence,
        refresh_after=payload.refresh_after,
        expires_at=payload.expires_at,
        metadata=payload.metadata,
        trace_id=new_trace_id(),
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    await session.commit()
    return MemoryRead(
        id=memory.id,
        kind=memory.kind,
        key=memory.key,
        layer=memory.layer,
        value=memory.value,
        confidence=memory.confidence,
        freshness_score=memory.freshness_score,
        source_message_ids=memory.source_message_ids,
        last_confirmed_at=memory.last_confirmed_at,
        last_accessed_at=memory.last_accessed_at,
        refresh_after=memory.refresh_after,
        expires_at=memory.expires_at,
        visibility=memory.visibility,
        user_editable=memory.user_editable,
        metadata=memory.metadata_,
    )


@router.delete("/{user_id}/memory/{memory_id}", status_code=204)
async def delete_memory(user_id: str, memory_id: str, session: SessionDep) -> None:
    await ConversationService(session).ensure_user(user_id)
    deleted = await MemoryService(session).delete_user_memory(
        user_id,
        memory_id,
        trace_id=new_trace_id(),
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    await session.commit()


@router.post("/{user_id}/pause-proactivity", response_model=UserRead)
async def pause_proactivity(user_id: str, session: SessionDep) -> UserRead:
    user = await ConversationService(session).ensure_user(user_id)
    user.proactive_enabled = False
    await session.commit()
    return await _user_read(user_id, session)


@router.post("/{user_id}/resume-proactivity", response_model=UserRead)
async def resume_proactivity(user_id: str, session: SessionDep) -> UserRead:
    user = await ConversationService(session).ensure_user(user_id)
    user.proactive_enabled = True
    user.proactive_paused_until = None
    await session.commit()
    return await _user_read(user_id, session)


@router.get("/{user_id}/timeline", response_model=UserTimelineResponse)
async def get_timeline(user_id: str, session: SessionDep) -> UserTimelineResponse:
    await ConversationService(session).ensure_user(user_id)
    thread = (
        await session.execute(
            select(ConversationThread)
            .where(ConversationThread.user_id == user_id, ConversationThread.is_primary.is_(True))
            .order_by(ConversationThread.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if thread is None:
        return UserTimelineResponse(user_id=user_id)
    open_loops = list(
        (
            await session.execute(
                select(OpenLoop)
                .where(OpenLoop.user_id == user_id, OpenLoop.status == "open")
                .order_by(OpenLoop.created_at.desc())
                .limit(10)
            )
        ).scalars()
    )
    messages = list(
        (
            await session.execute(
                select(Message)
                .where(Message.thread_id == thread.id)
                .order_by(Message.created_at.desc())
                .limit(20)
            )
        ).scalars()
    )
    return UserTimelineResponse(
        user_id=user_id,
        thread_id=thread.id,
        thread_summary=thread.summary,
        open_loops=[
            OpenLoopRead(
                id=loop.id,
                kind=loop.kind,
                title=loop.title,
                status=loop.status,
                due_after=loop.due_after,
                last_checked_at=loop.last_checked_at,
            )
            for loop in open_loops
        ],
        recent_messages=[
            TimelineMessage(
                id=message.id,
                role=message.role,
                content=message.content,
                channel=message.channel,
                created_at=message.created_at,
            )
            for message in reversed(messages)
        ],
    )


async def _user_read(user_id: str, session: SessionDep) -> UserRead:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserRead(
        id=user.id,
        timezone=user.timezone,
        quiet_start=user.quiet_start,
        quiet_end=user.quiet_end,
        onboarding_status=user.onboarding_status,
        consent_version=user.consent_version,
        locale=user.locale,
        notification_channel=user.notification_channel,
        last_active_at=user.last_active_at,
        proactive_enabled=user.proactive_enabled,
        proactive_max_per_day=user.proactive_max_per_day,
        proactive_cooldown_minutes=user.proactive_cooldown_minutes,
        proactive_paused_until=user.proactive_paused_until,
        monthly_llm_token_budget=user.monthly_llm_token_budget,
        monthly_llm_tokens_used=user.monthly_llm_tokens_used,
    )
