# Implemented optimizations

This inventory describes mechanisms present in code. It assigns no numeric gain
without a reproducible benchmark.

## Reduced repeated work

- Stat-keyed LRU metadata cache.
- Embedding cache for repeated queries.
- SQLite catalog for filters and pagination.
- Separate link and alias index.

## Indexing pipeline

- File parsing in a thread pool.
- Batched embedding and persistence.
- Staging tables before generation publication.
- Periodic LanceDB compaction.
- Optional ANN creation above a chunk threshold.

## Search

- Bounded candidates before reranking.
- FTS and vectors combined in hybrid mode.
- Column projection to reduce materialization.
- Optional prewarm when memory guardrails permit.

## Lifecycle

- Local daemon reuses models across MCP processes.
- Watcher coalesces events through debounce.
- Catalog reconciliation repairs missed events.
- Coordinated shutdown waits for protected sections.

## Tradeoffs

| Mechanism | Benefit | Cost |
|---|---|---|
| Cache | Less repeated computation | Memory and correct invalidation |
| Threads | Overlap I/O and parsing | Contention and memory peaks |
| Batch | Better backend utilization | Per-batch latency and memory |
| ANN | Fewer examined candidates | Build time, disk, approximate recall |
| Prewarm | Avoid cold file access | Resident memory |
| Daemon | Reuse models | Extra process and health checks |

## Proving a gain

1. State the hypothesis and metric.
2. Fix commit, lockfile, model, dataset, and hardware.
3. Measure baseline and variant in an alternating order.
4. Publish samples, median, p95, and peak memory.
5. Verify correctness and recall in addition to time.

See [benchmarking.md](benchmarking.md).
