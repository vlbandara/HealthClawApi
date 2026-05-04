from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64))
    quiet_start: Mapped[str] = mapped_column(String(5))
    quiet_end: Mapped[str] = mapped_column(String(5))
    onboarding_status: Mapped[str] = mapped_column(String(32), default="new")
    consent_version: Mapped[str] = mapped_column(String(32), default="wellness-v1")
    # WS5: location + chronotype for afferent sensing
    home_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    chronotype: Mapped[str] = mapped_column(String(16), default="intermediate")
    locale: Mapped[str] = mapped_column(String(16), default="en")
    # WS7: timezone confidence (0.0 = default/guessed, 1.0 = user-confirmed)
    timezone_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    notification_channel: Mapped[str] = mapped_column(String(32), default="telegram")
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proactive_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    proactive_max_per_day: Mapped[int] = mapped_column(Integer, default=2)
    proactive_cooldown_minutes: Mapped[int] = mapped_column(Integer, default=180)
    proactive_paused_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    monthly_llm_token_budget: Mapped[int] = mapped_column(Integer, default=500_000)
    monthly_llm_tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    monthly_llm_cost_cents_used: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_md: Mapped[str] = mapped_column(Text, default="")
    heartbeat_md_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    memories: Mapped[list[Memory]] = relationship(back_populates="user")


