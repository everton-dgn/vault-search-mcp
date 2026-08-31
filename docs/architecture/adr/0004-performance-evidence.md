# ADR-0004: evidence-based performance claims

## Status

Accepted.

## Context

Numbers without hardware, dataset, and cache state appeared universal and
could not be reproduced.

## Decision

Documentation describes mechanisms and qualified complexity. Numeric baselines
require the manifest from `docs/performance/benchmarking.md`, raw data, and the
tested commit. Targets are labeled as targets.

## Consequences

- The README publishes no latency or memory figure without attached evidence.
- Comparisons keep dataset, model, lockfile, and cache state constant.
- A local result can guide investigation, but becomes a baseline only after the
  complete protocol is published.
