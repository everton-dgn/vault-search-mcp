# Contributing

Contributions must preserve three properties: the vault remains primary, write
operations stay explicit, and performance claims remain reproducible.

## Before you start

1. Look for an issue or discussion that describes the problem.
2. Report security defects through the private process in [SECURITY.md](SECURITY.md).
3. Never attach real notes, machine paths, credentials, index databases, or raw
   unsanitized logs.
4. Changes to MCP names, arguments, or return values require documentation and
   contract tests in the same pull request.

## Local environment

Requirements are Python 3.14 or newer and `uv`. Changes to daemon shell scripts
also require ShellCheck.

```bash
uv sync --locked
cp config.example.yaml config.yaml
```

Use a synthetic vault for development. `config.yaml`, `data/`, and local vault
contents are excluded from version control.

## Workflow

Create a focused branch with one of these prefixes: `feat/`, `fix/`,
`refactor/`, `perf/`, `docs/`, `test/`, or `chore/`. Use a lowercase slug, for
example `fix/daemon-health-check`.

Commits follow Conventional Commits:

```text
fix(indexer): preserve the active index during rebuild
docs(config): document environment precedence
```

Configure Git with a public display name and a no-reply address. The
publication gate rejects personal email addresses from author and committer
metadata.

Keep broad refactors separate from functional fixes. Do not commit generated
artifacts, vault content, or local configuration.

## Local gates

Run the narrowest validation that covers the change, then the standard suite:

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
bash -n scripts/*.sh && shellcheck scripts/*.sh
uv run mypy src/vault_search
uv run pytest -m "not slow" --cov=vault_search --cov-report=term \
  --cov-fail-under=65
uv run python scripts/check_publication.py
uv build
uv run python scripts/check_publication.py --require-dist
```

Tests marked `slow` may download and load models. State in the pull request
whether they ran, on what hardware, and with which command.

## Code standards

- Public functions have type hints.
- Docstrings describe side effects, return shapes, and relevant failure modes.
- Logs exclude note bodies, full queries, and absolute paths.
- Note writes are atomic when the filesystem permits it.
- Client-supplied paths are resolved and verified inside the vault.
- Deletion moves data to trash. Never use permanent-removal APIs.
- A new dependency requires motivation, distribution impact, and a considered
  alternative.

## Documentation and performance

Document behavior observed in code. Label a target as a target. A benchmark
includes environment, dataset, cache state, sample count, and percentiles as
defined in [docs/performance/benchmarking.md](docs/performance/benchmarking.md).

The publication script validates relative links and public registry counts. If
a synthetic fixture must contain a forbidden pattern, add
`publication-check: synthetic-fixture` on that exact line. The exemption is
line-scoped and must never hide a real value.

## Pull requests

A pull request states:

- the problem and user impact;
- the contract that changed;
- risks and the rollback path;
- commands run and their results;
- relevant validation that did not run;
- the documentation update, or a concrete reason it is unnecessary.

Maintainers may request a split when a change becomes hard to review or revert.
