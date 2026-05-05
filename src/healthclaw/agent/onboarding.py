from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from healthclaw.engagement.metrics import is_meaningful_exchange

ACTIVE_ONBOARDING_DAY_LIMIT = 3
ACTIVE_ONBOARDING_TURN_LIMIT = 5
ONBOARDING_ASK_COOLDOWN_TURNS = 2
ACKNOWLEDGEMENT_MESSAGES = {
    "ok",
    "okay",
    "thanks",
    "thank you",
    "sure",
    "yep",
    "yes",
    "no",
    "nah",
    "cool",
    "nice",
}
DIRECT_UTILITY_HINTS = (
    "what time",
    "what day",
    "what do you remember",
    "what do you know about me",
    "remember about me",
    "what is my timezone",
    "what's my timezone",
)


@dataclass(frozen=True)
class OnboardingContext:
    status: str
    missing_fields: list[str]
    meaningful_user_turns: int
    days_since_signup: int
    recently_asked: bool
    current_turn_has_space: bool
    first_contact: bool = False

    @property
    def should_prompt(self) -> bool:
        return self.status == "active" and self.current_turn_has_space and not self.recently_asked

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["should_prompt"] = self.should_prompt
        return payload


def build_onboarding_context(
    *,
    user: Any,
    memories: list[dict[str, Any]],
    recent_messages: list[Any],
    current_content: str,
    current_content_type: str,
    is_command: bool,
    now: datetime | None = None,
) -> OnboardingContext:
    now = _ensure_utc(now or datetime.now(UTC))
    first_contact = str(getattr(user, "onboarding_status", "new") or "new") == "new"
    meaningful_user_turns = _meaningful_user_turns(recent_messages)
    created_at = getattr(user, "created_at", None) or now
    days_since_signup = max(0, (now - _ensure_utc(created_at)).days)
    missing_fields = _missing_fields(user, memories)
    complete = not missing_fields
    within_window = (
        meaningful_user_turns < ACTIVE_ONBOARDING_TURN_LIMIT
        and days_since_signup < ACTIVE_ONBOARDING_DAY_LIMIT
    )
    if complete:
        status = "complete"
    elif within_window:
        status = "active"
    elif first_contact:
        status = "new"
    else:
        status = "passive"
    recently_asked = _recent_onboarding_ask(recent_messages)
    current_turn_has_space = _current_turn_has_space(
        current_content=current_content,
        current_content_type=current_content_type,
        is_command=is_command,
    )
    return OnboardingContext(
        status=status,
        missing_fields=missing_fields,
        meaningful_user_turns=meaningful_user_turns,
        days_since_signup=days_since_signup,
        recently_asked=recently_asked,
        current_turn_has_space=current_turn_has_space,
        first_contact=first_contact,
    )


def onboarding_status_after_turn(
    *,
    user: Any,
    memories: list[dict[str, Any]],
    recent_messages: list[Any],
    now: datetime | None = None,
) -> str:
    context = build_onboarding_context(
        user=user,
        memories=memories,
        recent_messages=recent_messages,
        current_content="",
        current_content_type="text",
        is_command=False,
        now=now,
    )
    if not context.missing_fields:
        return "complete"
    if context.meaningful_user_turns >= ACTIVE_ONBOARDING_TURN_LIMIT or (
        context.days_since_signup >= ACTIVE_ONBOARDING_DAY_LIMIT
    ):
        return "passive"
    if str(getattr(user, "onboarding_status", "new") or "new") == "new":
        return "active"
    return "active"


def quiet_hours_confirmed(memories: list[dict[str, Any]]) -> bool:
    for memory in memories:
        kind = str(memory.get("kind") or "")
        key = str(memory.get("key") or "")
        metadata = memory.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        if kind != "profile":
            continue
        if key == "quiet_hours_preference":
            return True
        if metadata_dict.get("onboarding_field") == "quiet_hours":
            return True
    return False


def support_focus_confirmed(memories: list[dict[str, Any]]) -> bool:
    for memory in memories:
        kind = str(memory.get("kind") or "")
        if kind == "goal":
            return True
        key = str(memory.get("key") or "")
        metadata = memory.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        if kind == "profile" and (
            key in {"support_focus", "current_focus"}
            or metadata_dict.get("onboarding_field") == "support_focus"
        ):
            return True
    return False


def _missing_fields(user: Any, memories: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    timezone_confidence = float(getattr(user, "timezone_confidence", 0.0) or 0.0)
    if timezone_confidence < 0.6:
        missing.append("timezone")
    if not quiet_hours_confirmed(memories):
        missing.append("quiet_hours")
    if not support_focus_confirmed(memories):
        missing.append("support_focus")
    return missing


def _meaningful_user_turns(messages: list[Any]) -> int:
    count = 0
    for message in messages:
        if str(getattr(message, "role", "")) != "user":
            continue
        if _is_meaningful_message(message):
            count += 1
    return count


def _recent_onboarding_ask(messages: list[Any]) -> bool:
    meaningful_user_turns_since_ask = 0
    for message in reversed(messages):
        role = str(getattr(message, "role", ""))
        if role == "assistant":
            metadata = getattr(message, "metadata_", None)
            if not isinstance(metadata, dict):
                continue
            generation = metadata.get("generation")
            generation_dict = generation if isinstance(generation, dict) else {}
            onboarding = generation_dict.get("onboarding")
            onboarding_dict = onboarding if isinstance(onboarding, dict) else {}
            if onboarding_dict.get("asked") is True:
                return meaningful_user_turns_since_ask < ONBOARDING_ASK_COOLDOWN_TURNS
        elif role == "user" and _is_meaningful_message(message):
            meaningful_user_turns_since_ask += 1
    return False


def _current_turn_has_space(
    *,
    current_content: str,
    current_content_type: str,
    is_command: bool,
) -> bool:
    if is_command:
        return False
    stripped = current_content.strip()
    if not stripped:
        return False
    if current_content_type != "voice_transcript":
        compact = " ".join(stripped.lower().split())
        if compact in ACKNOWLEDGEMENT_MESSAGES:
            return False
        if len(compact.split()) <= 3:
            return False
        if any(hint in compact for hint in DIRECT_UTILITY_HINTS):
            return False
    return True


def _is_meaningful_message(message: Any) -> bool:
    metadata = getattr(message, "metadata_", None)
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    return is_meaningful_exchange(
        str(getattr(message, "content", "") or ""),
        content_type=str(metadata_dict.get("content_type") or "text"),
        is_command=bool(
            metadata_dict.get("command")
            or str(getattr(message, "content", "")).startswith("/")
        ),
    )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
