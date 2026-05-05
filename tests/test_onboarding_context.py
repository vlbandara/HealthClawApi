from __future__ import annotations

from datetime import UTC, datetime, timedelta

from healthclaw.agent.onboarding import (
    build_onboarding_context,
    onboarding_status_after_turn,
)
from tests.factories import make_message, make_user


def _meaningful_user_message(
    message_id: str,
    content: str = "I want help sleeping better tonight.",
):
    return make_message(
        message_id=message_id,
        role="user",
        content=content,
        metadata_={"content_type": "text"},
    )


def test_onboarding_context_respects_recent_ask_cooldown() -> None:
    user = make_user(
        onboarding_status="active",
        timezone_confidence=0.0,
        created_at=datetime.now(UTC),
    )
    messages = [
        make_message(
            message_id="a1",
            role="assistant",
            content="What time zone should I use for you?",
            metadata_={"generation": {"onboarding": {"asked": True}}},
        ),
        _meaningful_user_message("u1"),
    ]

    context = build_onboarding_context(
        user=user,
        memories=[],
        recent_messages=messages,
        current_content="doing okay",
        current_content_type="text",
        is_command=False,
        now=datetime.now(UTC),
    )

    assert context.status == "active"
    assert context.recently_asked is True
    assert context.should_prompt is False


def test_onboarding_status_becomes_complete_once_essentials_are_captured() -> None:
    user = make_user(
        onboarding_status="active",
        timezone_confidence=0.95,
        created_at=datetime.now(UTC),
    )
    memories = [
        {
            "kind": "profile",
            "key": "quiet_hours_preference",
            "value": {"quiet_start": "23:00", "quiet_end": "07:00"},
            "metadata": {"onboarding_field": "quiet_hours"},
        },
        {
            "kind": "goal",
            "key": "current_goal",
            "value": {"text": "sleep by 11pm"},
            "metadata": {},
        },
    ]

    status = onboarding_status_after_turn(
        user=user,
        memories=memories,
        recent_messages=[_meaningful_user_message("u1")],
        now=datetime.now(UTC),
    )

    assert status == "complete"


def test_onboarding_status_becomes_passive_after_early_window() -> None:
    user = make_user(
        onboarding_status="active",
        timezone_confidence=0.0,
        created_at=datetime.now(UTC) - timedelta(days=4),
    )
    recent_messages = [
        _meaningful_user_message("u1", "I feel off today and I skipped breakfast."),
        _meaningful_user_message("u2", "Work has been heavy and sleep is rough this week."),
        _meaningful_user_message("u3", "I want to get back to a steady routine soon."),
        _meaningful_user_message("u4", "I keep waking up too late and scrambling."),
        _meaningful_user_message("u5", "I am trying to fix the basics first."),
    ]

    status = onboarding_status_after_turn(
        user=user,
        memories=[],
        recent_messages=recent_messages,
        now=datetime.now(UTC),
    )

    assert status == "passive"


def test_preferred_name_does_not_complete_support_focus() -> None:
    user = make_user(
        onboarding_status="active",
        timezone_confidence=0.95,
        created_at=datetime.now(UTC),
    )
    context = build_onboarding_context(
        user=user,
        memories=[
            {
                "kind": "profile",
                "key": "preferred_name",
                "value": {"text": "Vinodh"},
                "metadata": {},
            },
            {
                "kind": "profile",
                "key": "quiet_hours_preference",
                "value": {"quiet_start": "23:00", "quiet_end": "07:00"},
                "metadata": {"onboarding_field": "quiet_hours"},
            },
        ],
        recent_messages=[],
        current_content="hey there",
        current_content_type="text",
        is_command=False,
        now=datetime.now(UTC),
    )

    assert "support_focus" in context.missing_fields
    assert context.status == "active"


def test_onboarding_context_skips_short_direct_utility_turns() -> None:
    user = make_user(
        onboarding_status="active",
        timezone_confidence=0.0,
        created_at=datetime.now(UTC),
    )

    utility_context = build_onboarding_context(
        user=user,
        memories=[],
        recent_messages=[],
        current_content="what time is it?",
        current_content_type="text",
        is_command=False,
        now=datetime.now(UTC),
    )
    short_reply_context = build_onboarding_context(
        user=user,
        memories=[],
        recent_messages=[],
        current_content="okay",
        current_content_type="text",
        is_command=False,
        now=datetime.now(UTC),
    )

    assert utility_context.should_prompt is False
    assert short_reply_context.should_prompt is False
