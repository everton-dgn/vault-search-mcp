"""Execução segura por ``python -m vault_search [daemon]``."""

from __future__ import annotations

import sys

from vault_search.cli import config_main, daemon_main, mcp_main


def main() -> int:
    """Seleciona o servidor MCP ou o daemon sem importar o runtime antes da borda."""
    if len(sys.argv) == 1:
        return mcp_main()
    if sys.argv[1:] == ["daemon"]:
        return daemon_main()
    if sys.argv[1:] == ["config"]:
        return config_main()
    sys.stderr.write("usage: python -m vault_search [daemon|config]\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
