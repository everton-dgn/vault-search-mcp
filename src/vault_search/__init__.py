"""vault-search-mcp: local search for knowledge bases."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "VAULT_PATH",
    "DATA_DIR",
    "LANCEDB_TABLE",
    "EMBEDDING_MODEL",
    "RERANKER_MODEL",
    "RiskLevel",
    "ChunkRecord",
    "ChunkWithVector",
    "SearchResult",
    "ReindexResult",
    "IndexStats",
    "FullReindexStats",
]

_LAZY_EXPORTS = {
    "VAULT_PATH": ("vault_search.config.paths", "VAULT_PATH"),
    "DATA_DIR": ("vault_search.config.paths", "DATA_DIR"),
    "LANCEDB_TABLE": ("vault_search.config.paths", "LANCEDB_TABLE"),
    "EMBEDDING_MODEL": ("vault_search.config.embedding", "EMBEDDING_MODEL"),
    "RERANKER_MODEL": ("vault_search.config.embedding", "RERANKER_MODEL"),
    "RiskLevel": ("vault_search.config.security", "RiskLevel"),
    "ChunkRecord": ("vault_search.type_defs", "ChunkRecord"),
    "ChunkWithVector": ("vault_search.type_defs", "ChunkWithVector"),
    "SearchResult": ("vault_search.type_defs", "SearchResult"),
    "ReindexResult": ("vault_search.type_defs", "ReindexResult"),
    "IndexStats": ("vault_search.type_defs", "IndexStats"),
    "FullReindexStats": ("vault_search.type_defs", "FullReindexStats"),
}


def __getattr__(name: str) -> Any:
    """Preserve the public API without loading configuration during package import."""
    try:
        module_name, attribute = _LAZY_EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
