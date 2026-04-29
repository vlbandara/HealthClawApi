from __future__ import annotations

import re
from dataclasses import dataclass

WAKE_DIRECTIVE_RE = re.compile(r"^wake\s*:\s*(.*)$", flags=re.IGNORECASE)
ALLOW_LONG_SILENCE_RE = re.compile(
    r"^allow(?:[_ -]?long(?:[_ -]?silence)?)\s*:\s*(true|false|yes|no|1|0)$",
    flags=re.IGNORECASE,
)


@dataclass(slots=True)
class HeartbeatProfile:
    standing_intent: str = ""
    wake_text: str | None = None
    allow_long_silence: bool | None = None
    has_wake_directive: bool = False
    has_allow_long_silence_directive: bool = False


def parse_heartbeat_md(raw_text: str | None) -> HeartbeatProfile:
    lines = (raw_text or "").replace("\r\n", "\n").split("\n")
    standing_lines: list[str] = []
    wake_text: str | None = None
    allow_long_silence: bool | None = None
    has_wake_directive = False
    has_allow_directive = False

    for raw_line in lines:
        stripped = raw_line.strip()
        wake_match = WAKE_DIRECTIVE_RE.match(stripped)
        if wake_match is not None:
            has_wake_directive = True
            value = wake_match.group(1).strip()
            wake_text = value or None
            continue

        allow_match = ALLOW_LONG_SILENCE_RE.match(stripped)
        if allow_match is not None:
            has_allow_directive = True
            allow_long_silence = allow_match.group(1).lower() in {"true", "yes", "1"}
            continue

        standing_lines.append(stripped)

    standing_intent = _normalize_standing_lines(standing_lines)
    return HeartbeatProfile(
        standing_intent=standing_intent,
        wake_text=wake_text,
        allow_long_silence=allow_long_silence,
        has_wake_directive=has_wake_directive,
        has_allow_long_silence_directive=has_allow_directive,
    )


def format_heartbeat_md(profile: HeartbeatProfile) -> str:
    blocks: list[str] = []
    if profile.standing_intent:
        blocks.append(profile.standing_intent[:4000].strip())

    directives: list[str] = []
    if profile.wake_text:
        directives.append(f"wake: {profile.wake_text.strip()[:240]}")
    if profile.allow_long_silence is not None:
        directives.append(
            f"allow_long_silence: {'true' if profile.allow_long_silence else 'false'}"
        )
    if directives:
        blocks.append("\n".join(directives))
    return "\n\n".join(block for block in blocks if block).strip()[:4000]


def canonicalize_heartbeat_md(raw_text: str | None) -> str:
    return format_heartbeat_md(parse_heartbeat_md(raw_text))


def merge_dream_heartbeat_md(existing_raw: str | None, proposed_raw: str | None) -> str:
    existing = parse_heartbeat_md(existing_raw)
    proposed = parse_heartbeat_md(proposed_raw)
    merged = HeartbeatProfile(
        standing_intent=proposed.standing_intent or existing.standing_intent,
        wake_text=existing.wake_text,
        allow_long_silence=existing.allow_long_silence,
        has_wake_directive=existing.has_wake_directive,
        has_allow_long_silence_directive=existing.has_allow_long_silence_directive,
    )

    if proposed.has_wake_directive:
        merged.wake_text = proposed.wake_text
        merged.has_wake_directive = True
    if proposed.has_allow_long_silence_directive:
        merged.allow_long_silence = proposed.allow_long_silence
        merged.has_allow_long_silence_directive = True

    return format_heartbeat_md(merged)


def _normalize_standing_lines(lines: list[str]) -> str:
    normalized: list[str] = []
    blank_pending = False
    for line in lines:
        if not line:
            if normalized:
                blank_pending = True
            continue
        if blank_pending and normalized:
            normalized.append("")
        normalized.append(line)
        blank_pending = False
    return "\n".join(normalized).strip()
