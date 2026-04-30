from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from healthclaw.schemas.actions import Action


def test_action_schema_accepts_variants() -> None:
    adapter = TypeAdapter(Action)
    reminder = adapter.validate_python(
        {
            "type": "create_reminder",
            "payload": {
                "text": "drink water",
                "due_at_iso": "2026-04-28T20:00:00+05:30",
            },
            "rationale": "user asked directly",
        }
    )
    open_loop = adapter.validate_python(
        {
            "type": "create_open_loop",
            "payload": {"title": "go for a walk", "kind": "commitment"},
        }
    )
    close_loop = adapter.validate_python(
        {
            "type": "close_open_loop",
            "payload": {
                "id": "loop-1",
                "summary": "done",
                "outcome": "completed",
            },
        }
    )
    none_action = adapter.validate_python({"type": "none", "payload": {}})
    novel_action = adapter.validate_python(
        {
            "type": "schedule_evening_reflection",
            "payload": {"time": "20:00", "channel": "telegram"},
            "rationale": "fits the user's stated routine",
        }
    )

    assert reminder.type == "create_reminder"
    assert reminder.payload["text"] == "drink water"
    assert open_loop.type == "create_open_loop"
    assert close_loop.type == "close_open_loop"
    assert none_action.type == "none"
    assert novel_action.type == "schedule_evening_reflection"


def test_action_schema_rejects_invalid_payloads() -> None:
    adapter = TypeAdapter(Action)
    with pytest.raises(ValidationError):
        adapter.validate_python({})
    with pytest.raises(ValidationError):
        adapter.validate_python({"payload": {"text": "missing type"}})
