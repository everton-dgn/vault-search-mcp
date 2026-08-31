"""Stable public errors that omit internal environment details."""

from __future__ import annotations

import logging
import secrets
from typing import Any


def public_error(
    logger: logging.Logger,
    operation: str,
    error: BaseException,
    *,
    code: str = "internal_error",
    message: str = "The operation could not be completed.",
) -> str:
    """Log minimal metadata and return a safe client-facing message."""
    error_id = secrets.token_hex(4)
    logger.error(
        "%s failed error_id=%s error_type=%s",
        operation,
        error_id,
        type(error).__name__,
    )
    return f"Error [{code}]: {message} Reference: {error_id}."


def public_error_dict(
    logger: logging.Logger,
    operation: str,
    error: BaseException,
    *,
    code: str = "internal_error",
    message: str = "The operation could not be completed.",
) -> dict[str, Any]:
    """Return the structured MCP resource form of :func:`public_error`."""
    return {
        "error": public_error(
            logger,
            operation,
            error,
            code=code,
            message=message,
        ),
        "code": code,
    }
