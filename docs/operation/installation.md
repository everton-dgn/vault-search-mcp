# Installation

## Current support

| Item | Support |
|---|---|
| Python | 3.14 or newer |
| Environment manager | `uv` with `uv.lock` |
| MCP server | Any client with `stdio` transport |
| Daemon | macOS launchd and user-level Linux systemd |
| Windows | Core lacks CI evidence; daemon has no installer |

## 1. Install the locked environment

```bash
git clone https://github.com/everton-dgn/vault-search-mcp.git
cd vault-search-mcp
uv sync --locked
```

`--locked` prevents silent lockfile changes. The command installs the editable
package and exposes `vault-search`, `vault-search-config`, and
`vault-search-daemon`.

## 2. Configure a vault

```bash
cp config.example.yaml config.yaml
```

Edit `paths.vault_path`. A relative path uses the directory of `config.yaml`.

```yaml
paths:
  vault_path: "vaults/obsidian_vault"
  data_dir: "data"
```

Or use an operational override:

```bash
export VAULT_SEARCH_VAULT_PATH="$PWD/vaults/obsidian_vault"
```

Local configuration, `data/`, and vault contents are ignored by Git. Never use
a real vault as a test fixture.

## 3. Validate configuration

```bash
uv run vault-search-config
```

Success exits with code zero and prints only
`vault-search configuration: ok`. Failure prints a sanitized type and reference
without values, tracebacks, or resolved paths. The equivalent module entry point
is `uv run python -m vault_search config`.

## 4. Build the index

```bash
uv run python -m vault_search.core.indexer
```

The index is stored below `paths.data_dir`. A first run may download models;
duration and disk use depend on platform, cache, and resolved versions.

Optional modes:

```bash
# Fail unless the daemon is healthy.
uv run python -m vault_search.core.indexer --require-daemon

# Wait for up to 60 seconds.
uv run python -m vault_search.core.indexer --wait-daemon 60
```

## 5. Start MCP

```bash
uv run vault-search
# Equivalent module entry point:
uv run python -m vault_search
```

The server uses `stdio`. Logs and banners must not enter the protocol channel.
The client launches the process with the repository root as its working
directory.

```json
{
  "mcpServers": {
    "vault-search": {
      "command": "uv",
      "args": ["run", "vault-search"]
    }
  }
}
```

When a client starts elsewhere, use its native `cwd` or project-directory
option. Do not copy absolute paths from another machine.

## 6. Verify the integration

From the MCP client:

1. list tools and resources;
2. run `health_check`;
3. run `vault_stats`;
4. search for a synthetic phrase.

The expected registry contains 43 tools and 6 resources.

## Optional daemon

Install the daemon after local indexing works:

```bash
# macOS
./scripts/install-daemon.sh

# Linux
./scripts/install-daemon-linux.sh

curl --fail http://127.0.0.1:9847/health
```

For an interactive run, use `uv run vault-search-daemon` or
`uv run python -m vault_search daemon`. During warmup, `/health` returns 503.
Installers wait for `ready` and restore the previous service if the deadline
expires. Read [the daemon guide](../daemon-setup.md) before changing host or
service behavior.

## Optional OCR

PyMuPDF reads native PDF text. Image-only documents require system Tesseract.

```bash
# macOS
brew install tesseract

# Debian and Ubuntu
sudo apt install tesseract-ocr tesseract-ocr-eng
```

Confirm available languages:

```bash
tesseract --list-langs
```

Set `pdf.ocr_languages` only to installed Tesseract language identifiers.

## Development environment

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/vault_search
uv run pytest -m "not slow" --cov=vault_search --cov-report=term \
  --cov-fail-under=65
uv run python scripts/check_publication.py
uv build
```

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for security, documentation, and
pull-request contracts.
