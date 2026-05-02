"""Motive layer — the agent's intrinsic drives on behalf of the user.

Motives reweight salience contributions (heat-stress signal × hydration.weight)
and shape what the inner synthesizer considers worth nudging about.

Six default motives are seeded on user creation at low weight (0.3).
The dream/consolidate jobs reinforce or add motives over time.
Weights decay on a configurable half-life unless reinforced.
"""
from __future__ import annotations

import logging
import math
from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from healthclaw.db.models import Motive, utc_now

logger = logging.getLogger(__name__)

# Default motives seeded for every new user
DEFAULT_MOTIVES = [
    ("hydration",           "Keep the user adequately hydrated, especially in heat."),
    ("sleep_protection",    "Protect sleep quality and circadian rhythm."),
    ("movement",            "Encourage regular physical activity."),
    ("mood_stability",      "Support emotional steadiness and notice distress early."),
    ("medication_adherence","Help the user stay on track with any medications or protocols."),
    ("social_connection",   "Notice when the user seems isolated and gently encourage connection."),
]

# Maps motive name → signal kinds it amplifies
MOTIVE_SIGNAL_MAP: dict[str, set[str]] = {
    "hydration":           {"weather_heat_stress", "weather_high_uv", "hydration_need"},
    "sleep_protection": {
        "wearable_poor_sleep", "wearable_low_recovery", "out_of_circadian_window"
    },
    "movement":            {"wearable_low_recovery", "movement_gap"},
    "mood_stability":      {"long_lapse", "calendar_imminent_event"},
    "medication_adherence":{"medication_due"},
    "social_connection":   {"long_lapse"},
}


class MotiveService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── CRUD ─────────────────────────────────────────────────────────────────

    async def get_active_motives(self, user_id: str) -> list[Motive]:
        result = await self.session.execute(
            select(Motive).where(
                Motive.user_id == user_id,
                Motive.is_active.is_(True),
            )
        )
        return list(result.scalars())

    async def get_motive(self, user_id: str, name: str) -> Motive | None:
        result = await self.session.execute(
            select(Motive).where(Motive.user_id == user_id, Motive.name == name)
        )
        return result.scalar_one_or_none()

    async def set_weight(self, user_id: str, name: str, weight: float) -> Motive | None:
        motive = await self.get_motive(user_id, name)
        if motive is None:
            return None
        motive.weight = max(0.0, min(1.0, weight))
        motive.source = "user_set"
        motive.updated_at = utc_now()
        await self.session.flush()
        return motive

    async def seed_defaults(self, user_id: str, seed_weight: float = 0.3) -> int:
        """Seed default motives for a new user. Skips any that already exist."""
        now = utc_now()
        seeded = 0
        for name, rationale in DEFAULT_MOTIVES:
            existing = await self.get_motive(user_id, name)
            if existing is not None:
                continue
            motive = Motive(
                user_id=user_id,
                name=name,
                weight=seed_weight,
                rationale=rationale,
                source="seeded",
                is_active=True,
                activated_at=now,
            )
            self.session.add(motive)
            seeded += 1
        if seeded:
            await self.session.flush()
        return seeded

    # ── Reinforcement ─────────────────────────────────────────────────────────

    async def reinforce(
        self,
        user_id: str,
        name: str,
        delta: float = 0.05,
        reason: str = "",
    ) -> None:
        """Boost a motive's weight (capped at 1.0) and update last_reinforced_at."""
        motive = await self.get_motive(user_id, name)
        if motive is None:
            return
        motive.weight = min(1.0, motive.weight + delta)
        motive.last_reinforced_at = utc_now()
        if reason:
            motive.rationale = reason[:2000]
        await self.session.flush()

    # ── Decay ────────────────────────────────────────────────────────────────

    async def apply_decay(self, user_id: str) -> int:
        """Apply exponential half-life decay to all active motives. Returns count updated."""
        now = utc_now()
        motives = await self.get_active_motives(user_id)
        updated = 0
        for motive in motives:
            last = motive.last_reinforced_at or motive.created_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            days_elapsed = (now - last.astimezone(UTC)).total_seconds() / 86400
            if days_elapsed < 1.0:
                continue
            half_life = motive.decay_half_life_days or 21
            decay_factor = math.pow(0.5, days_elapsed / half_life)
            new_weight = round(motive.weight * decay_factor, 4)
            motive.weight = new_weight
            motive.last_decayed_at = now
            updated += 1
        if updated:
            await self.session.flush()
        return updated

    # ── Serialisation ────────────────────────────────────────────────────────

    @staticmethod
    def to_dict(motive: Motive) -> dict[str, Any]:
        return {
            "id": motive.id,
            "name": motive.name,
            "weight": motive.weight,
            "rationale": motive.rationale,
            "source": motive.source,
            "is_active": motive.is_active,
        }


# ── Salience weighting helpers ────────────────────────────────────────────────


def motive_weight_for_signal(motives: list[Motive], signal_key: str) -> float:
    """Return the highest motive weight that amplifies *signal_key*.

    Falls back to 1.0 (no amplification) when no motive maps to that signal.
    This is a multiplier: contribution × max(1.0, weight * AMPLIFY_SCALE).
    """
    best = 1.0
    for motive in motives:
        if signal_key in MOTIVE_SIGNAL_MAP.get(motive.name, set()):
            # Scale from [0,1] weight to [1, 2.0] amplifier
            best = max(best, 1.0 + motive.weight)
    return best


def motives_as_dict_list(motives: list[Motive]) -> list[dict[str, Any]]:
    return [MotiveService.to_dict(m) for m in motives]
