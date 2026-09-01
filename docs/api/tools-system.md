# System tools

These three tools expose current process state. They help diagnose an
installation and produce local measurements. Values are not comparable across
machines unless the benchmark environment is recorded.

## `system_stats`

```python
system_stats(reset: bool = False) -> dict
```

| Key | Content |
|---|---|
| `performance.operations` | Recorded operation counts and latency distribution |
| `cache.metadata_cache` | Metadata cache size, capacity, hits, and misses |
| `cache.embedding_cache` | Query-embedding cache state |
| `catalog.notes_catalog` | Catalog counts or uninitialized state |
| `index` | Chunk count, unique notes, and last modification |
| `prewarm.status` | This process's prewarm result |

With `reset=True`, the tool assembles the response and then clears operation
metrics. It does not erase caches, catalog rows, or index data.

```python
{
    "performance": {"operations": {}},
    "cache": {"metadata_cache": {}, "embedding_cache": {}},
    "catalog": {"notes_catalog": {}},
    "index": {
        "total_chunks": 0,
        "unique_notes": 0,
        "last_modified": None,
    },
    "prewarm": {"status": {}},
}
```

The example documents structure, not a measured baseline.

## `health_check`

```python
health_check() -> dict
```

Checks index, catalog, models, required daemon state, and accumulated alerts.

```python
{
    "status": "healthy",
    "uptime_seconds": 0.0,
    "components": {
        "index_ready": True,
        "catalog_ready": True,
        "embed_model_loaded": False,
        "reranker_loaded": False,
        "daemon_required": False,
    },
    "alerts": [],
    "alerts_count": 0,
}
```

| Status | Condition |
|---|---|
| `healthy` | Index available and no current alert |
| `degraded` | Index has no chunks while the catalog is available |
| `warning` | A latency or cache alert exists |
| `unhealthy` | Index and catalog unavailable, or a required daemon is missing |

The current code opens an alert when recorded p95 exceeds 500 ms or an observed
cache hit rate drops below 0.70. These are fixed operational thresholds, not
universal performance targets.

Unloaded models do not make health invalid by themselves. They can load on
demand or run through the daemon.

## `benchmark_search`

```python
benchmark_search(query: str = "test", iterations: int = 10) -> dict | str
```

Runs `search` with `top_k=10` in the current process. `iterations` is clamped
from 1 through 100.

```python
{
    "query_length": 4,
    "iterations": 10,
    "mean_ms": 0.0,
    "min_ms": 0.0,
    "max_ms": 0.0,
    "p50_ms": 0.0,
    "p95_ms": 0.0,
}
```

The zero values document types only. Failed samples add `errors` and
`sample_error_type`. When every sample fails, statistics are omitted and the
tool returns environment diagnostics.

This microbenchmark does not measure recall, reranking quality, concurrency, or
memory use. Follow the [benchmark protocol](../performance/benchmarking.md)
before publishing results.
