"""
Optional daemon that keeps models in memory and watches the vault.

Architecture:
- A persistent local process loads the embedding and reranking models
- The MCP server delegates embedding and reranking over loopback HTTP
- An optional watcher indexes filesystem changes
"""

from vault_search.daemon.client import DaemonClient, is_daemon_running

__all__ = ["DaemonClient", "is_daemon_running"]
