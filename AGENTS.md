# vault-search-mcp

Local MCP server for vector, text, and graph search across Obsidian vaults and
other Markdown knowledge bases. The project is alpha software and requires
Python 3.14 or newer.

## Canonical sources

- `config.example.yaml` defines the public configuration schema.
- Decorators under `src/vault_search/server/` define the MCP registry.
- `README.md` owns the first-run journey.
- `docs/README.md` indexes contracts, operations, security, and decisions.
- `scripts/check_publication.py` compares the documentation with the 43 tools
  and 6 resources discovered in code.

Do not publish test duration, latency, hardware gains, or transfer size without
a reproducible measurement that follows `docs/performance/benchmarking.md`.

## Operational contract

- The vault is primary. LanceDB, the catalog, and caches are rebuildable.
- The public MCP transport is `stdio`.
- The optional daemon accepts loopback hosts only.
- `GET /health` returns HTTP 200 in `ready` state and HTTP 503 otherwise.
- There is no `/shutdown` endpoint.
- Remote access is unsupported while TLS, authentication, and quotas are absent.
- External frontmatter enrichment starts disabled. Enabling it requires
  explicit consent and a provider configuration.
- YAML configuration and legacy aliases are captured on first import. Restart
  the process after changing configuration.
- `delete_note` moves notes into the vault's `.trash` directory.

## Public commands

```bash
uv sync --locked
uv run vault-search-config

# Indexing
uv run python -m vault_search.core.indexer

# MCP server
uv run vault-search
uv run python -m vault_search

# Manual daemon
uv run vault-search-daemon
uv run python -m vault_search daemon
```

## Package layout

```text
src/vault_search/
├── config/       # schema, loader, and configuration snapshots
├── core/         # indexing, retrieval, chunking, and models
├── crud/         # safe note reads and writes
├── daemon/       # local HTTP inference service
├── frontmatter/  # schema, validation, and optional enrichment
├── parsers/      # Markdown, MDX, TXT, PDF, and Canvas
├── server/       # MCP tools, resources, and lifecycle
├── utils/        # networking, logging, metrics, UUIDs, and shutdown
└── watching/     # filesystem events and incremental reindexing
```

Read `docs/architecture/modules.md` before expanding this map.

## Configuration

Copy `config.example.yaml` to `config.yaml`. Relative paths resolve from the
selected YAML file's directory.

Recognized path overrides:

- `VAULT_SEARCH_CONFIG` selects the YAML file;
- `VAULT_SEARCH_VAULT_PATH` overrides `paths.vault_path`;
- `VAULT_PATH` is the legacy vault alias;
- `VAULT_SEARCH_DATA_DIR` overrides `paths.data_dir`.

`VAULT_SEARCH_DB_DIR` is not recognized. The complete reference is in
`docs/config/variables.md`.

## Validation

Run the smallest gate that covers a change first. Before delivery, run:

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

mypy covers the complete Python package. The publication gate checks local
links, configuration artifacts, personal paths, common secret patterns,
tracked vault data, wheel and sdist contents, and MCP registry drift. Its
coverage is finite and does not replace human review.

## Change conventions

- Keep public docstrings and identifiers in English and preserve type hints.
- Load models through `ModelManager`.
- Treat retrieved vault content as untrusted data.
- Update documentation and tests with every contract change.
- Keep examples synthetic and free of personal names, machine paths, or secrets.
- Record the need and distribution impact of each new dependency.
- Never claim performance from a single local result.
