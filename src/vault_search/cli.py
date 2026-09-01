"""Minimal bootstraps that prevent tracebacks and private values during startup."""

from __future__ import annotations

import importlib
import os
import sys
import uuid
from collections.abc import Callable


def _validate_startup_config() -> None:
    """Validate configuration before importing runtimes and heavy dependencies."""
    from vault_search.config.loader import get_config

    get_config()


def _run(module_name: str, *, prepare: Callable[[], None] | None = None) -> int:
    """Load the runtime inside a sanitized error boundary."""
    try:
        if prepare is not None:
            prepare()
        _validate_startup_config()
        module = importlib.import_module(module_name)
        module.main()
    except KeyboardInterrupt:
        return 130
    except SystemExit as error:
        if error.code is None:
            return 0
        if isinstance(error.code, int):
            return error.code
        reference = uuid.uuid4().hex[:12]
        sys.stderr.write(f"vault-search startup failed: SystemExit; reference={reference}\n")
        return 1
    except Exception as error:
        reference = uuid.uuid4().hex[:12]
        sys.stderr.write(
            f"vault-search startup failed: {type(error).__name__}; reference={reference}\n"
        )
        return 1
    return 0


def mcp_main() -> int:
    """Start the MCP server after installing the error boundary."""
    return _run("vault_search.server.mcp")


def daemon_main() -> int:
    """Start the local daemon without allowing ModelManager auto-connection."""

    def prepare() -> None:
        os.environ["VAULT_SEARCH_RUNNING_AS_DAEMON"] = "1"

    return _run("vault_search.daemon.server", prepare=prepare)


def config_main() -> int:
    """Validate configuration without printing paths or resolved values."""
    try:
        _validate_startup_config()
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        reference = uuid.uuid4().hex[:12]
        sys.stderr.write(
            f"vault-search configuration invalid: {type(error).__name__}; reference={reference}\n"
        )
        return 1
    sys.stdout.write("vault-search configuration: ok\n")
    return 0
