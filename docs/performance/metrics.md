# Metrics

## Principle

An operational metric helps locate failure. It becomes an SLO only after an
environment baseline and an explicit decision.

## Sources

| Source | Content |
|---|---|
| `system_stats` | caches, components, and internal metrics |
| `vault_stats` | notes, chunks, and index update state |
| `health_check` | aggregate health |
| `benchmark_search` | local search samples |
| daemon `/stats` | local model state |
| structured logs | event, duration, count, and failure code |

## Safe dimensions

Use operation, status, component, duration, and count. Avoid query, note
content, title, tag, UUID, and absolute path as labels. High-cardinality values
increase cost and can expose data.

## Distributions

Keep enough latency observations for median, p95, and sample count. Separate:

- cold and warm caches;
- daemon and in-process models;
- semantic and hybrid search;
- full and incremental indexing;
- success, timeout, cancellation, and failure.

## Baseline-derived alerts

Useful conditions include:

- repeated health-check failure;
- p95 departure from the same environment's baseline;
- hit-rate drop after a key change;
- enrichment queue stops advancing;
- index count diverges from the vault;
- daemon restart frequency increases.

Do not copy a threshold from different hardware. Record the calculation and
window.

## Export

The project has no Prometheus or OpenTelemetry exporter. A future integration
must preserve stable names, bounded cardinality, and private-by-default labels.
Until then, collect tools and logs locally.
