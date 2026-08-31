"""
Cache de metadados de notas com invalidação por filesystem.

Usa chave composta (path, mtime_ns, size) para validação automática
sem necessidade de watcher — se o arquivo mudou, cache miss.
"""

import logging
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from vault_search.crud.types import NoteMetadata

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class CacheKey:
    """Chave de cache baseada em filesystem metadata."""

    path: str
    mtime_ns: int
    size: int

    @classmethod
    def from_path(cls, file_path: Path) -> CacheKey:
        """Cria chave a partir de um path, usando stat()."""
        stat = file_path.stat()
        return cls(
            path=str(file_path),
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
        )

    @classmethod
    def from_stat(cls, path: str, stat_result: os.stat_result) -> CacheKey:
        """Cria chave a partir de stat_result já obtido (evita stat() duplo)."""
        return cls(
            path=path,
            mtime_ns=stat_result.st_mtime_ns,
            size=stat_result.st_size,
        )


class MetadataCache:
    """
    Cache LRU em memória para metadados de notas.

    A validação é automática: se o arquivo mudou (mtime_ns ou size diferente),
    a chave antiga não será encontrada e haverá cache miss.

    Thread-safe via lock.

    Uso:
        cache = MetadataCache(max_size=10000)

        # Tentar obter do cache
        key = CacheKey.from_path(file_path)
        metadata = cache.get(key)
        if metadata is None:
            # Cache miss - carregar e armazenar
            metadata = load_metadata(file_path)
            cache.set(key, metadata)
    """

    def __init__(self, max_size: int = 10000):
        """
        Parâmetros:
            max_size: número máximo de entradas no cache
        """
        self._max_size = max_size
        self._cache: OrderedDict[CacheKey, NoteMetadata] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: CacheKey) -> NoteMetadata | None:
        """
        Obtém metadados do cache.

        Move item acessado para o fim (LRU).

        Parâmetros:
            key: chave de cache (path, mtime_ns, size)

        Retorna:
            NoteMetadata se encontrado, None caso contrário.
        """
        with self._lock:
            if key in self._cache:
                # Move para o fim (mais recente)
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None

    def set(self, key: CacheKey, metadata: NoteMetadata) -> None:
        """
        Armazena metadados no cache.

        Se cache cheio, remove item mais antigo (LRU eviction).

        Parâmetros:
            key: chave de cache
            metadata: metadados a armazenar
        """
        with self._lock:
            if key in self._cache:
                # Atualizar existente e mover para o fim
                self._cache.move_to_end(key)
                self._cache[key] = metadata
                return

            # Eviction se necessário
            while len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)

            self._cache[key] = metadata

    def invalidate(self, path: str) -> int:
        """
        Invalida todas as entradas para um path específico.

        Útil para forçar invalidação via watcher.

        Parâmetros:
            path: caminho do arquivo

        Retorna:
            Número de entradas removidas.
        """
        with self._lock:
            to_remove = [k for k in self._cache if k.path == path]
            for k in to_remove:
                del self._cache[k]
            return len(to_remove)

    def clear(self) -> None:
        """Limpa todo o cache."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    @property
    def size(self) -> int:
        """Número de entradas no cache."""
        with self._lock:
            return len(self._cache)

    @property
    def hit_rate(self) -> float:
        """Taxa de acertos (0.0 a 1.0)."""
        with self._lock:
            total = self._hits + self._misses
            if total == 0:
                return 0.0
            return self._hits / total

    def stats(self) -> dict[str, int | float]:
        """Retorna estatísticas do cache."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0.0
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 4),
            }


# Instância global singleton
_metadata_cache: MetadataCache | None = None
_cache_lock = threading.Lock()


def get_metadata_cache(max_size: int = 10000) -> MetadataCache:
    """Obtém instância singleton do cache de metadados."""
    global _metadata_cache
    with _cache_lock:
        if _metadata_cache is None:
            _metadata_cache = MetadataCache(max_size=max_size)
        return _metadata_cache


def reset_metadata_cache() -> None:
    """Reseta o cache singleton (útil para testes)."""
    global _metadata_cache
    with _cache_lock:
        if _metadata_cache is not None:
            _metadata_cache.clear()
        _metadata_cache = None
