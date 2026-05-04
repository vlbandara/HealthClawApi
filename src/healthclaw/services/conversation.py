from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.agent.context_harness import ContextHarness
from healthclaw.agent.graph import agent_graph
from healthclaw.agent.thread_digest import compact_thread_summary
from healthclaw.agent.time_context import build_time_context
from healthclaw.core.config import get_settings
from healthclaw.core.tracing import new_trace_id, redacted_payload, start_span
from healthclaw.db.models import (
    Account,
    AgentCheckpoint,
    ChannelAccount,
    ConversationThread,
    HeartbeatJob,
    InboundEvent,
    Message,
    OpenLoop,
    ProposedAction,
    Ritual,
    SafetyEvent,
    TraceRef,
    User,
    UserEngagementState,
    UserQuota,
    UserSoulPreference,
    utc_now,
)
from healthclaw.engagement.metrics import (
    build_relationship_context,
    is_meaningful_exchange,
    update_meaningful_engagement,
)
from healthclaw.heartbeat.profile import canonicalize_heartbeat_md
from healthclaw.heartbeat.rituals import RitualService
from healthclaw.heartbeat.service import HeartbeatService
from healthclaw.heartbeat.streaks import RitualStreakService
from healthclaw.memory.documents import MarkdownMemoryService
from healthclaw.memory.embeddings import EmbeddingClient
from healthclaw.memory.service import MemoryService
from healthclaw.proactivity.service import ProactivityService
from healthclaw.schemas.actions import (
    CloseOpenLoopPayload,
    CreateOpenLoopPayload,
    CreateReminderPayload,
    OpenTopicPayload,
    SetUserTimezonePayload,
)
from healthclaw.schemas.events import ConversationEvent
from healthclaw.schemas.memory import MemoryMutation
from healthclaw.schemas.messages import MessageResponse
from healthclaw.services.account import AccountService

