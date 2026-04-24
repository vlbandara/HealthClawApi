from __future__ import annotations

from datetime import UTC, datetime

from healthclaw.agent.time_context import TimeContext
from healthclaw.db.models import ConversationThread, Message, User


def make_user(user_id: str = "u-test", **overrides) -> User:
    values = {
        "id": user_id,
        "timezone": "Asia/Colombo",
        "quiet_start": "22:00",
        "quiet_end": "07:00",
    }
    values.update(overrides)
    return User(**values)


def make_thread(
    user_id: str = "u-test",
    thread_id: str = "thread-test",
    **overrides,
) -> ConversationThread:
    values = {
        "id": thread_id,
        "user_id": user_id,
        "channel": "web",
        "is_primary": True,
        "summary": "",
        "open_loop_count": 0,
    }
    values.update(overrides)
    return ConversationThread(**values)


def make_message(
    *,
    message_id: str,
    user_id: str = "u-test",
    thread_id: str = "thread-test",
    role: str = "user",
    content: str = "Message",
    **overrides,
) -> Message:
    values = {
        "id": message_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "role": role,
        "content": content,
        "channel": "web",
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    return Message(**values)


def make_time_context(**overrides) -> TimeContext:
    values = {
        "local_datetime": "2026-04-21T08:00:00+05:30",
        "local_date": "2026-04-21",
        "weekday": "Tuesday",
        "part_of_day": "morning",
        "quiet_hours": False,
        "interaction_gap_days": None,
        "long_lapse": False,
    }
    values.update(overrides)
    return TimeContext(**values)
