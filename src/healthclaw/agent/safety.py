from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyDecision:
    category: str
    severity: str
    action: str


CRISIS_TERMS = (
    "kill myself",
    "end my life",
    "suicide",
    "self harm",
    "hurt myself",
    "do not want to live",
)

MEDICAL_TERMS = (
    "diagnose",
    "medication",
    "dose",
    "chest pain",
    "can't breathe",
    "cant breathe",
    "fainted",
    "blood pressure",
    "injury",
    "treatment",
)


def classify_safety(content: str) -> SafetyDecision:
    lowered = content.lower()
    if any(term in lowered for term in CRISIS_TERMS):
        return SafetyDecision(category="crisis", severity="high", action="escalate")
    if any(term in lowered for term in MEDICAL_TERMS):
        return SafetyDecision(
            category="medical_boundary", severity="medium", action="boundaried_support"
        )
    return SafetyDecision(category="wellness", severity="low", action="support")
