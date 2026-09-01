"""MCP middleware that preserves privacy in errors and telemetry."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp import McpError
from mcp.types import ErrorData

from vault_search.utils.privacy import redact_text


class SafeErrorMiddleware(Middleware):
    """Convert every exception to a stable code and message."""

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("vault_search.mcp.errors")
        self.error_counts: dict[str, int] = {}

    @staticmethod
    def _error_contract(error: Exception) -> tuple[int, str]:
        source = error.__cause__ or error
        if isinstance(error, McpError):
            code = error.error.code
            if code == -32602:
                return code, "Invalid params"
            if code == -32001:
                return code, "Resource not found"
            if code == -32000:
                return code, "Request failed"
        if isinstance(source, (ValueError, TypeError)):
            return -32602, "Invalid params"
        if isinstance(source, (FileNotFoundError, KeyError)):
            return -32001, "Resource not found"
        if isinstance(source, (PermissionError, TimeoutError)):
            return -32000, "Request failed"
        return -32603, "Internal error"

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        try:
            return await call_next(context)
        except Exception as error:
            error_type = type(error).__name__
            method = redact_text(context.method or "unknown")
            reference = uuid.uuid4().hex[:12]
            key = f"{error_type}:{method}"
            self.error_counts[key] = self.error_counts.get(key, 0) + 1
            self.logger.error(
                "mcp_request_failed method=%s error_type=%s reference=%s",
                method,
                error_type,
                reference,
            )
            code, message = self._error_contract(error)
            raise McpError(
                ErrorData(code=code, message=f"{message}; reference={reference}")
            ) from None


class SafeTimingMiddleware(Middleware):
    """Measure requests without interpolating exceptions or parameters."""

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("vault_search.mcp.timing")

    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        method = redact_text(context.method or "unknown")
        started = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as error:
            duration_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "mcp_request_finished method=%s status=failed duration_ms=%.2f error_type=%s",
                method,
                duration_ms,
                type(error).__name__,
            )
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        self.logger.info(
            "mcp_request_finished method=%s status=ok duration_ms=%.2f",
            method,
            duration_ms,
        )
        return result
