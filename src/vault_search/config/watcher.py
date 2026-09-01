"""
File watcher settings.
"""

from vault_search.config.loader import get_config

_config = get_config().watcher

# Debounce in seconds to avoid repeated reindexing during rapid edits
WATCHER_DEBOUNCE = _config.debounce

# Debounce divisor used for the worker polling interval
WATCHER_POLL_FACTOR = _config.poll_factor

# Timeout in seconds for joining threads while stopping the watcher
THREAD_JOIN_TIMEOUT = _config.thread_join_timeout
