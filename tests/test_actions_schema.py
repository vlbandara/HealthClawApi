from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from healthclaw.schemas.actions import Action


def test_action_schema_accepts_variants() -> None:
    adapter = TypeAdapter(Action)
    reminder = adapter.validate_python(
        {
            "type": "create_reminder",
            "text": "drink water",
            "due_at_iso": "2026-04-28T20:00:00+05:30",
        }
    )
    open_loop = adapter.validate_python(
        {"type": "create_open_loop", "title": "go for a walk", "kind": "commitment"}
    )
    close_loop = adapter.validate_python(
        {"type": "close_open_loop", "id": "loop-1", "summary": "done"}
    )
    none_action = adapter.validate_python({"type": "none"})

    assert reminder.type == "create_reminder"
    assert open_loop.type == "create_open_loop"
    assert close_loop.type == "close_open_loop"
    assert none_action.type == "none"


def test_action_schema_rejects_invalid_payloads() -> None:
    adapter = TypeAdapter(Action)
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "bad_type"})
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "create_reminder", "text": "missing due date"})
