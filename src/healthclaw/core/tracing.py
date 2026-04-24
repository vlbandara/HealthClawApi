from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from fastapi.encoders import jsonable_encoder

from healthclaw.core.observability import redact_text


def new_trace_id() -> str:
    return uuid.uuid4().hex


def redacted_payload(
    payload: Mapping[str, Any],
    *,
    include_raw_content: bool = False,
) -> dict[str, Any]:
    return {
        str(key): _redacted_value(value, include_raw_content=include_raw_content)
        for key, value in payload.items()
    }


def _redacted_value(value: Any, *, include_raw_content: bool) -> Any:
    if isinstance(value, str):
        return value if include_raw_content else redact_text(value)
    if isinstance(value, Mapping):
        return redacted_payload(value, include_raw_content=include_raw_content)
    if isinstance(value, list):
        return [
            _redacted_value(item, include_raw_content=include_raw_content)
            for item in value
        ]
    return jsonable_encoder(value)
