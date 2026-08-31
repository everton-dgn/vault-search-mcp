"""
Filesystem-invalidated note metadata cache.

Uses a composite (path, mtime_ns, size) key so changed files become cache misses
without requiring a watcher.
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
    """Cache key based on filesystem metadata."""

    path: str
    mtime_ns: int
    size: int

    @classmethod
    def from_path(cls, file_path: Path) -> CacheKey:
        """Create a key from a path using stat()."""
        stat = file_path.stat()
        return cls(
            path=str(file_path),
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
        )

    @classmethod
    def from_stat(cls, path: str, stat_result: os.stat_result) -> CacheKey:
        """Create a key from an existing stat result."""
        return cls(
            path=path,
            mtime_ns=stat_result.st_mtime_ns,
            size=stat_result.st_size,
        )


class MetadataCache:
    """
    Thread-safe in-memory LRU cache for note metadata.

    A changed mtime or size yields a new key and therefore a cache miss.

    Example:
        cache = MetadataCache(max_size=10000)

        # Try the cache first.
        key = CacheKey.from_path(file_path)
        metadata = cache.get(key)
        if metadata is None:
            # Load and store on a miss.
            metadata = load_metadata(file_path)
            cache.set(key, metadata)
    """

    def __init__(self, max_size: int = 10000):
        """
        Parameters:
            max_size: maximum number of cache entries
        """
        self._max_size = max_size
        self._cache: OrderedDict[CacheKey, NoteMetadata] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: CacheKey) -> NoteMetadata | None:
        """
        Get metadata from the cache.

        Move a hit to the most-recent end of the LRU.

        Parameters:
            key: cache key (path, mtime_ns, size)

        Returns:
            Cached NoteMetadata, or None.
        """
        with self._lock:
            if key in self._cache:
                # Mark the entry as most recently used.
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None

    def set(self, key: CacheKey, metadata: NoteMetadata) -> None:
        """
        Store metadata in the cache.

        Evict the least-recently used entry when the cache is full.

        Parameters:
            key: cache key
            metadata: metadata to store
        """
        with self._lock:
            if key in self._cache:
                # Update an existing entry and mark it as recent.
                self._cache.move_to_end(key)
                self._cache[key] = metadata
                return

            # Evict until the size limit is satisfied.
            while len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)

            self._cache[key] = metadata

    def invalidate(self, path: str) -> int:
        """
        Invalidate every entry for one path.

        This supports explicit watcher-driven invalidation.

        Parameters:
            path: file path

        Returns:
            Number of removed entries.
        """
        with self._lock:
            to_remove = [k for k in self._cache if k.path == path]
            for k in to_remove:
                del self._cache[k]
            return len(to_remove)

    def clear(self) -> None:
        """Clear the entire cache and its counters."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    @property
    def size(self) -> int:
        """Number of entries in the cache."""
        with self._lock:
            return len(self._cache)

    @property
    def hit_rate(self) -> float:
        """Cache hit rate from 0.0 to 1.0."""
        with self._lock:
            total = self._hits + self._misses
            if total == 0:
                return 0.0
            return self._hits / total

    def stats(self) -> dict[str, int | float]:
        """Return cache statistics."""
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


# Process-wide singleton.
_metadata_cache: MetadataCache | None = None
_cache_lock = threading.Lock()


def get_metadata_cache(max_size: int = 10000) -> MetadataCache:
    """Return the process-wide metadata cache."""
    global _metadata_cache
    with _cache_lock:
        if _metadata_cache is None:
            _metadata_cache = MetadataCache(max_size=max_size)
        return _metadata_cache


def reset_metadata_cache() -> None:
    """Reset the process-wide cache, primarily for tests."""
    global _metadata_cache
    with _cache_lock:
        if _metadata_cache is not None:
            _metadata_cache.clear()
        _metadata_cache = None
