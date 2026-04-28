from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from healthclaw.agent.time_context import TimeContext
from healthclaw.core.config import Settings
from healthclaw.core.tracing import start_span
from healthclaw.db.models import HeartbeatJob, OpenLoop, User

logger = logging.getLogger(__name__)

DECISION_SYSTEM_PROMPT = """\
You are the autonomous wake gate for a private health companion.
A proactive message is being considered. Decide: should the companion reach out right now?

Return ONLY valid JSON with exactly these fields:
  "decision": "run" or "skip"
  "action": a single sentence describing what to say (or null if skip)
  "reason": 8-20 words explaining your decision

Rules:
- Prefer skip unless a heartbeat intent or open loop genuinely motivates outreach NOW.
- If quiet_hours is true, always return skip.
- If the user has been silent for less than 6 hours, prefer skip unless a ritual is due.
- If there are no open loops and no heartbeat intents, return skip.
- Keep the action message warm, grounded, and specific to the context."""


@dataclass
class DecisionResult:
    decision: str  # "run" | "skip"
    action: str | None
    reason: str
    model: str
    decision_input: dict


async def decide(
    job: HeartbeatJob,
    user: User,
    time_context: TimeContext,
    open_loops: list[OpenLoop],
    recent_exchanges: list[dict],
    settings: Settings,
    relationship: dict | None = None,
) -> DecisionResult:
    """LLM soft gate: returns (run, action) or (skip, None). Never raises."""
    from healthclaw.integrations.openrouter import OpenRouterClient

    decision_input = build_decision_input(
        job,
        user,
        time_context,
        open_loops,
        recent_exchanges,
        relationship=relationship,
    )

    # Python short-circuit: skip LLM if there's clearly nothing to do
    if time_context.quiet_hours:
        return DecisionResult(
            decision="skip",
            action=None,
            reason="quiet hours active",
            model="",
            decision_input=decision_input,
        )

    has_open_loops = bool(open_loops)
    has_heartbeat_intents = bool(user.heartbeat_md.strip())
    # Job kinds that carry their own specific trigger (payload has the context)
    is_specific_trigger = job.kind in {"ritual", "open_loop_followup", "memory_refresh"}
    recent_gap = time_context.interaction_gap_days or 0

    if (
        not has_open_loops
        and not has_heartbeat_intents
        and not is_specific_trigger
        and recent_gap < 1
    ):
        return DecisionResult(
            decision="skip",
            action=None,
            reason="no triggers and user active recently",
            model="",
            decision_input=decision_input,
        )

    # Build context for the LLM
    user_prompt = (
        f"<time_context>{json.dumps(decision_input['time_context'])}</time_context>\n"
        f"<heartbeat_md>{decision_input['heartbeat_md']}</heartbeat_md>\n"
        f"<relationship>{json.dumps(decision_input['relationship'], default=str)}</relationship>\n"
        f"<open_loops>{json.dumps(decision_input['open_loops'])}</open_loops>\n"
        f"<recent_exchanges>{json.dumps(decision_input['recent_exchanges'])}</recent_exchanges>\n"
        f"<candidate_trigger>{json.dumps(decision_input['candidate_trigger'])}</candidate_trigger>"
    )

    client = OpenRouterClient(settings)
    if not client.enabled:
        # LLM unavailable — trust the hard gate that already ran; default to run
        return DecisionResult(
            decision="run",
            action=None,
            reason="llm_unavailable_hard_gate_passed",
            model="",
            decision_input=decision_input,
        )

    try:
        async with start_span(
                "openrouter.chat",
                attributes={
                    "model_role": "decision",
                    "user_id": user.id,
                    "job_kind": job.kind,
                },
            ):
                result = await client.chat_completion(
                    messages=[
                        {"role": "system", "content": DECISION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=200,
                    temperature=0.0,
                    model=settings.openrouter_decision_model,
                    metadata={
                        "model_role": "decision",
                        "user_id": user.id,
                        "job_kind": job.kind,
                    },
                )
        raw = result.content.strip()
        # Strip markdown fences
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:].strip()
        parsed = json.loads(raw)
        return DecisionResult(
            decision=parsed.get("decision", "skip"),
            action=parsed.get("action"),
            reason=str(parsed.get("reason", ""))[:128],
            model=result.model,
            decision_input=decision_input,
        )
    except Exception as exc:
        logger.warning("Decision gate failed for job %s: %s", job.id, exc)
        return DecisionResult(
            decision="skip",
            action=None,
            reason="decision_error",
            model="",
            decision_input=decision_input,
        )


def build_decision_input(
    job: HeartbeatJob,
    user: User,
    time_context: TimeContext,
    open_loops: list[OpenLoop],
    recent_exchanges: list[dict],
    *,
    relationship: dict | None = None,
) -> dict:
    relationship_payload = dict(
        relationship
        or {
            "sentiment_ema": 0.0,
            "voice_text_ratio": 0.0,
            "reply_latency_seconds_ema": None,
            "last_meaningful_exchange_at": None,
            "bands": {
                "low_pressure": False,
                "voice_heavy": False,
                "slow_reentry": False,
                "continuity_fresh": False,
            },
        }
    )
    last_meaningful_exchange_at = relationship_payload.get("last_meaningful_exchange_at")
    if hasattr(last_meaningful_exchange_at, "isoformat"):
        relationship_payload["last_meaningful_exchange_at"] = (
            last_meaningful_exchange_at.isoformat()
        )
    return {
        "time_context": {
            "part_of_day": time_context.part_of_day,
            "quiet_hours": time_context.quiet_hours,
            "interaction_gap_days": time_context.interaction_gap_days,
            "long_lapse": time_context.long_lapse,
        },
        "heartbeat_md": user.heartbeat_md[:800],
        "relationship": relationship_payload,
        "open_loops": [
            {
                "title": loop.title,
                "kind": loop.kind,
                "age_hours": _age_hours(loop),
            }
            for loop in open_loops[:5]
        ],
        "recent_exchanges": recent_exchanges[-3:],
        "candidate_trigger": {
            "kind": job.kind,
            "details": job.payload,
        },
    }


def _age_hours(loop: OpenLoop) -> float:
    from datetime import UTC, datetime

    if loop.created_at is None:
        return 0.0
    created = loop.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (datetime.now(UTC) - created).total_seconds() / 3600
