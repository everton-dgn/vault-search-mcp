"""Bootstraps mínimos que impedem traceback e valores privados no startup."""

from __future__ import annotations

import importlib
import os
import sys
import uuid
from collections.abc import Callable


def _validate_startup_config() -> None:
    """Valida a configuração antes de importar runtimes e dependências pesadas."""
    from vault_search.config.loader import get_config

    get_config()


def _run(module_name: str, *, prepare: Callable[[], None] | None = None) -> int:
    """Carrega o runtime dentro de uma fronteira de erro sanitizada."""
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
    """Inicia o servidor MCP após instalar a fronteira de erro."""
    return _run("vault_search.server.mcp")


def daemon_main() -> int:
    """Inicia o daemon local sem permitir auto-conexão do ModelManager."""

    def prepare() -> None:
        os.environ["VAULT_SEARCH_RUNNING_AS_DAEMON"] = "1"

    return _run("vault_search.daemon.server", prepare=prepare)


def config_main() -> int:
    """Valida a configuração sem imprimir paths ou valores resolvidos."""
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
