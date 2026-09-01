# Testing and quality strategy

## Project gates

| Layer | Goal | Command |
|---|---|---|
| Lint | Source, tests, and Python scripts | `uv run ruff check src tests scripts` |
| Format | Source, tests, and Python scripts | `uv run ruff format --check src tests scripts` |
| Shell | Daemon installers and uninstallers | `bash -n scripts/*.sh && shellcheck scripts/*.sh` |
| Types | Complete Python package | `uv run mypy src/vault_search` |
| Unit | Rules without loading real models | `uv run pytest -m "not slow" --cov=vault_search --cov-fail-under=65` |
| ML integration | Models, index, and target environment | `uv run pytest -m slow` |
| Publication | Docs, privacy, Git tree, and distributions | `uv run python scripts/check_publication.py && uv build && uv run python scripts/check_publication.py --require-dist` |

ShellCheck is a development requirement for `scripts/*.sh` changes, not a
Python runtime dependency.

## Package type checking

mypy checks every source file. Project types model heterogeneous payloads;
exceptions for libraries without stubs stay scoped to declared external
modules in `pyproject.toml`. A blanket ignore must not hide package errors.

## Tests without real models

The standard suite uses synthetic fixtures and mocks at ML boundaries. It must
not download models, read a personal vault, call external services, or depend
on a previously installed daemon.

`publication-check: synthetic-fixture` exempts only the exact line containing a
deliberate synthetic forbidden pattern. Never place that marker beside a real
credential, address, or path.

When Git is available, publication checks inspect tracked files and reachable
history. They reject local configuration, vault data, generated artifacts, and
personal commit email addresses. Generic project identities, bots, and GitHub
no-reply addresses are allowed. With `--require-dist`, wheel and sdist contents
are checked without extracting archives.

```bash
uv run pytest -m "not slow" --cov=vault_search --cov-report=term \
  --cov-fail-under=65
```

For a localized change, run its focused test first and the standard suite next.

## Slow tests

Use `slow` when a test loads models, depends on hardware, or processes a volume
that cannot provide short feedback. Record:

- resolved model and version;
- device and precision;
- hardware and operating system;
- cold or warm cache state;
- exact command, total duration, and outcome.

## Coverage

Coverage identifies code without execution. It does not prove assertion quality
or external contracts.

```bash
uv run pytest --cov=vault_search --cov-report=term-missing -m "not slow"
```

CI requires at least 65% combined statement coverage. Do not turn one local
result into a permanent claim; report the current command output with its
runtime and environment when evidence matters.

## Minimum cases by boundary

### Paths and CRUD

- `..`, absolute paths, and null bytes;
- internal symlinks that point outside the vault;
- races between validation and persistence;
- failure before and after atomic replacement;
- preservation of original content after a failed write.

### Daemon

- closed port, unexpected listener, and invalid response;
- failure after a successful health probe;
- body, text-count, and text-length limits;
- connection and read timeout;
- rejection of non-loopback binding.

### Index

- empty rebuild and mid-rebuild failure;
- old generation available until new commit;
- ANN creation at the configured threshold;
- invalid configuration without silent fallback.

### MCP

- every decorator registered;
- argument types, defaults, and limits;
- sanitized errors;
- cancellation and shutdown.

## Baseline changes

Never update a snapshot or threshold only to make CI green. Explain the contract
change first and review its effect on consumers.
