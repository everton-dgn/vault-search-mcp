"""
Testes para o cache de metadados de notas.
"""

from pathlib import Path

from vault_search.crud.cache import (
    CacheKey,
    MetadataCache,
    get_metadata_cache,
    reset_metadata_cache,
)


class TestCacheKey:
    """Testes para CacheKey."""

    def test_from_path(self, tmp_path: Path):
        path = tmp_path / "cache-key.bin"
        path.write_bytes(b"test content")

        key = CacheKey.from_path(path)

        assert key.path == str(path)
        assert key.mtime_ns > 0
        assert key.size == 12  # "test content"

    def test_from_stat(self, tmp_path: Path):
        path = tmp_path / "cache-stat.bin"
        path.write_bytes(b"hello")
        stat = path.stat()

        key = CacheKey.from_stat(str(path), stat)

        assert key.path == str(path)
        assert key.mtime_ns == stat.st_mtime_ns
        assert key.size == stat.st_size

    def test_equality(self):
        key1 = CacheKey(path="/test", mtime_ns=1000, size=100)
        key2 = CacheKey(path="/test", mtime_ns=1000, size=100)
        key3 = CacheKey(path="/test", mtime_ns=2000, size=100)

        assert key1 == key2
        assert key1 != key3

    def test_hashable(self):
        key = CacheKey(path="/test", mtime_ns=1000, size=100)
        d = {key: "value"}
        assert d[key] == "value"


class TestMetadataCache:
    """Testes para MetadataCache."""

    def test_get_miss(self):
        cache = MetadataCache(max_size=10)
        key = CacheKey(path="/test", mtime_ns=1000, size=100)
        assert cache.get(key) is None

    def test_set_and_get(self):
        cache = MetadataCache(max_size=10)
        key = CacheKey(path="/test", mtime_ns=1000, size=100)
        metadata = {"path": "/test", "title": "Test"}

        cache.set(key, metadata)
        result = cache.get(key)

        assert result == metadata

    def test_lru_eviction(self):
        cache = MetadataCache(max_size=2)

        key1 = CacheKey(path="/1", mtime_ns=1000, size=100)
        key2 = CacheKey(path="/2", mtime_ns=1000, size=100)
        key3 = CacheKey(path="/3", mtime_ns=1000, size=100)

        cache.set(key1, {"path": "/1"})
        cache.set(key2, {"path": "/2"})
        cache.set(key3, {"path": "/3"})

        # key1 deve ter sido evictado
        assert cache.get(key1) is None
        assert cache.get(key2) is not None
        assert cache.get(key3) is not None

    def test_lru_reorder_on_get(self):
        cache = MetadataCache(max_size=2)

        key1 = CacheKey(path="/1", mtime_ns=1000, size=100)
        key2 = CacheKey(path="/2", mtime_ns=1000, size=100)
        key3 = CacheKey(path="/3", mtime_ns=1000, size=100)

        cache.set(key1, {"path": "/1"})
        cache.set(key2, {"path": "/2"})

        # Acessar key1 move para o fim (mais recente)
        cache.get(key1)

        # Adicionar key3 deve evictar key2 (mais antigo)
        cache.set(key3, {"path": "/3"})

        assert cache.get(key1) is not None
        assert cache.get(key2) is None
        assert cache.get(key3) is not None

    def test_invalidate_by_path(self):
        cache = MetadataCache(max_size=10)

        key1 = CacheKey(path="/test", mtime_ns=1000, size=100)
        key2 = CacheKey(path="/test", mtime_ns=2000, size=100)  # mesmo path, diferente mtime
        key3 = CacheKey(path="/other", mtime_ns=1000, size=100)

        cache.set(key1, {"path": "/test", "v": 1})
        cache.set(key2, {"path": "/test", "v": 2})
        cache.set(key3, {"path": "/other"})

        removed = cache.invalidate("/test")

        assert removed == 2
        assert cache.get(key1) is None
        assert cache.get(key2) is None
        assert cache.get(key3) is not None

    def test_clear(self):
        cache = MetadataCache(max_size=10)
        key = CacheKey(path="/test", mtime_ns=1000, size=100)
        cache.set(key, {"path": "/test"})

        cache.clear()

        assert cache.size == 0
        assert cache.get(key) is None

    def test_size_property(self):
        cache = MetadataCache(max_size=10)
        assert cache.size == 0

        cache.set(CacheKey("/1", 1000, 100), {})
        assert cache.size == 1

        cache.set(CacheKey("/2", 1000, 100), {})
        assert cache.size == 2

    def test_hit_rate(self):
        cache = MetadataCache(max_size=10)
        key = CacheKey(path="/test", mtime_ns=1000, size=100)
        cache.set(key, {"path": "/test"})

        cache.get(key)  # hit
        cache.get(key)  # hit
        cache.get(CacheKey("/miss", 1000, 100))  # miss

        # 2 hits, 1 miss = 66.67%
        assert 0.66 < cache.hit_rate < 0.67

    def test_stats(self):
        cache = MetadataCache(max_size=100)
        key = CacheKey(path="/test", mtime_ns=1000, size=100)
        cache.set(key, {"path": "/test"})
        cache.get(key)

        stats = cache.stats()

        assert stats["size"] == 1
        assert stats["max_size"] == 100
        assert stats["hits"] == 1
        assert stats["misses"] == 0


class TestCacheSingleton:
    """Testes para funções singleton."""

    def setup_method(self):
        reset_metadata_cache()

    def test_get_metadata_cache_singleton(self):
        cache1 = get_metadata_cache()
        cache2 = get_metadata_cache()
        assert cache1 is cache2

    def test_reset_clears_singleton(self):
        cache1 = get_metadata_cache()
        key = CacheKey(path="/test", mtime_ns=1000, size=100)
        cache1.set(key, {"path": "/test"})

        reset_metadata_cache()

        cache2 = get_metadata_cache()
        assert cache2.get(key) is None


class TestCacheAutoInvalidation:
    """Testa invalidação automática por mudança de arquivo."""

    def test_file_change_causes_miss(self, tmp_path: Path):
        """Se o arquivo muda, a chave antiga não funciona."""
        cache = MetadataCache(max_size=10)
        path = tmp_path / "changing-note.md"
        path.write_text("content v1")

        # Cachear com estado inicial
        key1 = CacheKey.from_path(path)
        cache.set(key1, {"version": 1})

        # O tamanho diferente garante uma nova chave sem depender do relógio do sistema.
        path.write_text("content v2 - longer")

        # Nova chave é diferente (mtime e/ou size mudaram)
        key2 = CacheKey.from_path(path)

        assert key1 != key2
        assert cache.get(key1) == {"version": 1}  # chave antiga ainda existe
        assert cache.get(key2) is None  # nova chave não existe
