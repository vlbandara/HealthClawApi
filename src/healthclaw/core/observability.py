from __future__ import annotations

import os
import re

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from healthclaw.core.config import Settings

SENSITIVE_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    re.compile(r"\b(?:\+?\d[\d .-]{7,}\d)\b"),
]


def redact_text(value: str) -> str:
    redacted = value
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def configure_observability(app: FastAPI, settings: Settings) -> None:
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    os.environ.setdefault("LANGSMITH_TRACING", "true" if settings.langsmith_tracing else "false")
    FastAPIInstrumentor.instrument_app(app)
