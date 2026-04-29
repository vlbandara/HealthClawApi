from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from healthclaw.agent.wellbeing import parse_delay_minutes
from healthclaw.channels.telegram import TelegramAdapter
from healthclaw.core.config import get_settings
from healthclaw.core.tracing import new_trace_id
from healthclaw.db.session import SessionLocal
from healthclaw.heartbeat.service import HeartbeatService
from healthclaw.proactivity.service import ProactivityService
from healthclaw.services.account import AccountService

logger = logging.getLogger(__name__)


async def process_due_reminders() -> dict[str, int]:
    settings = get_settings()
    telegram = TelegramAdapter(settings)
    async with SessionLocal() as session:
        service = ProactivityService(session)
        accounts = AccountService(session, settings)
        reminders = await service.due_reminders(datetime.now(UTC))
        sent = 0
        suppressed = 0
        deferred = 0
        failed = 0
        for reminder in reminders:
            now = datetime.now(UTC)
            trace_id = new_trace_id()
            decision = await service.should_send(reminder, now)
            if not decision.reach_out or decision.when != "now":
                delay_minutes = parse_delay_minutes(decision.when)
                if delay_minutes is not None:
                    reminder.due_at = now + timedelta(minutes=delay_minutes)
                    await service.record_decision(
                        reminder,
                        "deferred",
                        decision.rationale,
                        trace_id=trace_id,
                    )
                    deferred += 1
                else:
                    reminder.status = "suppressed"
                    await service.record_decision(
                        reminder,
                        "suppressed",
                        decision.rationale,
                        trace_id=trace_id,
                    )
                    suppressed += 1
                continue

            external_id = await service.external_channel_id(reminder.user_id, reminder.channel)
            if reminder.channel != "telegram" or external_id is None:
                reminder.status = "failed"
                reminder.last_error = "channel_not_deliverable"
                reminder.attempts += 1
                await service.record_decision(
                    reminder, "failed", "channel_not_deliverable", trace_id=trace_id
                )
                failed += 1
                continue

            bot_token: str | None = None
            if settings.multi_tenant_mode:
                bot_token = await accounts.get_bot_token_for_user(reminder.user_id)
                if bot_token is None:
                    reminder.status = "failed"
                    reminder.last_error = "bot_token_unavailable"
                    reminder.attempts += 1
                    await service.record_decision(
                        reminder, "failed", "bot_token_unavailable", trace_id=trace_id
                    )
                    failed += 1
                    continue
            try:
                reminder_text = decision.message_seed or reminder.text
                await telegram.send_status(external_id, "typing", bot_token=bot_token)
                await telegram.send_message(external_id, reminder_text, bot_token=bot_token)
            except Exception:
                reminder.status = "failed"
                reminder.last_error = "send_error"
                reminder.attempts += 1
                await service.record_decision(reminder, "failed", "send_error", trace_id=trace_id)
                failed += 1
            else:
                reminder.status = "sent"
                reminder.sent_at = datetime.now(UTC)
                reminder.attempts += 1
                await service.record_decision(
                    reminder,
                    "sent",
                    decision.rationale,
                    trace_id=trace_id,
                )
                sent += 1
        await session.commit()
        result = {
            "due": len(reminders),
            "sent": sent,
            "suppressed": suppressed,
            "deferred": deferred,
            "failed": failed,
        }
        logger.info("Reminder sweep completed: %s", result)
        return result