class Identity(Base):
    __tablename__ = "identities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserSoulPreference(Base):
    __tablename__ = "user_soul_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_soul_preferences_user"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    tone_preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    blocked_policy_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ChannelAccount(Base):
    __tablename__ = "channel_accounts"
    __table_args__ = (UniqueConstraint("channel", "external_id", name="uq_channel_external"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConversationThread(Base):
    __tablename__ = "conversation_threads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(32))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    open_loop_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(ForeignKey("conversation_threads.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(32))
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (UniqueConstraint("user_id", "kind", "key", name="uq_memory_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(128))
    layer: Mapped[str] = mapped_column(String(32), default="durable")
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    semantic_text: Mapped[str] = mapped_column(Text, default="")
    # embedding_vec stored via raw SQL / pgvector; accessed through HybridRetriever
    confidence: Mapped[float] = mapped_column(Float)
    freshness_score: Mapped[float] = mapped_column(Float, default=1.0)
    source_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    visibility: Mapped[str] = mapped_column(String(24), default="user_visible")
    user_editable: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    has_embedding: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    user: Mapped[User] = relationship(back_populates="memories")


class MemoryRevision(Base):
    __tablename__ = "memory_revisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"))
    previous_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    source_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserMemoryDocument(Base):
    __tablename__ = "user_memory_documents"
    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_user_memory_document_kind"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="generated")
    version: Mapped[int] = mapped_column(Integer, default=1)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DreamRun(Base):
    __tablename__ = "dream_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="started")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DreamChange(Base):
    __tablename__ = "dream_changes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("dream_runs.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(48))
    target_key: Mapped[str] = mapped_column(String(160))
    previous_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    protected_policy_check: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PolicyProposal(Base):
    __tablename__ = "policy_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(128))
    proposed_value: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryKindAudit(Base):
    __tablename__ = "memory_kind_audits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProposedAction(Base):
    __tablename__ = "proposed_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown_type")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InboundEvent(Base):
    __tablename__ = "inbound_events"
    __table_args__ = (
        UniqueConstraint("channel", "idempotency_key", name="uq_inbound_channel_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    channel: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    user_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    assistant_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_reminder_idempotency"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    channel: Mapped[str] = mapped_column(String(32), default="telegram")
    status: Mapped[str] = mapped_column(String(24), default="scheduled")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OpenLoop(Base):
    __tablename__ = "open_loops"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    thread_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversation_threads.id"), nullable=True
    )
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="commitment")
    title: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(24), default="open")
    due_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    # WS7: topic memory engagement tracking
    surface_count: Mapped[int] = mapped_column(Integer, default=0)
    last_surfaced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_hours: Mapped[int] = mapped_column(Integer, default=12)
    max_surfaces: Mapped[int] = mapped_column(Integer, default=2)
    engaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    engagement_score: Mapped[float] = mapped_column(Float, default=0.0)
    disengage_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class HeartbeatJob(Base):
    __tablename__ = "heartbeat_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_heartbeat_idempotency"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    open_loop_id: Mapped[str | None] = mapped_column(ForeignKey("open_loops.id"), nullable=True)
    ritual_id: Mapped[str | None] = mapped_column(ForeignKey("rituals.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="scheduled")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    channel: Mapped[str] = mapped_column(String(32), default="telegram")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HeartbeatEvent(Base):
    __tablename__ = "heartbeat_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("heartbeat_jobs.id"), nullable=True)
    open_loop_id: Mapped[str | None] = mapped_column(ForeignKey("open_loops.id"), nullable=True)
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decision_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProactiveEvent(Base):
    __tablename__ = "proactive_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reminder_id: Mapped[str | None] = mapped_column(ForeignKey("reminders.id"), nullable=True)
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentCheckpoint(Base):
    __tablename__ = "agent_checkpoints"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(ForeignKey("conversation_threads.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(32))
    trace_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserEngagementState(Base):
    __tablename__ = "user_engagement_states"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_engagement_user"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_user_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_assistant_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    conversation_count: Mapped[int] = mapped_column(Integer, default=0)
    voice_note_count: Mapped[int] = mapped_column(Integer, default=0)
    lapse_count: Mapped[int] = mapped_column(Integer, default=0)
    weekly_reflection_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trust_level: Mapped[float] = mapped_column(Float, default=0.3)
    open_loop_count: Mapped[int] = mapped_column(Integer, default=0)
    sentiment_ema: Mapped[float] = mapped_column(Float, default=0.0)
    voice_text_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    reply_latency_seconds_ema: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_meaningful_exchange_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class UserQuota(Base):
    __tablename__ = "user_quotas"
    __table_args__ = (UniqueConstraint("user_id", "period_key", name="uq_user_quota_period"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    period_key: Mapped[str] = mapped_column(String(16))
    token_budget: Mapped[int] = mapped_column(Integer, default=500_000)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    cost_cents_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ConsentEvent(Base):
    __tablename__ = "consent_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TraceRef(Base):
    __tablename__ = "trace_refs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(256))
    redacted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SafetyEvent(Base):
    __tablename__ = "safety_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(24))
    action: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Ritual(Base):
    __tablename__ = "rituals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(240))
    schedule_cron: Mapped[str] = mapped_column(String(64))
    prompt_template: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    streak_count: Mapped[int] = mapped_column(Integer, default=0)
    streak_last_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("email", name="uq_accounts_email"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254))
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    bot_token_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bot_telegram_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(96), nullable=True)
    plan: Mapped[str] = mapped_column(String(32), default="free")
    monthly_message_count: Mapped[int] = mapped_column(Integer, default=0)
    monthly_message_period_start: Mapped[str | None] = mapped_column(String(7), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AuthMagicLink(Base):
    __tablename__ = "auth_magic_links"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserMemoryCursor(Base):
    __tablename__ = "user_memory_cursors"
    __table_args__ = (UniqueConstraint("user_id", name="uq_memory_cursor_user"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    consolidation_cursor_msg_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    consolidation_cursor_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dream_cursor_msg_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    dream_cursor_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    embedding_cursor_memory_id: Mapped[str | None] = mapped_column(
        ForeignKey("memories.id"), nullable=True
    )


# ── WS5: Afferent sensing layer ────────────────────────────────────────────


class UserLocation(Base):
    """Named locations for a user (home, work, travel). home_lat/lon on User is denorm fast-path."""

    __tablename__ = "user_locations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(64), default="home")
    source: Mapped[str] = mapped_column(String(32), default="manual")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IntegrationCredential(Base):
    """OAuth tokens and provider credentials, stored encrypted."""

    __tablename__ = "integration_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_integration_credential_user_provider"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    encrypted_payload: Mapped[bytes | None] = mapped_column(
        type_=String(8192).with_variant(String(8192), "postgresql"), nullable=True
    )
    scopes: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Signal(Base):
    """An afferent perception event (weather, calendar, wearable, location).
    Deduplicated via dedup_key to avoid re-ticking on unchanged data."""

    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32))
    dedup_key: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Thought(Base):
    """Inner cognitive stream entry — not shown to user, auditable via admin/OTel."""

    __tablename__ = "thoughts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[str] = mapped_column(String(24), default="passive")
    content_summary: Mapped[str] = mapped_column(Text, default="")
    salience: Mapped[float] = mapped_column(Float, default=0.0)
    salience_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    signal_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    time_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    became_utterance: Mapped[bool] = mapped_column(Boolean, default=False)
    heartbeat_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("heartbeat_jobs.id"), nullable=True
    )
    # WS6: intent fields populated by inner synthesizer
    intent_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent_motive: Mapped[str | None] = mapped_column(String(64), nullable=True)
    discarded_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deferred_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


# ── WS6: Motive layer ─────────────────────────────────────────────────────────


class Motive(Base):
    """An agent-held intrinsic drive on behalf of the user.

    Motives reweight salience scoring (heat-stress × hydration.weight)
    and guide the inner synthesizer toward what's worth nudging about.
    They are the agent's — not the user's goals (which live in Memory(kind='goal')).
    """

    __tablename__ = "motives"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_motive_user_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))  # hydration, sleep_protection, movement, …
    weight: Mapped[float] = mapped_column(Float, default=0.3)   # 0..1
    rationale: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="seeded")  # seeded|learned|user_set
    decay_half_life_days: Mapped[int] = mapped_column(Integer, default=21)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reinforced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_decayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


# ── WS6: Metric logs ─────────────────────────────────────────────────────────


class MetricLog(Base):
    """Append-only time-series for user-reported health metrics.

    Written by the log_metric action executor. Kept separate from Memory
    to avoid polluting retrieval with raw time-series rows.
    """

    __tablename__ = "metric_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    metric: Mapped[str] = mapped_column(String(64))  # sleep_hours, mood_1_5, steps, water_ml, …
    value: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


# ── WS6: Action audit ────────────────────────────────────────────────────────


class ActionAudit(Base):
    """Audit trail for every executed action, including safety_category."""

    __tablename__ = "action_audit"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    action_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="executed")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    safety_category: Mapped[str] = mapped_column(String(32), default="normal")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
