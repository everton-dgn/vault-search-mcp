"""
Helpers for the MCP server.

Includes parameter validation, privacy-safe logging, and decorators.
"""

import functools
import logging
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from vault_search.config.search import SEARCH_TOP_K_MAX, SEARCH_TOP_K_MIN
from vault_search.config.security import MAX_QUERY_LENGTH
from vault_search.server.errors import public_error

logger = logging.getLogger("vault-search-mcp")

P = ParamSpec("P")
R = TypeVar("R")


def clamp_top_k(top_k: int) -> int:
    """Clamp top_k to configured limits."""
    return max(SEARCH_TOP_K_MIN, min(top_k, SEARCH_TOP_K_MAX))


def truncate_query(query: str) -> str:
    """Truncate oversized queries before processing."""
    if len(query) > MAX_QUERY_LENGTH:
        logger.warning(
            "query_truncated original_length=%d maximum_length=%d",
            len(query),
            MAX_QUERY_LENGTH,
        )
        return query[:MAX_QUERY_LENGTH]
    return query


def log_query(query: str) -> str:
    """Return query metadata without logging its content."""
    return f"[redacted length={len(query)}]"


def execute_search(
    tool_name: str,
    query: str,
    top_k: int,
    search_fn: Callable[..., list[dict[str, object]]],
    **kwargs: object,
) -> list[dict[str, object]] | str:
    """
    Execute a search with standardized validation, logging, and errors.

    Parameters:
        tool_name: MCP tool name for logs
        query: search text
        top_k: result count
        search_fn: search callable
        **kwargs: additional arguments for search_fn

    Returns:
        Search results or a stable error message.
    """
    if not query or not query.strip():
        return "Error: query cannot be empty."
    query = truncate_query(query.strip())
    top_k = clamp_top_k(top_k)
    logger.info("%s query_length=%d top_k=%d", tool_name, len(query), top_k)
    try:
        return search_fn(query, top_k=top_k, **kwargs)
    except RuntimeError as e:
        return public_error(
            logger,
            tool_name,
            e,
            code="search_unavailable",
            message="Search is temporarily unavailable.",
        )
    except Exception as e:
        return public_error(logger, tool_name, e)


# =============================================================================
# MCP tool decorators
# =============================================================================


def with_error_handling(tool_name: str):
    """
    Add standardized error handling to an MCP tool.

    Converts internal exceptions to bounded public messages.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R | str]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | str:
            try:
                return func(*args, **kwargs)
            except (ValueError, FileNotFoundError) as e:
                return public_error(
                    logger,
                    tool_name,
                    e,
                    code="invalid_request",
                    message="The input is invalid or the resource does not exist.",
                )
            except Exception as e:
                return public_error(logger, tool_name, e)

        return wrapper

    return decorator


# Tools with specialized logic may continue to handle errors inline.
