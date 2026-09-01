# Documentation

This directory separates user workflows, public contracts, operations, design
decisions, security, and reproducible performance work. Behavioral claims
should point to code or tests. Performance numbers require the environment and
sample information defined by the benchmark protocol.

## Start here

1. [Installation](operation/installation.md)
2. [YAML configuration](config/yaml.md)
3. [MCP tool catalog](api/tools.md)
4. [Threat model](security/threat-model.md)

## Operations

| Task | Document |
|---|---|
| Install and index | [operation/installation.md](operation/installation.md) |
| Keep models in a daemon | [daemon-setup.md](daemon-setup.md) |
| Diagnose a failure | [operation/troubleshooting.md](operation/troubleshooting.md) |
| Observe health and metrics | [operation/monitoring.md](operation/monitoring.md) |

## Configuration

| Topic | Document |
|---|---|
| Canonical file and precedence | [config/yaml.md](config/yaml.md) |
| Environment variables | [config/variables.md](config/variables.md) |
| Paths and local data | [config/paths.md](config/paths.md) |
| Hardware-aware tuning | [config/tuning.md](config/tuning.md) |

## MCP reference

- [Complete catalog](api/tools.md)
- [Search](api/tools-search.md)
- [CRUD and frontmatter](api/tools-crud.md)
- [Indexing](api/tools-indexing.md)
- [Navigation](api/tools-navigation.md)
- [Graph](api/tools-graph.md)
- [System](api/tools-system.md)
- [Resources](api/tools-resources.md)
- [Types](api/types.md)
- [Errors](api/errors.md)

The catalog must declare `43 tools` and `6 resources`. The publication check
derives both counts from decorators in `src/vault_search/server/`.

## Architecture

- [Overview](architecture/overview.md)
- [Module map](architecture/modules.md)
- [Diagrams](architecture/diagrams.md)
- [Decision records](architecture/decisions.md)

### Architecture decision records

- [ADR-0001: the vault is the source of truth](architecture/adr/0001-vault-as-source-of-truth.md)
- [ADR-0002: local model daemon](architecture/adr/0002-local-model-daemon.md)
- [ADR-0003: canonical YAML configuration](architecture/adr/0003-canonical-configuration.md)
- [ADR-0004: evidence-based performance claims](architecture/adr/0004-performance-evidence.md)

## Features

- [File formats](features/file-formats.md)
- [Frontmatter schema](features/frontmatter-schema.md)
- [UUID v7](features/uuid-system.md)
- [Link index](features/link-index.md)
- [Faceted search](features/faceted-search.md)
- [External enrichment](features/ai-enrichment.md)

## Performance

- [Measurement protocol](performance/benchmarking.md)
- [Indexing](performance/indexing.md)
- [Cache](performance/cache.md)
- [Auxiliary catalog](performance/catalog.md)
- [Prewarming](performance/prewarm.md)
- [Instrumentation](performance/metrics.md)
- [Implemented optimizations](performance/optimizations.md)

Performance documents explain mechanisms. Treat numeric results as a baseline
only when they include the environment required by the protocol.

## Development and maintenance

- [Testing strategy](development/testing.md)
- [Release process](development/release-checklist.md)
- [Security policy](../SECURITY.md)
- [Contributing](../CONTRIBUTING.md)
- [Changelog](../CHANGELOG.md)

## Documentation contract

A change to a tool, resource, configuration key, security behavior, or
operational command updates its documentation in the same pull request. Run:

```bash
uv run python scripts/check_publication.py
```

The check validates local links, public placeholders, personal paths, unsafe
commands, common secret patterns, distribution contents, and MCP registry
counts. It complements human review; it is not a proof that every private value
has been found.