logger = logging.getLogger(__name__)


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    def _embedding_client(self) -> EmbeddingClient:
        return EmbeddingClient(self.settings)

    async def ensure_user(self, user_id: str, timezone: str | None = None) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            user = User(
                id=user_id,
                timezone=timezone or self.settings.default_timezone,
                quiet_start=self.settings.default_quiet_start,
                quiet_end=self.settings.default_quiet_end,
                onboarding_status="new",
                consent_version="wellness-v1",
                locale="en",
                notification_channel="telegram",
                proactive_enabled=True,
                proactive_max_per_day=self.settings.default_proactive_max_per_day,
                proactive_cooldown_minutes=self.settings.default_proactive_cooldown_minutes,
                monthly_llm_token_budget=500_000,
                monthly_llm_tokens_used=0,
                monthly_llm_cost_cents_used=0,
            )
            self.session.add(user)
            await self.session.flush()
            # Seed default rituals for new users
            await RitualService(self.session).seed_defaults_for_user(user)
        elif timezone and user.timezone != timezone:
            user.timezone = timezone
        return user

    async def get_or_create_thread(self, user_id: str, channel: str) -> ConversationThread:
        result = await self.session.execute(
            select(ConversationThread)
            .where(ConversationThread.user_id == user_id, ConversationThread.channel == channel)
            .order_by(ConversationThread.created_at.desc())
            .limit(1)
        )
        thread = result.scalar_one_or_none()
        if thread is None:
            thread = ConversationThread(
                user_id=user_id,
                channel=channel,
                is_primary=True,
                summary="",
                open_loop_count=0,
            )
            self.session.add(thread)
            await self.session.flush()
        return thread

    async def link_channel_account(
        self,
        user_id: str,
        channel: str,
        external_id: str,
        metadata: dict | None = None,
    ) -> None:
        result = await self.session.execute(
            select(ChannelAccount).where(
                ChannelAccount.channel == channel,
                ChannelAccount.external_id == external_id,
            )
        )
        account = result.scalar_one_or_none()
        if account is None:
            self.session.add(
                ChannelAccount(
                    user_id=user_id,
                    channel=channel,
                    external_id=external_id,
                    metadata_=metadata or {},
                )
            )
        elif account.user_id != user_id:
            account.user_id = user_id
            account.metadata_ = metadata or account.metadata_

    async def handle_event(
        self,
        event: ConversationEvent,
        timezone: str | None = None,
        *,
        account_id: str | None = None,
    ) -> MessageResponse:
        trace_id = new_trace_id()
        user = await self.ensure_user(event.user_id, timezone)
        account: Account | None = None
        if account_id:
            account = await self.session.get(Account, account_id)
            if account is not None and account.user_id is None:
                account.user_id = user.id
        inbound_event: InboundEvent | None = None
        if event.idempotency_key:
            existing = await self.session.execute(
                select(InboundEvent).where(
                    InboundEvent.channel == event.channel,
                    InboundEvent.idempotency_key == event.idempotency_key,
                )
            )
            inbound_event = existing.scalar_one_or_none()
            if inbound_event and inbound_event.response_payload:
                response_payload = {**inbound_event.response_payload, "idempotent_replay": True}
                return MessageResponse(**response_payload)
            if inbound_event is None:
                inbound_event = InboundEvent(
                    channel=event.channel,
                    idempotency_key=event.idempotency_key,
                    user_id=user.id,
                    trace_id=trace_id,
                )
                self.session.add(inbound_event)
                await self.session.flush()

        if event.external_user_id:
            await self.link_channel_account(
                user.id,
                event.channel,
                event.external_user_id,
                event.metadata,
            )
        thread = await self.get_or_create_thread(event.user_id, event.channel)
        last_interaction_at = thread.last_message_at
        now = utc_now()
        user.last_active_at = now
        user_message = Message(
            thread_id=thread.id,
            user_id=user.id,
            role="user",
            content=event.content,
            channel=event.channel,
            trace_id=trace_id,
            metadata_={
                **event.metadata,
                "content_type": event.content_type,
                "idempotency_key": event.idempotency_key,
                "trace_id": trace_id,
                "transcription_uncertain": self._transcription_uncertain(event.metadata),
            },
        )
        self.session.add(user_message)
        await self.session.flush()
        if inbound_event is not None:
            inbound_event.user_message_id = user_message.id
        is_command = event.content.startswith("/")
        live_safety_category = "wellness"

        if command_response := await self._handle_command(
            event,
            user,
            thread,
            user_message,
            trace_id,
            inbound_event,
        ):
            return command_response

        harness_mode = self._context_harness_mode()
        memory_service = MemoryService(self.session, self._embedding_client())
        candidate_memories = await memory_service.retrieve_relevant_memories(
            user.id,
            event.content,
            limit=(
                self.settings.memory_retrieval_limit
                if harness_mode == "legacy"
                else max(
                    self.settings.memory_retrieval_limit,
                    self.settings.context_harness_candidate_memory_limit,
                )
            ),
        )
        memory_candidates_payload = [self._memory_payload(memory) for memory in candidate_memories]
        legacy_memories = memory_candidates_payload[: self.settings.memory_retrieval_limit]
        soul_preferences = await self._soul_preferences_payload(user.id)
        engagement = await self._engagement_state(user.id)
        engagement_context = self._engagement_payload(engagement)
        raw_open_loops = await self._open_loops_payload(user.id)

        # WS7: score engagement for any recently surfaced open topics
        try:
            from healthclaw.inner.engagement import (
                filter_surfaceable_open_loops,
                score_open_topic_engagement,
            )
            await score_open_topic_engagement(user.id, event.content or "", self.session)
            open_loops = filter_surfaceable_open_loops(raw_open_loops)
        except Exception:
            open_loops = raw_open_loops  # fallback — never block the response

        streaks = await RitualStreakService(self.session).streaks_payload(user.id)
        recent_messages = await self._recent_messages_payload(
            thread.id,
            exclude_message_id=user_message.id,
            limit=self.settings.recent_message_context_limit,
        )
        memory_documents = await MarkdownMemoryService(self.session).documents_for_prompt(user)
        user_context = {
            "id": user.id,
            "timezone": user.timezone,
            "quiet_start": user.quiet_start,
            "quiet_end": user.quiet_end,
            "proactive_enabled": user.proactive_enabled,
            **engagement_context,
        }
        prompt_context = None
        if harness_mode != "legacy":
            prompt_context = ContextHarness(self.settings).build(
                user_content=event.content,
                time_context=build_time_context(user, last_interaction_at=last_interaction_at),
                memories=memory_candidates_payload,
                recent_messages=recent_messages,
                open_loops=open_loops,
                memory_documents=memory_documents,
                user_context=user_context,
                thread_summary=thread.summary or "",
                mode=harness_mode,
            )

        selected_memories = (
            prompt_context.memories
            if harness_mode == "active" and prompt_context
            else legacy_memories
        )
        selected_open_loops = (
            prompt_context.open_loops if harness_mode == "active" and prompt_context else open_loops
        )
        selected_recent_messages = (
            prompt_context.recent_messages
            if harness_mode == "active" and prompt_context
            else recent_messages
        )
        selected_memory_documents = (
            prompt_context.memory_documents
            if harness_mode == "active" and prompt_context
            else memory_documents
        )
        selected_thread_summary = (
            prompt_context.thread_summary if harness_mode == "active" and prompt_context else ""
        )
        selected_relationship_signals = (
            prompt_context.relationship_signals
            if harness_mode == "active" and prompt_context
            else []
        )
        context_harness_trace = self._context_harness_trace_payload(
            mode=harness_mode,
            legacy_memories=legacy_memories,
            selected_memories=selected_memories,
            legacy_open_loops=open_loops,
            selected_open_loops=selected_open_loops,
            legacy_recent_messages=recent_messages,
            selected_recent_messages=selected_recent_messages,
            legacy_memory_documents=memory_documents,
            selected_memory_documents=selected_memory_documents,
            selected_thread_summary=selected_thread_summary,
            selected_relationship_signals=selected_relationship_signals,
            prompt_context=prompt_context,
            memory_candidates=memory_candidates_payload,
        )
        state = await agent_graph.ainvoke(
            {
                "user": user_context,
                "user_content": event.content,
                "channel": event.channel,
                "user_message": {
                    "id": user_message.id,
                    "is_command": is_command,
                    "content_type": event.content_type,
                    "attachments": event.metadata.get("attachments"),
                    "transcription_uncertain": self._transcription_uncertain(event.metadata),
                },
                "memories": selected_memories,
                "soul_preferences": soul_preferences,
                "open_loops": selected_open_loops,
                "streaks": streaks,
                "recent_messages": selected_recent_messages,
                "memory_documents": selected_memory_documents,
                "thread_summary": selected_thread_summary,
                "relationship_signals": selected_relationship_signals,
                "trace_metadata": {
                    "trace_id": trace_id,
                    "thread_id": thread.id,
                    "last_interaction_at": last_interaction_at,
                    "content_type": event.content_type,
                    "context_harness": context_harness_trace,
                },
            },
        )
        await self._execute_actions(
            state=state,
            user=user,
            thread=thread,
            user_message=user_message,
        )

        assistant_message = Message(
            thread_id=thread.id,
            user_id=user.id,
            role="assistant",
            content=state["response"],
            channel=event.channel,
            trace_id=trace_id,
            metadata_={
                "trace_id": trace_id,
                "safety_category": live_safety_category,
                "generation": state.get("trace_metadata", {}).get("generation", {}),
            },
        )
        self.session.add(assistant_message)
        await self.session.flush()
        thread.last_message_at = utc_now()
        meaningful_exchange = is_meaningful_exchange(
            event.content,
            content_type=event.content_type,
            is_command=is_command,
        )
        streak_updates: list[dict[str, object]] = []
        await self._update_engagement_state(
            user.id,
            user_message.created_at,
            assistant_message.created_at,
            content=event.content,
            voice_note=event.content_type == "voice_transcript",
            long_lapse=bool(state["time_context"].get("long_lapse")),
            meaningful_exchange=meaningful_exchange,
        )
        if meaningful_exchange:
            advanced_streaks = await RitualStreakService(self.session).record_meaningful_exchange(
                user,
                user_message.created_at,
                live_safety_category,
            )
            streak_updates = self._streak_progress_payload(advanced_streaks)
            if streak_updates:
                logger.info("Advanced ritual streaks for %s: %s", user.id, streak_updates)
        state.setdefault("trace_metadata", {})["streak_updates"] = streak_updates
        assistant_message.metadata_["generation"]["streak_updates"] = streak_updates

        memory_updates: list[dict] = []
        heartbeat_service = HeartbeatService(self.session)
        for raw_mutation in state.get("memory_mutations", []):
            mutation = (
                raw_mutation
                if isinstance(raw_mutation, MemoryMutation)
                else MemoryMutation(**raw_mutation)
            )
            memory, _outcome = await memory_service.upsert_memory(
                user.id,
                mutation,
                [user_message.id],
                trace_id=trace_id,
            )
            memory_updates.append(
                {
                    "id": memory.id,
                    "kind": memory.kind,
                    "key": memory.key,
                    "layer": memory.layer,
                    "confidence": memory.confidence,
                }
            )
            if mutation.kind in {"commitment", "open_loop"} and not bool(
                mutation.metadata.get("skip_open_loop_creation")
            ):
                title = str(
                    mutation.value.get("text") or mutation.value.get("summary") or mutation.key
                )
                await heartbeat_service.create_open_loop(
                    user_id=user.id,
                    thread_id=thread.id,
                    source_message_id=user_message.id,
                    title=title,
                    kind=mutation.kind,
                )
        await MarkdownMemoryService(self.session).refresh_for_user(user)
        await heartbeat_service.ensure_refresh_jobs(user.id)
        await self._update_thread_summary(thread, event.content, assistant_message.content, user.id)
        await self._record_usage(user, state.get("trace_metadata", {}).get("generation", {}))

        # WS7: bootstrap user_pattern rows once enough turns have accumulated
        if self.settings.bootstrap_patterns_after_turns > 0:
            try:
                from healthclaw.memory.bootstrap_patterns import seed_observable_patterns
                await seed_observable_patterns(
                    user.id,
                    self.session,
                    min_turns=self.settings.bootstrap_patterns_after_turns,
                )
            except Exception:
                pass  # non-critical; never block the response

        self.session.add(
            SafetyEvent(
                user_id=user.id,
                message_id=user_message.id,
                category="model_managed",
                severity="info",
                action="llm_response",
            )
        )
        self.session.add(
            TraceRef(
                user_id=user.id,
                message_id=user_message.id,
                provider="healthclaw",
                trace_id=trace_id,
                redacted=not self.settings.trace_raw_content,
            )
        )
        self.session.add(
            AgentCheckpoint(
                thread_id=thread.id,
                user_id=user.id,
                channel=event.channel,
                trace_id=trace_id,
                state=redacted_payload(
                    {
                        "response": state["response"],
                        "safety_category": live_safety_category,
                        "time_context": state["time_context"],
                        "trace_metadata": state.get("trace_metadata", {}),
                        "memory_updates": memory_updates,
                    },
                    include_raw_content=self.settings.trace_raw_content,
                ),
            )
        )
        response_payload = MessageResponse(
            trace_id=trace_id,
            idempotent_replay=False,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            thread_id=thread.id,
            response=assistant_message.content,
            safety_category=live_safety_category,
            time_context=state["time_context"],
            memory_updates=memory_updates,
        ).model_dump(mode="json")
        if inbound_event is not None:
            inbound_event.assistant_message_id = assistant_message.id
            inbound_event.response_payload = response_payload
        if account is not None:
            await AccountService(self.session, self.settings).increment_message_usage(account)
        await self.session.commit()

        return MessageResponse(**response_payload)

    async def _execute_actions(
        self,
        *,
        state: dict,
        user: User,
        thread: ConversationThread,
        user_message: Message,
    ) -> None:
        heartbeat = HeartbeatService(self.session)
        proactivity = ProactivityService(self.session)
        executed: list[dict[str, object]] = []
        dropped = list(
            state.get("trace_metadata", {}).get("action_execution", {}).get("dropped", [])
        )
        proposed: list[dict[str, object]] = []
        for raw_action in state.get("actions_taken", []):
            action_type = str(raw_action.get("type") or "")
            payload = (
                raw_action.get("payload")
                if isinstance(raw_action.get("payload"), dict)
                else {}
            )
            rationale = str(raw_action.get("rationale") or "").strip() or None
            try:
                if action_type == "create_reminder":
                    action = CreateReminderPayload.model_validate(payload)
                    due_at = self._parse_iso_datetime(action.due_at_iso)
                    if due_at is None:
                        dropped.append({"type": action_type, "reason": "due_at_invalid"})
                        continue
                    reminder = await proactivity.create_reminder(
                        user_id=user.id,
                        text=action.text,
                        due_at=due_at,
                        channel=state.get("channel", "telegram"),
                        idempotency_key=action.idempotency_key,
                    )
                    executed.append({"type": action_type, "id": reminder.id})
                elif action_type == "create_open_loop":
                    action = CreateOpenLoopPayload.model_validate(payload)
                    due_after = self._parse_iso_datetime(action.due_after_iso)
                    loop = await heartbeat.create_open_loop(
                        user_id=user.id,
                        thread_id=thread.id,
                        source_message_id=user_message.id,
                        title=action.title,
                        kind=action.kind,
                        due_after=due_after,
                    )
                    executed.append({"type": action_type, "id": loop.id})
                elif action_type == "close_open_loop":
                    action = CloseOpenLoopPayload.model_validate(payload)
                    async with start_span(
                        "loop.close.execute",
                        attributes={
                            "open_loop_id": action.id,
                            "decided_by": "llm",
                            "outcome": action.outcome,
                        },
                    ) as close_span:
                        open_loop = await self.session.get(OpenLoop, action.id)
                        if open_loop is None or open_loop.status != "open":
                            dropped.append({"type": action_type, "reason": "open_loop_missing"})
                            close_span.set_attribute("closed", False)
                            continue
                        open_loop.status = "closed"
                        open_loop.closed_at = utc_now()
                        open_loop.metadata_ = {
                            **(open_loop.metadata_ or {}),
                            "closed_summary": action.summary,
                            "closed_outcome": action.outcome,
                        }
                        loop_thread = await self.session.get(
                            ConversationThread, open_loop.thread_id
                        )
                        if loop_thread is not None and loop_thread.open_loop_count > 0:
                            loop_thread.open_loop_count -= 1
                        open_jobs = await self.session.execute(
                            select(HeartbeatJob).where(
                                HeartbeatJob.open_loop_id == open_loop.id,
                                HeartbeatJob.status == "scheduled",
                            )
                        )
                        for job in open_jobs.scalars():
                            job.status = "suppressed"
                            job.last_error = "open_loop_closed"
                        state.setdefault("memory_mutations", []).append(
                            {
                                "kind": "relationship",
                                "key": f"closed_loop:{open_loop.id}",
                                "value": {
                                    "summary": action.summary,
                                    "title": open_loop.title,
                                    "outcome": action.outcome,
                                },
                                "confidence": 0.7,
                                "reason": "User confirmed completion of open loop.",
                                "layer": "relationship",
                            }
                        )
                        print(f"DEBUG_CLOSE: appending mutation for key=closed_loop:{open_loop.id}", flush=True)
                        close_span.set_attribute("closed", True)
                    executed.append(
                        {
                            "type": action_type,
                            "id": action.id,
                            "summary": action.summary,
                            "outcome": action.outcome,
                        }
                    )
                elif action_type == "set_user_timezone":
                    # WS7: persist timezone from LLM capture
                    tz_action = SetUserTimezonePayload.model_validate(payload)
                    try:
                        from zoneinfo import ZoneInfo
                        ZoneInfo(tz_action.tz)  # validate IANA name
                        user.timezone = tz_action.tz
                        user.timezone_confidence = tz_action.confidence
                        if tz_action.lat is not None:
                            user.home_lat = tz_action.lat
                        if tz_action.lon is not None:
                            user.home_lon = tz_action.lon
                        executed.append({"type": action_type, "tz": tz_action.tz})
                        logger.info(
                            "set_user_timezone: user=%s tz=%s conf=%.2f source=%s",
                            user.id, tz_action.tz, tz_action.confidence, tz_action.source,
                        )
                    except Exception as tz_err:
                        dropped.append({"type": action_type, "reason": f"invalid_tz: {tz_err}"})
                elif action_type == "open_topic":
                    # WS7: track agent's suggestion as an open topic with cooldown
                    from datetime import timedelta as _timedelta
                    ot_action = OpenTopicPayload.model_validate(payload)
                    now_utc = utc_now()
                    loop = OpenLoop(
                        user_id=user.id,
                        thread_id=thread.id,
                        source_message_id=user_message.id,
                        kind=ot_action.kind,
                        title=ot_action.title,
                        status="open",
                        cooldown_hours=ot_action.cooldown_hours,
                        max_surfaces=ot_action.max_surfaces,
                        surface_count=1,
                        last_surfaced_at=now_utc,
                        cooldown_until=now_utc + _timedelta(hours=ot_action.cooldown_hours),
                    )
                    self.session.add(loop)
                    await self.session.flush()
                    executed.append({"type": action_type, "id": loop.id, "title": ot_action.title})
                    logger.debug(
                        "open_topic: user=%s title=%s cooldown=%dh",
                        user.id, ot_action.title, ot_action.cooldown_hours,
                    )
                elif action_type and action_type != "none":
                    proposed_action = ProposedAction(
                        user_id=user.id,
                        type=action_type[:128],
                        payload=payload,
                        rationale=rationale,
                        status="unknown_type",
                    )
                    self.session.add(proposed_action)
                    await self.session.flush()
                    proposed.append({"type": action_type, "id": proposed_action.id})
                    executed.append(
                        {
                            "type": action_type,
                            "id": proposed_action.id,
                            "status": "unknown_type",
                        }
                    )
            except Exception:
                dropped.append({"type": action_type or "unknown", "reason": "execution_error"})

        if proposed:
            proposed_types = ", ".join(str(item["type"]) for item in proposed[:3])
            state["response"] = (
                state["response"].rstrip()
                + "\n\n"
                + f"I tried to handle {proposed_types}, but I don't have that capability yet."
            )

        state["actions_taken"] = executed
        action_types = [str(item.get("type") or "") for item in executed]
        execution_trace = {
            "action_count": len(executed),
            "action_types": ",".join(action_types),
            "action.consistency": state.get("trace_metadata", {})
            .get("action_execution", {})
            .get("action.consistency", "ok"),
            "dropped": dropped,
            "proposed": proposed,
        }
        state.setdefault("trace_metadata", {})["action_execution"] = execution_trace

    @staticmethod
    def _parse_iso_datetime(value: str | None):
        from datetime import UTC, datetime

        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    async def _handle_command(
        self,
        event: ConversationEvent,
        user: User,
        thread: ConversationThread,
        user_message: Message,
        trace_id: str,
        inbound_event: InboundEvent | None,
    ) -> MessageResponse | None:
        if not event.content.startswith("/"):
            return None
        command, _, argument = event.content.strip().partition(" ")
        command = command.split("@", 1)[0].lower()
        memory_service = MemoryService(self.session, self._embedding_client())
        if command == "/start":
            user.onboarding_status = "active"
            response = (
                "Hey, I am Healthclaw. I will keep this simple and get to know you as we go. "
                "What kind of day are you having?"
            )
        elif command == "/settings":
            response = (
                f"Timezone: {user.timezone}\n"
                f"Quiet hours: {user.quiet_start}-{user.quiet_end}\n"
                f"Proactive support: {'on' if user.proactive_enabled else 'paused'}\n"
                f"Daily limit: {user.proactive_max_per_day}"
            )
        elif command == "/memory":
            response = await memory_service.summarize_user_memory(user.id)
        elif command == "/rituals":
            response = await self._handle_rituals_command(user, argument)
        elif command == "/streak":
            response = await self._handle_streak_command(user)
        elif command == "/heartbeat":
            response = await self._handle_heartbeat_command(user, argument)
        elif command == "/soul":
            response = await self._handle_soul_command(user, argument)
        elif command == "/pause":
            user.proactive_enabled = False
            await self._adjust_trust(user.id, -0.03, "pause_proactivity")
            response = "Proactive follow-ups are paused. You can still message me anytime."
        elif command == "/resume":
            user.proactive_enabled = True
            user.proactive_paused_until = None
            response = "Proactive follow-ups are back on, still bounded by quiet hours and limits."
        elif command == "/forget":
            removed = await memory_service.deactivate_matching_memories(user.id, argument)
            response = (
                f"I removed {removed} matching memory item."
                if removed
                else "Tell me what to forget after the command, for example: /forget late snacks"
            )
        elif command == "/export":
            response = "Active memory export:\n" + await memory_service.summarize_user_memory(
                user.id,
                limit=50,
            )
        elif command == "/delete":
            removed = 0
            for memory in await memory_service.list_memories(user.id):
                if await memory_service.delete_user_memory(user.id, memory.id, trace_id=trace_id):
                    removed += 1
            user.proactive_enabled = False
            await self._adjust_trust(user.id, -0.05, "delete_memory")
            response = (
                f"I deleted {removed} active memory item and paused proactive follow-ups. "
                "Your account shell remains so this chat can receive the confirmation."
            )
        else:
            response = (
                "Available commands: /start, /settings, /memory, /pause, /resume, "
                "/rituals, /streak, /heartbeat, /soul, /forget, /export, /delete."
            )
        assistant_message = Message(
            thread_id=thread.id,
            user_id=user.id,
            role="assistant",
            content=response,
            channel=event.channel,
            trace_id=trace_id,
            metadata_={"trace_id": trace_id, "command": command},
        )
        self.session.add(assistant_message)
        await self.session.flush()
        thread.last_message_at = utc_now()
        await self._update_engagement_state(
            user.id,
            user_message.created_at,
            assistant_message.created_at,
            content=event.content,
            voice_note=False,
            long_lapse=False,
            meaningful_exchange=False,
        )
        self.session.add(
            TraceRef(
                user_id=user.id,
                message_id=user_message.id,
                provider="healthclaw",
                trace_id=trace_id,
                redacted=True,
            )
        )
        self.session.add(
            AgentCheckpoint(
                thread_id=thread.id,
                user_id=user.id,
                channel=event.channel,
                trace_id=trace_id,
                state=redacted_payload(
                    {"response": response, "command": command, "trace_id": trace_id},
                    include_raw_content=False,
                ),
            )
        )
        response_payload = MessageResponse(
            trace_id=trace_id,
            idempotent_replay=False,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            thread_id=thread.id,
            response=response,
            safety_category="command",
            time_context={},
            memory_updates=[],
        ).model_dump(mode="json")
        if inbound_event is not None:
            inbound_event.assistant_message_id = assistant_message.id
            inbound_event.response_payload = response_payload
        await self.session.commit()
        return MessageResponse(**response_payload)

    async def _handle_rituals_command(self, user: User, argument: str) -> str:
        mode = argument.strip().lower()
        result = await self.session.execute(
            select(Ritual).where(Ritual.user_id == user.id).order_by(Ritual.created_at.asc())
        )
        rituals = list(result.scalars())
        if mode in {"off", "disable", "disabled"}:
            for ritual in rituals:
                ritual.enabled = False
            return "Ritual check-ins are off. You can turn them back on with /rituals on."
        if mode in {"on", "enable", "enabled"}:
            if not rituals:
                rituals = await RitualService(self.session).seed_defaults_for_user(user)
            for ritual in rituals:
                ritual.enabled = True
            return "Ritual check-ins are on, still bounded by quiet hours and limits."
        if not rituals:
            rituals = await RitualService(self.session).seed_defaults_for_user(user)
        lines = ["Rituals:"]
        for ritual in rituals:
            status = "on" if ritual.enabled else "off"
            lines.append(f"- {ritual.title}: {status}, schedule {ritual.schedule_cron}")
        lines.append("Use /rituals off or /rituals on.")
        return "\n".join(lines)

    async def _handle_streak_command(self, user: User) -> str:
        result = await self.session.execute(
            select(Ritual).where(Ritual.user_id == user.id).order_by(Ritual.created_at.asc())
        )
        rituals = list(result.scalars())
        if not rituals:
            rituals = await RitualService(self.session).seed_defaults_for_user(user)

        enabled = [ritual for ritual in rituals if ritual.enabled]
        if not enabled:
            return "Ritual check-ins are off. You can turn them back on with /rituals on."

        payload = await RitualStreakService(self.session).streaks_payload(user.id)
        lines = ["Rituals and streaks:"]
        if payload:
            for item in payload:
                title = str(item.get("title") or item.get("kind") or "Ritual")
                count = int(item.get("streak_count") or 0)
                last = str(item.get("streak_last_date") or "unknown")
                lines.append(f"- {title}: {count}-day streak (last activity: {last})")
        else:
            lines.append("- (none yet): reply within 12h of a check-in to start a streak.")
        return "\n".join(lines)

    async def _handle_heartbeat_command(self, user: User, argument: str) -> str:
        text = argument.strip()
        if not text:
            body = user.heartbeat_md.strip() or "(empty)"
            return (
                "Heartbeat intent:\n"
                f"{body}\n"
                "Use /heartbeat <text> to update it, or /heartbeat off to clear it."
            )
        if text.lower() in {"off", "clear", "reset"}:
            user.heartbeat_md = ""
            user.heartbeat_md_updated_at = None
            return (
                "Heartbeat intent cleared. Autonomous outreach will rely on rituals and open loops."
            )
        user.heartbeat_md = canonicalize_heartbeat_md(text)
        user.heartbeat_md_updated_at = utc_now()
        return "Heartbeat intent saved. I will use it as standing guidance for proactive check-ins."

    async def _handle_soul_command(self, user: User, argument: str) -> str:
        if argument.strip():
            return "Soul revert is not enabled yet. The current soul overlay is below."
        docs = await MarkdownMemoryService(self.session).refresh_for_user(user)
        soul = next((doc for doc in docs if doc.kind == "SOUL"), None)
        return "Soul overlay:\n" + (soul.content if soul is not None else "No soul overlay yet.")

    @staticmethod
    def _transcription_uncertain(metadata: dict) -> bool:
        transcription = metadata.get("transcription")
        if not isinstance(transcription, dict):
            return False
        confidence = transcription.get("confidence")
        return isinstance(confidence, int | float) and confidence < 0.65

    @staticmethod
    def _memory_payload(memory) -> dict:
        return {
            "id": memory.id,
            "kind": memory.kind,
            "key": memory.key,
            "layer": memory.layer,
            "value": memory.value,
            "confidence": memory.confidence,
            "freshness_score": memory.freshness_score,
            "semantic_text": memory.semantic_text,
            "visibility": memory.visibility,
            "last_confirmed_at": memory.last_confirmed_at,
            "last_accessed_at": memory.last_accessed_at,
            "created_at": memory.created_at,
            "updated_at": memory.updated_at,
        }

    def _context_harness_mode(self) -> str:
        mode = self.settings.context_harness_mode.strip().lower()
        return mode if mode in {"legacy", "shadow", "active"} else "active"

    @staticmethod
    def _memory_keys(memories: list[dict]) -> list[str]:
        return [f"{memory.get('kind')}:{memory.get('key')}" for memory in memories]

    @staticmethod
    def _document_kinds(memory_documents: dict[str, str]) -> list[str]:
        return [kind for kind, content in memory_documents.items() if str(content or "").strip()]

    def _context_harness_trace_payload(
        self,
        *,
        mode: str,
        legacy_memories: list[dict],
        selected_memories: list[dict],
        legacy_open_loops: list[dict],
        selected_open_loops: list[dict],
        legacy_recent_messages: list[dict],
        selected_recent_messages: list[dict],
        legacy_memory_documents: dict[str, str],
        selected_memory_documents: dict[str, str],
        selected_thread_summary: str,
        selected_relationship_signals: list[str],
        prompt_context,
        memory_candidates: list[dict],
    ) -> dict[str, object]:
        applied = mode == "active"
        payload = {
            "mode": mode if mode in {"legacy", "shadow", "active"} else "active",
            "applied": applied,
            "candidate_memory_count": len(memory_candidates),
            "selected_memory_keys": (
                prompt_context.metadata.get("selected_memory_keys", [])
                if prompt_context is not None
                else self._memory_keys(legacy_memories)
            ),
            "applied_memory_keys": self._memory_keys(selected_memories),
            "selected_open_loop_ids": (
                prompt_context.metadata.get("selected_open_loop_ids", [])
                if prompt_context is not None
                else [str(loop.get("id") or "") for loop in selected_open_loops]
            ),
            "applied_counts": {
                "memories": len(selected_memories),
                "open_loops": len(selected_open_loops),
                "recent_messages": len(selected_recent_messages),
                "documents": len(self._document_kinds(selected_memory_documents)),
                "relationship_signals": len(selected_relationship_signals),
            },
            "applied_thread_summary": bool(selected_thread_summary),
            "budget_usage": (
                prompt_context.metadata.get("budget_usage", {})
                if prompt_context is not None
                else {}
            ),
        }
        if prompt_context is not None:
            payload["shadow_selected_memory_keys"] = self._memory_keys(prompt_context.memories)
            payload["shadow_selected_open_loop_ids"] = [
                str(loop.get("id") or "") for loop in prompt_context.open_loops
            ]
            payload["shadow_counts"] = {
                "memories": len(prompt_context.memories),
                "open_loops": len(prompt_context.open_loops),
                "recent_messages": len(prompt_context.recent_messages),
                "documents": len(self._document_kinds(prompt_context.memory_documents)),
                "relationship_signals": len(prompt_context.relationship_signals),
            }
            payload["shadow_thread_summary"] = bool(prompt_context.thread_summary)
        if mode == "shadow":
            payload["shadow_delta"] = {
                "memory_keys_changed": self._memory_keys(legacy_memories)
                != self._memory_keys(prompt_context.memories if prompt_context is not None else []),
                "open_loops_changed": len(legacy_open_loops)
                != len(prompt_context.open_loops if prompt_context is not None else []),
                "recent_messages_changed": len(legacy_recent_messages)
                != len(prompt_context.recent_messages if prompt_context is not None else []),
                "documents_changed": self._document_kinds(legacy_memory_documents)
                != self._document_kinds(prompt_context.memory_documents if prompt_context else {}),
            }
        return payload

    @staticmethod
    def _streak_progress_payload(rituals: list[Ritual]) -> list[dict[str, object]]:
        return [
            {
                "kind": ritual.kind,
                "title": ritual.title,
                "streak_count": int(ritual.streak_count or 0),
                "streak_last_date": ritual.streak_last_date,
            }
            for ritual in rituals
        ]

    async def _open_loops_payload(self, user_id: str) -> list[dict]:
        from datetime import UTC, datetime

        result = await self.session.execute(
            select(OpenLoop)
            .where(
                OpenLoop.user_id == user_id,
                OpenLoop.status == "open",
            )
            .limit(10)
        )
        now = datetime.now(UTC)
        loops = []
        for loop in result.scalars():
            created = loop.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            age_hours = (now - created).total_seconds() / 3600
            loops.append(
                {
                    "id": loop.id,
                    "title": loop.title,
                    "kind": loop.kind,
                    "status": loop.status,
                    "age_hours": round(age_hours, 1),
                    # WS7: engagement gating fields
                    "surface_count": getattr(loop, "surface_count", 0) or 0,
                    "max_surfaces": getattr(loop, "max_surfaces", 2) or 2,
                    "cooldown_until": (
                        loop.cooldown_until.isoformat()
                        if getattr(loop, "cooldown_until", None)
                        else None
                    ),
                }
            )
        return loops

    async def _recent_messages_payload(
        self,
        thread_id: str,
        *,
        exclude_message_id: str,
        limit: int = 30,
    ) -> list[dict[str, str]]:
        result = await self.session.execute(
            select(Message)
            .where(
                Message.thread_id == thread_id,
                Message.id != exclude_message_id,
                Message.role.in_(["user", "assistant"]),
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = list(reversed(list(result.scalars())))
        payload: list[dict[str, str]] = []
        total_chars = 0
        max_chars = self.settings.recent_message_context_max_chars
        for message in messages:
            content = message.content.strip()[:800]
            if not content:
                continue
            next_total = total_chars + len(content)
            if payload and next_total > max_chars:
                break
            payload.append({"role": message.role, "content": content})
            total_chars = next_total
        return payload

    async def _soul_preferences_payload(self, user_id: str) -> dict:
        result = await self.session.execute(
            select(UserSoulPreference).where(UserSoulPreference.user_id == user_id)
        )
        preferences = result.scalar_one_or_none()
        if preferences is None:
            return {}
        return {
            "tone_preferences": preferences.tone_preferences,
            "response_preferences": preferences.response_preferences,
            "blocked_policy_keys": preferences.blocked_policy_keys,
        }

    async def _engagement_state(self, user_id: str) -> UserEngagementState | None:
        result = await self.session.execute(
            select(UserEngagementState).where(UserEngagementState.user_id == user_id)
        )
        return result.scalar_one_or_none()

    def _engagement_payload(self, engagement: UserEngagementState | None) -> dict:
        relationship = build_relationship_context(engagement)
        return {
            "trust_level": engagement.trust_level if engagement is not None else 0.3,
            "sentiment_ema": relationship["sentiment_ema"],
            "voice_text_ratio": relationship["voice_text_ratio"],
            "reply_latency_seconds_ema": relationship["reply_latency_seconds_ema"],
            "last_meaningful_exchange_at": relationship["last_meaningful_exchange_at"],
        }

    async def _update_engagement_state(
        self,
        user_id: str,
        user_message_at,
        assistant_message_at,
        *,
        content: str,
        voice_note: bool,
        long_lapse: bool,
        meaningful_exchange: bool,
    ) -> None:
        result = await self.session.execute(
            select(UserEngagementState).where(UserEngagementState.user_id == user_id)
        )
        engagement = result.scalar_one_or_none()
        if engagement is None:
            engagement = UserEngagementState(
                user_id=user_id,
                conversation_count=0,
                voice_note_count=0,
                lapse_count=0,
                trust_level=0.3,
                sentiment_ema=0.0,
                voice_text_ratio=0.0,
                metadata_={},
            )
            self.session.add(engagement)
        previous_assistant_message_at = engagement.last_assistant_message_at
        engagement.last_seen_at = assistant_message_at
        engagement.last_user_message_at = user_message_at
        engagement.last_assistant_message_at = assistant_message_at
        engagement.conversation_count += 1
        if long_lapse:
            engagement.lapse_count += 1
        if voice_note:
            engagement.voice_note_count += 1
        if not meaningful_exchange:
            return

        update_meaningful_engagement(
            engagement,
            content=content,
            voice_note=voice_note,
            user_message_at=user_message_at,
            previous_assistant_message_at=previous_assistant_message_at,
        )
        metadata = dict(engagement.metadata_ or {})
        meaningful_exchange_count = int(metadata.get("meaningful_exchange_count", 0)) + 1
        metadata["meaningful_exchange_count"] = meaningful_exchange_count
        engagement.metadata_ = metadata

        if voice_note:
            engagement.trust_level = min(1.0, engagement.trust_level + 0.02)
        if meaningful_exchange_count % 5 == 0:
            engagement.trust_level = min(1.0, engagement.trust_level + 0.01)

    async def _adjust_trust(self, user_id: str, delta: float, reason: str) -> None:
        result = await self.session.execute(
            select(UserEngagementState).where(UserEngagementState.user_id == user_id)
        )
        engagement = result.scalar_one_or_none()
        if engagement is None:
            engagement = UserEngagementState(user_id=user_id, metadata_={})
            self.session.add(engagement)
        engagement.trust_level = max(0.0, min(1.0, engagement.trust_level + delta))
        engagement.metadata_ = {
            **(engagement.metadata_ or {}),
            "last_trust_adjustment_reason": reason,
        }

    async def _record_usage(self, user: User, generation: dict) -> None:
        usage = generation.get("usage")
        if not isinstance(usage, dict):
            return
        tokens = usage.get("total_tokens")
        if not isinstance(tokens, int):
            return
        user.monthly_llm_tokens_used += tokens
        period_key = utc_now().strftime("%Y-%m")
        result = await self.session.execute(
            select(UserQuota).where(
                UserQuota.user_id == user.id,
                UserQuota.period_key == period_key,
            )
        )
        quota = result.scalar_one_or_none()
        if quota is None:
            self.session.add(
                UserQuota(
                    user_id=user.id,
                    period_key=period_key,
                    token_budget=user.monthly_llm_token_budget,
                    tokens_used=tokens,
                    cost_cents_used=0,
                )
            )
        else:
            quota.tokens_used += tokens
            quota.token_budget = user.monthly_llm_token_budget
            quota.cost_cents_used += 0

    async def _update_thread_summary(
        self,
        thread: ConversationThread,
        user_content: str,
        response: str,
        user_id: str,
    ) -> None:
        count_result = await self.session.execute(
            select(func.count(Message.id)).where(
                Message.thread_id == thread.id,
                Message.role == "user",
            )
        )
        turn_count = count_result.scalar() or 0

        if turn_count % self.settings.thread_summary_compact_every != 0:
            snippet = f"User: {user_content[:160]} | Assistant: {response[:160]}"
            existing = thread.summary or ""
            thread.summary = f"{existing}\n{snippet}"[-2000:].strip()
            return

        recent_result = await self.session.execute(
            select(Message)
            .where(
                Message.thread_id == thread.id,
                Message.role.in_(["user", "assistant"]),
            )
            .order_by(Message.created_at.desc())
            .limit(self.settings.thread_summary_compact_every * 2)
        )
        recent_messages = list(reversed(list(recent_result.scalars())))
        recent_turns = [
            {"role": msg.role, "content": msg.content[:300]} for msg in recent_messages
        ]

        try:
            digest = await compact_thread_summary(
                prior_summary=thread.summary or "",
                recent_turns=recent_turns,
                user_id=user_id,
                thread_id=thread.id,
            )
            thread.summary = digest[: self.settings.thread_summary_max_chars]
        except Exception as exc:
            from opentelemetry import trace
            tracer = trace.get_tracer("healthclaw")
            with tracer.start_as_current_span("thread.compact.error") as span:
                span.record_exception(exc)
