"""
Kiro Structured Logging

Uses structlog for structured, JSON-formatted logging.
Provides both JSON output (for production) and pretty console output (for development).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Literal

import structlog
from structlog.typing import EventDict, WrappedLogger


def add_app_context(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Add Kiro application context to all log entries."""
    event_dict["app"] = "kiro"
    return event_dict


def setup_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
    format: Literal["json", "console"] = "json",
    log_file: Path | None = None,
) -> None:
    """
    Configure structured logging for Kiro.

    Args:
        level: Minimum log level to output
        format: Output format - 'json' for production, 'console' for development
        log_file: Optional file path for log output
    """
    # Convert level string to logging constant
    log_level = getattr(logging, level.upper())

    # Shared processors for structlog
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        add_app_context,
        structlog.stdlib.ExtraAdder(),
    ]

    if format == "json":
        # Production: JSON output
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Development: Pretty console output
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=structlog.dev.plain_traceback,
            ),
        ]

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also configure stdlib logging for third-party libraries
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Set up file handler if requested
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        
        # Use JSON format for file output
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(file_handler)

    # Quiet noisy loggers
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """
    Get a logger instance.

    Args:
        name: Logger name (usually __name__ of the calling module)

    Returns:
        Configured structlog BoundLogger
    """
    return structlog.get_logger(name)


# Convenience: pre-bound logger for quick imports
logger = structlog.get_logger()
