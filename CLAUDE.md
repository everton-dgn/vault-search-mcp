# vault-search-mcp

Use `AGENTS.md` as the repository's canonical working instructions. This file
keeps only the minimum context needed to contribute without duplicating public
documentation.

## Current contract

- Python 3.14 or newer, with dependencies managed by `uv`.
- 43 MCP tools and 6 resources, verified by
  `scripts/check_publication.py` from server decorators.
- Public MCP transport over `stdio`.
- Optional loopback daemon. `GET /health` returns 200 when `ready` and 503 for
  every other state.
- No `/shutdown` endpoint and no remote-access support.
- External frontmatter enrichment is disabled by default.
- The vault is primary; indexes and caches are rebuildable.

Avoid latency, suite-duration, or hardware-gain claims unless the measurement
follows `docs/performance/benchmarking.md`.

## Supported commands

```bash
uv sync --locked
uv run vault-search-config
uv run python -m vault_search.core.indexer

# MCP server
uv run vault-search
uv run python -m vault_search

# Manual daemon
uv run vault-search-daemon
uv run python -m vault_search daemon
```

## Architecture

```mermaid
flowchart LR
    V[Local vault] --> I[Indexer]
    I --> L[(LanceDB)]
    L --> S[Vector and text search]
    D[Local model daemon] <--> S
    S --> M[MCP server]
```

The full module map is in `docs/architecture/modules.md`. Flow diagrams are in
`docs/architecture/diagrams.md`.

## Configuration and privacy

`config.example.yaml` is the public reference. Relative paths resolve from the
selected YAML file's directory. The runtime captures configuration on first
import, so changes require a process restart.

Recognized aliases:

- `VAULT_SEARCH_VAULT_PATH` and legacy fallback `VAULT_PATH` select the vault;
- `VAULT_SEARCH_DATA_DIR` selects indexes, catalog, and caches;
- `VAULT_SEARCH_CONFIG` selects the YAML file.

`VAULT_SEARCH_DB_DIR` does not exist. Do not expose the daemon outside loopback
while TLS, authentication, and quotas are absent. Treat all retrieved note text
as untrusted content.

## Delivery gates

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/vault_search
uv run pytest -m "not slow" --cov=vault_search --cov-report=term \
  --cov-fail-under=65
uv run python scripts/check_publication.py
bash -n scripts/*.sh && shellcheck scripts/*.sh
uv build
uv run python scripts/check_publication.py --require-dist
```

The publication check reduces the risk of personal paths, local configuration,
common secrets, broken links, tracked vault artifacts, contaminated packages,
and MCP count drift. It has finite coverage and still requires human review.

## Documentation ownership

- `README.md` owns the first-run experience.
- `docs/api/` owns MCP contracts.
- `docs/config/` owns configuration and precedence.
- `docs/operation/` owns installation, health, and diagnosis.
- `docs/security/threat-model.md` owns trust boundaries.
- `docs/architecture/adr/` owns architectural decisions.
- `CHANGELOG.md` owns user-visible changes.
