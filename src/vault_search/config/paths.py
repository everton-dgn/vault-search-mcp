"""
Path and directory settings for vault-search-mcp.

NOTE: This module exists for compatibility with legacy imports.
For the centralized configuration: from vault_search.config import get_config
"""

import os
from pathlib import Path

from vault_search.config.loader import get_config

# Project root, three levels above src/vault_search/config/
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _resolve_path(path_str: str) -> Path:
    """Resolve a path to an absolute path, expanding ``~`` and following symlinks."""
    return Path(path_str).expanduser().resolve(strict=False)


def _load_paths() -> tuple[Path, Path, str]:
    """Load paths from environment or config with a legacy fallback."""
    # Explicit override to support running the daemon outside the project.
    env_vault = os.environ.get("VAULT_SEARCH_VAULT_PATH") or os.environ.get("VAULT_PATH")
    env_data_dir = os.environ.get("VAULT_SEARCH_DATA_DIR")
    if env_vault:
        config = get_config()
        data_dir = _resolve_path(env_data_dir or config.paths.data_dir)
        return _resolve_path(env_vault), data_dir, config.paths.lancedb_table

    config = get_config()
    vault = _resolve_path(config.paths.vault_path)
    data_dir = _resolve_path(env_data_dir or config.paths.data_dir)
    return vault, data_dir, config.paths.lancedb_table


# Effective paths
VAULT_PATH, DATA_DIR, LANCEDB_TABLE = _load_paths()

# Directory for auxiliary SQLite databases such as cache and catalog
DB_DIR = DATA_DIR

LINKS_TABLE = "links_index"
ALIASES_TABLE = "note_aliases"
