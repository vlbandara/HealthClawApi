from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from healthclaw.agent.time_context import TimeContext
from healthclaw.agent.wellbeing import build_wellbeing_input, reflect_on_wellbeing
from healthclaw.core.config import Settings
from healthclaw.db.models import HeartbeatJob, OpenLoop, User


@dataclass
class DecisionResult:
    decision: str  # "run" | "skip"
    action: str | None
    reason: str
    model: str
    decision_input: dict
    when: str


async def decide(
    job: HeartbeatJob,
    user: User,
    time_context: TimeContext,
    open_loops: list[OpenLoop],
    recent_exchanges: list[dict],
    settings: Settings,
    relationship: dict | None = None,
    *,
    outbound_count_24h: int = 0,
    last_outbound_at: datetime | None = None,
    daily_cap: int = 0,
) -> DecisionResult:
    decision_input = build_decision_input(
        job,
        user,
        time_context,
        open_loops,
        recent_exchanges,
        relationship=relationship,
        outbound_count_24h=outbound_count_24h,
        last_outbound_at=last_outbound_at,
        daily_cap=daily_cap,
    )
    reflection = await reflect_on_wellbeing(
        settings=settings,
        user_id=user.id,
        decision_input=decision_input,
        metadata={"job_kind": job.kind, "channel": job.channel},
    )
    return DecisionResult(
        decision="run" if reflection.reach_out and reflection.when == "now" else "skip",
        action=reflection.message_seed or None,
        reason=reflection.rationale,
        model=reflection.model or "",
        decision_input=decision_input,
        when=reflection.when,
    )


def build_decision_input(
    job: HeartbeatJob,
    user: User,
    time_context: TimeContext,
    open_loops: list[OpenLoop],
    recent_exchanges: list[dict],
    *,
    relationship: dict | None = None,
    outbound_count_24h: int = 0,
    last_outbound_at: datetime | None = None,
    daily_cap: int = 0,
) -> dict:
    return build_wellbeing_input(
        user_id=user.id,
        source_kind="heartbeat_job",
        timezone=user.timezone,
        quiet_start=user.quiet_start,
        quiet_end=user.quiet_end,
        time_context=time_context.to_dict(),
        heartbeat_md=user.heartbeat_md,
        relationship=relationship,
        open_loops=[
            {
                "id": loop.id,
                "title": loop.title,
                "kind": loop.kind,
                "age_hours": _age_hours(loop),
            }
            for loop in open_loops[:5]
        ],
        recent_exchanges=recent_exchanges[-3:],
        candidate={
            "kind": job.kind,
            "channel": job.channel,
            "details": job.payload,
        },
        last_active_at=user.last_active_at,
        proactive_paused_until=user.proactive_paused_until,
        proactive_enabled=user.proactive_enabled,
        outbound_count_24h=outbound_count_24h,
        last_outbound_at=last_outbound_at,
        daily_cap=daily_cap,
        monthly_llm_tokens_used=user.monthly_llm_tokens_used,
        monthly_llm_token_budget=user.monthly_llm_token_budget,
    )


def _age_hours(loop: OpenLoop) -> float:
    from datetime import UTC, datetime

    if loop.created_at is None:
        return 0.0
    created = loop.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (datetime.now(UTC) - created).total_seconds() / 3600
