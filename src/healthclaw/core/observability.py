from __future__ import annotations

import logging
import os
import re

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from healthclaw.core.config import Settings

logger = logging.getLogger(__name__)

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


def _configure_tracer_provider(settings: Settings) -> None:
    resource = Resource.create({"service.name": "healthclaw"})
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    if settings.otel_exporter_otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc import OTLPSpanExporter as GRPCExporter
            from opentelemetry.exporter.otlp.proto.http import OTLPSpanExporter as HTTPExporter

            if settings.otel_exporter_otlp_endpoint.startswith("http"):
                exporter = HTTPExporter(endpoint=settings.otel_exporter_otlp_endpoint)
            else:
                exporter = GRPCExporter(endpoint=settings.otel_exporter_otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception as exc:
            logger.warning("Failed to configure OTLP exporter: %s", exc)

    if settings.langsmith_tracing and settings.langsmith_project:
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
        os.environ.setdefault("LANGSMITH_TRACING", "true")


def configure_observability(app: FastAPI, settings: Settings) -> None:
    _configure_tracer_provider(settings)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    os.environ.setdefault("LANGSMITH_TRACING", "true" if settings.langsmith_tracing else "false")
    FastAPIInstrumentor.instrument_app(app)
