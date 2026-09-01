"""
Centralized vault-search configuration.

New system (recommended):
    from vault_search.config import get_config

    config = get_config()
    print(config.search.top_k)
    print(config.paths.vault_path)

Legacy imports (compatibility):
    from vault_search.config.paths import VAULT_PATH, DATA_DIR
    from vault_search.config.search import SEARCH_TOP_K, FTS_LANGUAGE
    from vault_search.config.security import RiskLevel
"""

from vault_search.config.loader import (
    get_config,
    get_project_root,
    load_config_from_dict,
    load_config_from_file,
    reload_config,
)
from vault_search.config.settings import (
    ChunkingConfig,
    DaemonConfig,
    EmbeddingConfig,
    FrontmatterAIConfig,
    FTSConfig,
    IndexingConfig,
    NavigationConfig,
    PathsConfig,
    PDFConfig,
    PrewarmConfig,
    SearchConfig,
    SecurityConfig,
    VaultSearchConfig,
    VectorIndexConfig,
    WatcherConfig,
)

__all__ = [
    # Loader
    "get_config",
    "get_project_root",
    "load_config_from_dict",
    "load_config_from_file",
    "reload_config",
    # Models
    "ChunkingConfig",
    "DaemonConfig",
    "EmbeddingConfig",
    "FTSConfig",
    "FrontmatterAIConfig",
    "IndexingConfig",
    "NavigationConfig",
    "PathsConfig",
    "PDFConfig",
    "PrewarmConfig",
    "SearchConfig",
    "SecurityConfig",
    "VaultSearchConfig",
    "VectorIndexConfig",
    "WatcherConfig",
]
