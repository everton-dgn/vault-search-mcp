"""
Structured logging with structlog.

Features:
- JSON in production, parseable by ELK, Loki, and Datadog
- Readable colored console output in development
- Context variables for request IDs and metadata
- Fast serialization with orjson
- Logger caching

References:
- https://www.structlog.org/en/stable/logging-best-practices.html
- https://betterstack.com/community/guides/logging/structlog/
"""

import logging
import os
import sys
from collections.abc import Callable
from typing import Any, BinaryIO, TextIO, cast

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from vault_search.utils.privacy import redact_mapping, redact_text

# Detect the environment.
IS_PRODUCTION = os.environ.get("VAULT_SEARCH_ENV", "development") == "production"
IS_TTY = sys.stderr.isatty()
LOG_LEVEL = os.environ.get("VAULT_SEARCH_LOG_LEVEL", "INFO").upper()


class _DynamicStderrText:
    """Forward writes to the current stderr, including during test capture."""

    def write(self, value: str) -> int:
        return sys.stderr.write(value)

    def flush(self) -> None:
        sys.stderr.flush()


class _DynamicStderrBytes:
    """Binary stderr proxy used by the JSON renderer."""

    def write(self, value: bytes) -> int:
        return sys.stderr.buffer.write(value)

    def flush(self) -> None:
        sys.stderr.buffer.flush()


class _DynamicStderrHandler(logging.StreamHandler[TextIO]):
    """Avoid retaining a reference to an already closed capture stream."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stderr
        super().emit(record)


class PrivacyFilter(logging.Filter):
    """Apply the same privacy policy to standard-library logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.msg)
        if isinstance(record.args, dict):
            record.args = redact_mapping(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact_mapping(item) for item in record.args)

        if record.exc_info:
            record.error_type = getattr(record.exc_info[0], "__name__", "Exception")
            record.exc_info = None
        record.stack_info = None
        return True


def add_app_context(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Add application context to every log event."""
    event_dict["app"] = "vault-search-mcp"
    return event_dict


def add_safe_logger_name(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Include the logger name only when the factory exposes it."""
    name = getattr(logger, "name", None)
    if name:
        event_dict["logger"] = redact_text(name)
    return event_dict


def censor_sensitive_fields(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Recursively remove private content and paths from logs."""
    exc_info = event_dict.pop("exc_info", None)
    if exc_info:
        if isinstance(exc_info, tuple) and exc_info:
            error_type = getattr(exc_info[0], "__name__", "Exception")
        elif isinstance(exc_info, BaseException):
            error_type = type(exc_info).__name__
        else:
            error_type = "Exception"
        event_dict["error_type"] = error_type

    return redact_mapping(event_dict)


def configure_logging(
    json_output: bool | None = None,
    level: str | None = None,
) -> None:
    """
    Configure structured logging for the application.

    Parameters:
        json_output: Force JSON; ``None`` auto-detects from the TTY.
        level: Log level such as DEBUG, INFO, WARNING, or ERROR.
    """
    # Auto-detect the format when unspecified.
    if json_output is None:
        # Use JSON in production or outside a TTY, such as Docker or systemd.
        json_output = IS_PRODUCTION or not IS_TTY

    # Log level.
    log_level = getattr(logging, level or LOG_LEVEL, logging.INFO)

    # Shared processors.
    shared_processors: list[Processor] = [
        # Add context variables such as request_id and user_id.
        structlog.contextvars.merge_contextvars,
        # Add application context.
        add_app_context,
        # Add the logger name.
        add_safe_logger_name,
        # Add the log level.
        structlog.stdlib.add_log_level,
        # Identify the module without revealing the local checkout path.
        structlog.processors.CallsiteParameterAdder(
            [
                structlog.processors.CallsiteParameter.MODULE,
                structlog.processors.CallsiteParameter.LINENO,
            ]
        ),
        # Redaction must run after all contextual fields have been added.
        censor_sensitive_fields,
        # Timestamp ISO 8601
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    processors: list[Processor]
    factory: Callable[..., WrappedLogger]

    if json_output:
        # Production: JSON optimized with orjson.
        processors = shared_processors + [
            # Serialize to JSON with orjson.
            structlog.processors.JSONRenderer(serializer=_orjson_dumps),
        ]
        # High-throughput factory that writes bytes directly to stderr.
        # MCP stdio transport reserves stdout for the protocol.
        factory = structlog.BytesLoggerFactory(file=cast(BinaryIO, _DynamicStderrBytes()))
    else:
        # Development: readable colored console output.
        processors = shared_processors + [
            # Colors and readable formatting.
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=structlog.dev.plain_traceback,
            ),
        ]
        # Keep logs on stderr so stdout remains clean.
        factory = structlog.PrintLoggerFactory(file=cast(TextIO, _DynamicStderrText()))

    # Configure structlog.
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=factory,
        cache_logger_on_first_use=True,
    )

    # Configure standard-library logging to capture dependency logs.
    stdlib_handler = _DynamicStderrHandler()
    stdlib_handler.addFilter(PrivacyFilter())
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=[stdlib_handler],
        force=True,
    )


def _orjson_dumps(obj: Any, **kwargs: Any) -> bytes:
    """Serialize to JSON with orjson."""
    import orjson

    return orjson.dumps(obj, default=str)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger.

    Parameters:
        name: Optional logger name; uses the caller module when omitted.

    Returns:
        A structured logger with context.

    Usage:
        logger = get_logger(__name__)
        logger.info("operation completed", duration_ms=45.2, results=10)
    """
    return structlog.get_logger(name)


# Configure automatically on import; callers may reconfigure later.
configure_logging()
