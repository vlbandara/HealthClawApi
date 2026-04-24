from __future__ import annotations

import logging

import structlog

from healthclaw.core.config import Settings


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(level=logging.INFO if settings.is_production else logging.DEBUG)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
