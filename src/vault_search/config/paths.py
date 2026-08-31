"""
Configurações de caminhos e diretórios do vault-search-mcp.

NOTA: Este módulo existe para compatibilidade com imports legados.
Para nova config centralizada: from vault_search.config import get_config
"""

import os
from pathlib import Path

from vault_search.config.loader import get_config

# Raiz do projeto (3 níveis acima de src/vault_search/config/)
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _resolve_path(path_str: str) -> Path:
    """Resolve path para absoluto, expandindo ~ e seguindo symlink."""
    return Path(path_str).expanduser().resolve(strict=False)


def _load_paths() -> tuple[Path, Path, str]:
    """Carrega paths via env/config com fallback legado."""
    # Override explícito para facilitar execução do daemon fora do projeto.
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


# Paths efetivos
VAULT_PATH, DATA_DIR, LANCEDB_TABLE = _load_paths()

# Diretório para bancos SQLite auxiliares (cache, catalog)
DB_DIR = DATA_DIR

LINKS_TABLE = "links_index"
ALIASES_TABLE = "note_aliases"
