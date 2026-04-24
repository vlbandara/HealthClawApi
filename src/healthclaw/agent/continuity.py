from __future__ import annotations

from healthclaw.agent.time_context import TimeContext

MemoryLike = dict[str, object]
OpenLoopLike = dict[str, object]

MAX_BRIDGES = 2


def build_bridges(
    time_context: TimeContext,
    memories: list[MemoryLike],
    open_loops: list[OpenLoopLike],
    *,
    safety_category: str = "wellness",
    recently_surfaced_loop_ids: set[str] | None = None,
) -> list[str]:
    """Return 0-2 seed sentences that help the LLM pick up from prior context.

    These are injected into the user-turn context, not the system prompt, so the
    LLM can weave them in naturally or ignore them. Never emit for crisis/medical
    safety categories. Deterministic — no LLM call.
    """
    if safety_category in {"crisis", "medical_boundary"}:
        return []

    recently_surfaced = recently_surfaced_loop_ids or set()
    bridges: list[str] = []

    # Bridge 1 — long lapse reopener
    if time_context.long_lapse and time_context.interaction_gap_days is not None:
        n = time_context.interaction_gap_days
        bridges.append(
            f"It's been {n} day{'s' if n != 1 else ''} since we last spoke — "
            "no need to recap, just pick up wherever feels natural."
        )

    # Bridge 3 — stale open loop surfacing (pilot: rules 1 and 3 only)
    if len(bridges) < MAX_BRIDGES:
        loop = _pick_stale_loop(open_loops, recently_surfaced)
        if loop is not None:
            title = str(loop.get("title") or "something you mentioned")
            bridges.append(
                f"By the way — you mentioned \"{title}\" a while back. "
                "Did that end up happening, or is it still on your list?"
            )

    return bridges[:MAX_BRIDGES]


def _pick_stale_loop(
    open_loops: list[OpenLoopLike],
    recently_surfaced: set[str],
) -> OpenLoopLike | None:
    """Return the most eligible open loop to surface, or None."""
    AGE_THRESHOLD_HOURS = 18

    for loop in open_loops:
        loop_id = str(loop.get("id") or "")
        if loop_id in recently_surfaced:
            continue
        if loop.get("status") != "open":
            continue
        age_hours = float(loop.get("age_hours") or 0.0)
        if age_hours < AGE_THRESHOLD_HOURS:
            continue
        return loop
    return None
