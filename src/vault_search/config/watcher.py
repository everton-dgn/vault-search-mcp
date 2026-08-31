"""
Configurações do file watcher.
"""

from vault_search.config.loader import get_config

_config = get_config().watcher

# Debounce (segundos) para evitar reindexações múltiplas em edições rápidas
WATCHER_DEBOUNCE = _config.debounce

# Fator de divisão do debounce para intervalo de polling do worker
WATCHER_POLL_FACTOR = _config.poll_factor

# Timeout (segundos) para join de threads ao parar o watcher
THREAD_JOIN_TIMEOUT = _config.thread_join_timeout
