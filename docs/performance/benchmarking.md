# Reproducible benchmarking

## Principle

Performance is environment-specific. A latency value without hardware, dataset,
cache state, and sample count is not a project baseline.

## Separate measurements

1. `benchmark_search` measures the configured search path in the server.
2. Initial indexing measures scanning, parsing, chunking, embeddings, and index
   persistence.

Publish them separately. Warm-cache search does not describe first-run cost.

## Minimum manifest

```yaml
project:
  version: "0.1.0"
  commit: "<tested-commit>"
environment:
  os: "<system-and-version>"
  python: "3.14.x"
  cpu: "<model>"
  ram_bytes: 0
  device: "cpu"
dataset:
  source: "synthetic fixture"
  notes: 0
  chunks: 0
  index_bytes: 0
runtime:
  daemon: false
  model_cache: "cold"
  index_cache: "cold"
sample:
  warmups: 0
  runs: 0
```

Replace every placeholder before publication. This template alone is not
evidence.

## Search

From an MCP client, call `benchmark_search` with synthetic queries representing
exact terms, paraphrases, acronyms, and filters. Record each configuration
separately:

- semantic or hybrid retrieval;
- `top_k` and candidate count;
- reranker state;
- daemon or in-process models;
- cold or warm caches.

Use a warmup when measuring warm state. Publish median, p95, and sample count.
An average alone hides the tail.

## Indexing

Generate a versioned synthetic vault instead of copying personal notes. Record
size and format distributions. Before a cold run, move only confirmed
rebuildable artifacts through a recoverable mechanism.

```bash
time uv run python -m vault_search.core.indexer
```

For comparisons, keep lockfile, model, device, dataset, and cache state fixed.

## Report contract

| Field | Required |
|---|---|
| Commit, lockfile, and Python version | yes |
| Hardware, device, and precision | yes |
| Note count, chunk count, and index size | yes |
| Warmups, samples, median, and p95 | yes |
| Exact command or MCP payload | yes |
| Raw observations or result file | yes |
| Interpretation and limitations | yes |

## Public baseline state

No release baseline currently follows this complete protocol. Older numbers
without a manifest are historical observations, not comparisons or promises.
