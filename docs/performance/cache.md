# Caches

## Current layers

| Layer | Key | Invalidation |
|---|---|---|
| Note metadata | path, `mtime_ns`, and size | Stat change creates a miss; CRUD invalidates the path |
| Query embedding | normalized-query hash | Bounded LRU; reconfiguration requires a new process |
| Operating-system file cache | accessed LanceDB files | Managed by the OS; prewarm is optional |

## Metadata cache

`MetadataCache` uses an `OrderedDict` and lock. The key includes filesystem
metadata, so a content change normally creates a new entry. CRUD also removes
entries for the affected path.

This reduces repeated parsing but does not remove races between `stat` and read.
File operations remain responsible for consistency.

## Embedding cache

`VaultSearcher` keeps repeated query embeddings in memory. The cache is bounded
and exposes hit and miss metrics through `system_stats`.

Never log a query to explain a hit. A hash, count, and duration are enough for
operational diagnosis.

## Measure

- current and maximum size for each cache;
- hits, misses, and calculated hit rate;
- cold and warm latency separately;
- process memory before and after warming;
- invalidation after write, move, and reindex.

Follow [benchmarking.md](benchmarking.md) before publishing numbers.

## Required failure tests

- content changes while file size stays constant;
- timestamp resolution cannot distinguish two writes;
- invalidation races with a read;
- LRU limit under concurrent access;
- query cache after an index-generation change.
