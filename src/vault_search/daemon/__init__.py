"""
Daemon para manter modelos em memória e watcher ativo.

Arquitetura:
- Daemon roda persistente, carrega modelos BGE-M3 e reranker
- MCP server conecta via HTTP para embed/rerank
- Watcher sempre ativo indexando mudanças
"""

from vault_search.daemon.client import DaemonClient, is_daemon_running

__all__ = ["DaemonClient", "is_daemon_running"]
