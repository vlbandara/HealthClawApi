from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from fastapi.encoders import jsonable_encoder
from opentelemetry import trace

from healthclaw.core.observability import redact_text

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

_tracer: trace.Tracer | None = None


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


def _get_tracer() -> trace.Tracer:
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer("healthclaw")
    return _tracer


@asynccontextmanager
async def start_span(
    name: str,
    attributes: dict[str, Any] | None = None,
):
    tracer = _get_tracer()
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                if value is not None:
                    span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            raise


@contextlib.contextmanager
def start_span_sync(
    name: str,
    attributes: dict[str, Any] | None = None,
):
    tracer = _get_tracer()
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                if value is not None:
                    span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            raise


def traced_node(name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            tracer = _get_tracer()
            with tracer.start_as_current_span(f"agent.{name}") as span:
                state: dict[str, Any] | None = None
                for arg in args:
                    if isinstance(arg, dict) and "user" in arg:
                        state = arg
                        break
                if state:
                    user = state.get("user", {})
                    if isinstance(user, dict):
                        span.set_attribute("user_id", str(user.get("id", "unknown")))
                    trace_meta = state.get("trace_metadata", {})
                    if isinstance(trace_meta, dict):
                        trace_id = trace_meta.get("trace_id")
                        if trace_id:
                            span.set_attribute("trace_id", str(trace_id))
                span.set_attribute("node", name)
                try:
                    result = await fn(*args, **kwargs)
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    raise

        return wrapper
    return decorator