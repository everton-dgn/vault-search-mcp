# Environment tuning

## Start from automatic defaults

```yaml
embedding:
  device: "auto"
  use_fp16: null

indexing:
  workers: null
  batch_size: 500

prewarm:
  enabled: true
```

Change one axis at a time and keep the benchmark manifest. Unmeasured tuning
can exchange latency for memory or reduce retrieval quality.

## Device

| Value | Use |
|---|---|
| `auto` | Detect an available backend and retain fallback behavior |
| `cpu` | Highest portability and clearest diagnosis |
| `mps` | Apple Silicon, subject to unsupported operations |
| `cuda` | NVIDIA GPU with a compatible stack |

A fixed device needs validation on target hardware. FP16 reduces precision and
memory on compatible backends; `null` leaves the decision to runtime.

## Batch size and workers

`indexing.batch_size` affects peak memory and backend call count.
`indexing.workers` affects parser concurrency only. Changing both together
makes cause attribution difficult.

Signs of an oversized batch:

- out-of-memory failures or sustained swap;
- long pauses before visible progress;
- daemon timeouts;
- backend instability.

Signs of excessive workers:

- saturated storage;
- CPU spent on context switching;
- memory growth without throughput improvement.

## Candidates and reranking

`search.candidates`, `candidates_multiplier`, and `candidates_max` define the
pool before reranking. A larger pool may improve recall and costs more. Measure
latency and retrieval quality against a labeled query set.

## Full-text search

`fts.language: null` disables language-specific stemming and stop-word removal,
while retaining lowercase and accent folding for multilingual matching. A
specific language enables its analyzer. Rebuild FTS and compare the same query
set before retaining a change.

## Chunking

Larger chunks preserve more context and send more text to each embedding.
Overlap helps passages at chunk boundaries while increasing index size and
repetition. Compare by format and note type, never by one query alone.

## ANN

`vector_index.min_chunks` avoids an approximate index on small datasets.
`num_sub_vectors` must be compatible with embedding dimension and index type.
The schema rejects incompatible `IVF_PQ` combinations before indexing.
`vector_index_status` reports effective state.

## Prewarm and cache

Prewarming has memory guardrails. Disable it for short-lived processes or
contended machines. Query caches help repetition; a cold-state benchmark starts
in a new process.

## Measurement protocol

Follow [benchmarking](../performance/benchmarking.md) and record correctness,
recall, latency, and memory. A setting becomes a recommendation only after
repetition in representative environments.