async def process_due_heartbeats() -> dict[str, int]:
    settings = get_settings()
    telegram = TelegramAdapter(settings)
    async with SessionLocal() as session:
        heartbeat = HeartbeatService(session, settings)
        proactivity = ProactivityService(session)
        accounts = AccountService(session, settings)
        now = datetime.now(UTC)

        # Enqueue due rituals for all users, then schedule memory/open-loop work
        from healthclaw.heartbeat.rituals import RitualService

        ritual_service = RitualService(session)
        ritual_jobs = await ritual_service.enqueue_due_for_all_users(now)
        scheduled_work = await heartbeat.schedule_due_work(now)

        jobs = await heartbeat.due_jobs(now)
        sent = 0
        suppressed = 0
        deferred = 0
        soft_skipped = 0
        failed = 0

        for job in jobs:
            now = datetime.now(UTC)
            trace_id = new_trace_id()

            # Hard gate first (cheap Python checks)
            eligible, reason = await heartbeat.should_send(job, now)
            if not eligible:
                # Defer quiet-hours jobs (both regular blocked by quiet hours,
                # and internal jobs awaiting quiet hours) +30 min so they retry.
                if reason in {"quiet_hours", "awaiting_quiet_hours"}:
                    job.due_at = now + timedelta(minutes=30)
                    await heartbeat.record_event(
                        job, "deferred", reason, trace_id=trace_id, skip_reason=reason
                    )
                    deferred += 1
                else:
                    job.status = "suppressed"
                    await heartbeat.record_event(
                        job, "suppressed", reason, trace_id=trace_id, skip_reason=reason
                    )
                    suppressed += 1
                continue

            # Internal job branch: dream/consolidate run directly, no Telegram
            if job.kind in {"dream", "consolidate"}:
                try:
                    if job.kind == "dream":
                        from healthclaw.memory.dream import DreamService

                        await DreamService(session, settings).run_for_user(job.user_id)
                    else:
                        from healthclaw.memory.consolidator import ConsolidatorService
                        from healthclaw.memory.service import MemoryService

                        await ConsolidatorService(
                            session, settings, MemoryService(session)
                        ).run_for_user(job.user_id)
                    job.status = "sent"
                    job.sent_at = datetime.now(UTC)
                    job.attempts += 1
                    await heartbeat.record_event(
                        job, "internal", job.kind, trace_id=trace_id
                    )
                    sent += 1
                except Exception as exc:
                    job.status = "failed"
                    job.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                    job.attempts += 1
                    await heartbeat.record_event(
                        job, "failed", f"{job.kind}_error", trace_id=trace_id
                    )
                    failed += 1
                continue

            # Load user for soft gate
            from healthclaw.db.models import User

            user = await session.get(User, job.user_id)
            if user is None:
                job.status = "suppressed"
                await heartbeat.record_event(job, "suppressed", "user_not_found", trace_id=trace_id)
                suppressed += 1
                continue

            # Soft gate: LLM skip/run decision
            reflection = await heartbeat.should_send_soft(job, user, now)
            if not reflection.reach_out or reflection.when != "now":
                delay_minutes = parse_delay_minutes(reflection.when)
                if delay_minutes is not None:
                    job.due_at = now + timedelta(minutes=delay_minutes)
                    await heartbeat.record_event(
                        job,
                        "deferred",
                        reflection.rationale,
                        trace_id=trace_id,
                        decision_input=reflection.decision_input,
                        decision_model=reflection.model,
                        skip_reason=reflection.when,
                    )
                    deferred += 1
                else:
                    job.status = "suppressed"
                    await heartbeat.record_event(
                        job,
                        "suppressed",
                        reflection.rationale,
                        trace_id=trace_id,
                        decision_input=reflection.decision_input,
                        decision_model=reflection.model,
                        skip_reason=reflection.when,
                    )
                    soft_skipped += 1
                continue

            external_id = await proactivity.external_channel_id(job.user_id, job.channel)
            if job.channel != "telegram" or external_id is None:
                job.status = "failed"
                job.last_error = "channel_not_deliverable"
                job.attempts += 1
                await heartbeat.record_event(
                    job, "failed", "channel_not_deliverable", trace_id=trace_id
                )
                failed += 1
                continue

            bot_token: str | None = None
            if settings.multi_tenant_mode:
                bot_token = await accounts.get_bot_token_for_user(job.user_id)
                if bot_token is None:
                    job.status = "failed"
                    job.last_error = "bot_token_unavailable"
                    job.attempts += 1
                    await heartbeat.record_event(
                        job,
                        "failed",
                        "bot_token_unavailable",
                        trace_id=trace_id,
                        decision_input=reflection.decision_input,
                        decision_model=reflection.model,
                    )
                    failed += 1
                    continue
            try:
                message_text = reflection.message_seed or await heartbeat.render_job(job)
                await telegram.send_status(external_id, "typing", bot_token=bot_token)
                await telegram.send_message(external_id, message_text, bot_token=bot_token)
            except Exception:
                job.status = "failed"
                job.last_error = "send_error"
                job.attempts += 1
                await heartbeat.record_event(
                    job,
                    "failed",
                    "send_error",
                    trace_id=trace_id,
                    decision_input=reflection.decision_input,
                    decision_model=reflection.model,
                )
                failed += 1
            else:
                job.status = "sent"
                job.sent_at = datetime.now(UTC)
                job.attempts += 1
                await heartbeat.record_event(
                    job,
                    "sent",
                    reflection.rationale,
                    trace_id=trace_id,
                    decision_input=reflection.decision_input,
                    decision_model=reflection.model,
                )
                sent += 1

        await session.commit()
        result = {
            "ritual_jobs": ritual_jobs,
            "refresh_jobs": int(scheduled_work.get("refresh_jobs", 0)),
            "open_loop_jobs": int(scheduled_work.get("open_loop_jobs", 0)),
            "due": len(jobs),
            "sent": sent,
            "suppressed": suppressed,
            "deferred": deferred,
            "soft_skipped": soft_skipped,
            "failed": failed,
        }
        logger.info("Heartbeat sweep completed: %s", result)
        return result
