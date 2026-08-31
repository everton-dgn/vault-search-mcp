# Environment variables

YAML holds product configuration. Environment variables select files, paths,
and operational modes.

| Variable | Value | Effect |
|---|---|---|
| `VAULT_SEARCH_CONFIG` | file path | Select YAML before root-level candidates |
| `VAULT_SEARCH_VAULT_PATH` | directory path | Override only `paths.vault_path` |
| `VAULT_PATH` | directory path | Legacy vault alias used only without the modern variable |
| `VAULT_SEARCH_DATA_DIR` | directory path | Override `paths.data_dir` for LanceDB, catalog, and cache |
| `VAULT_SEARCH_REQUIRE_DAEMON` | `1` or `0` | Reject or allow fallback to local models |
| `VAULT_SEARCH_WAIT_DAEMON` | seconds; `0` waits without a deadline | Wait for the daemon in the indexer |
| `VAULT_SEARCH_DAEMON_STARTUP_TIMEOUT` | positive seconds | Startup window used only by daemon installers |
| `VAULT_SEARCH_WRITE_LOCK_TIMEOUT_SECONDS` | number from 0 through 300 | Write-lock deadline; invalid input falls back to five seconds |
| `VAULT_SEARCH_ENV` | `production` or another value | Select logging format |
| `VAULT_SEARCH_LOG_LEVEL` | Python level name | Set log level |
| `PYTORCH_ENABLE_MPS_FALLBACK` | `1` or `0` | Allow fallback for unsupported MPS operations |

`VAULT_SEARCH_RUNNING_AS_DAEMON` is internal. The daemon entry point sets it;
operators should not.

## Examples

```bash
export VAULT_SEARCH_CONFIG="$PWD/config.yaml"
export VAULT_SEARCH_VAULT_PATH="$PWD/vaults/obsidian_vault"
export VAULT_SEARCH_DATA_DIR="$PWD/data"
export VAULT_SEARCH_LOG_LEVEL="INFO"
```

Require the daemon:

```bash
export VAULT_SEARCH_REQUIRE_DAEMON=1
uv run python -m vault_search.core.indexer --wait-daemon 60
```

## Security rules

- Never place a secret in a variable documented in the repository.
- Never print the complete environment in logs or reports.
- Sanitize path values before sharing diagnostics.
- Restart processes after changing variables.

`VAULT_PATH` and `VAULT_SEARCH_DATA_DIR` are captured on first import of
`vault_search.config.paths`. Prefer `VAULT_SEARCH_VAULT_PATH` for the vault.
`VAULT_SEARCH_DB_DIR` is not recognized and has no effect.

`VAULT_SEARCH_WRITE_LOCK_TIMEOUT_SECONDS` is captured on first import of the
locking module. Restart after changing it.

The daemon installers persist a documented allowlist rather than the complete
environment. See [Local model daemon](../daemon-setup.md#installation) for the
captured variables and reinstall the service after changing one.
