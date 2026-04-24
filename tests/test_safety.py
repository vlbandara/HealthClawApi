from __future__ import annotations

from healthclaw.agent.safety import classify_safety


def test_crisis_classification() -> None:
    decision = classify_safety("I want to kill myself")
    assert decision.category == "crisis"
    assert decision.action == "escalate"


def test_medical_boundary_classification() -> None:
    decision = classify_safety("Should I change my medication dose?")
    assert decision.category == "medical_boundary"


def test_wellness_classification() -> None:
    decision = classify_safety("I want to sleep earlier tonight")
    assert decision.category == "wellness"
