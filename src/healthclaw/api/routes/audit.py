from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from healthclaw.api.deps import SessionDep
from healthclaw.core.security import require_api_key
from healthclaw.db.models import MemoryRevision, PolicyProposal, TraceRef

router = APIRouter(prefix="/v1/audit", tags=["audit"], dependencies=[Depends(require_api_key)])


@router.get("/memory-events")
async def memory_events(session: SessionDep, limit: int = 100) -> dict[str, list[dict]]:
    result = await session.execute(
        select(MemoryRevision).order_by(MemoryRevision.created_at.desc()).limit(min(limit, 500))
    )
    revisions = result.scalars()
    return {
        "events": [
            {
                "id": revision.id,
                "memory_id": revision.memory_id,
                "previous_value": revision.previous_value,
                "new_value": revision.new_value,
                "reason": revision.reason,
                "confidence": revision.confidence,
                "source_message_ids": revision.source_message_ids,
                "created_at": revision.created_at.isoformat(),
            }
            for revision in revisions
        ]
    }


@router.get("/policy-proposals")
async def policy_proposals(session: SessionDep, limit: int = 100) -> dict[str, list[dict]]:
    result = await session.execute(
        select(PolicyProposal).order_by(PolicyProposal.created_at.desc()).limit(min(limit, 500))
    )
    proposals = result.scalars()
    return {
        "events": [
            {
                "id": proposal.id,
                "user_id": proposal.user_id,
                "key": proposal.key,
                "proposed_value": proposal.proposed_value,
                "reason": proposal.reason,
                "status": proposal.status,
                "trace_id": proposal.trace_id,
                "created_at": proposal.created_at.isoformat(),
            }
            for proposal in proposals
        ]
    }


@router.patch("/policy-proposals/{proposal_id}")
async def update_policy_proposal(
    proposal_id: str, status: str, session: SessionDep
) -> dict[str, str]:
    if status not in {"approved", "rejected"}:
        return {"status": "invalid"}
    proposal = await session.get(PolicyProposal, proposal_id)
    if proposal is None:
        return {"status": "not_found"}
    proposal.status = status
    await session.commit()
    return {"status": proposal.status}


@router.get("/traces")
async def trace_refs(session: SessionDep, limit: int = 100) -> dict[str, list[dict]]:
    result = await session.execute(
        select(TraceRef).order_by(TraceRef.created_at.desc()).limit(min(limit, 500))
    )
    traces = result.scalars()
    return {
        "events": [
            {
                "id": trace.id,
                "user_id": trace.user_id,
                "message_id": trace.message_id,
                "provider": trace.provider,
                "trace_id": trace.trace_id,
                "redacted": trace.redacted,
                "created_at": trace.created_at.isoformat(),
            }
            for trace in traces
        ]
    }
