# Local model daemon

The daemon keeps embedding and reranking models loaded between MCP processes.
It is optional, local, and separate from the vault and its derived index.

## Security boundary

The internal HTTP protocol has no authentication. Configuration, server, and
client accept loopback addresses only. Use `127.0.0.1`, do not publish the port,
and do not place a reverse proxy in front of it. Remote access remains outside
the contract until TLS, authentication, quotas, and a dedicated threat model
exist. The client ignores environment proxy settings and refuses HTTP redirects,
so note content cannot leave the selected loopback endpoint through either path.

## Installation

First validate `uv run python -m vault_search.core.indexer` and local
configuration.

```bash
# macOS
./scripts/install-daemon.sh

# Linux with user-level systemd
./scripts/install-daemon-linux.sh
```

The installers:

- locate `uv` and the project root;
- resolve the project environment and register its daemon executable directly;
- read host and port from configuration resolved in the project root, unless an
  explicit absolute `VAULT_SEARCH_CONFIG` selects another file;
- convert configured path overrides to absolute paths and copy only the documented
  daemon environment allowlist into the service definition;
- preserve an existing service definition before replacement;
- register a user service;
- require the health response to be `ready` and identify the PID managed by the
  service manager;
- restore the prior definition if activation or health validation fails.

Health validation waits up to 300 seconds by default, including a first model
download. Increase the window without changing YAML:

```bash
VAULT_SEARCH_DAEMON_STARTUP_TIMEOUT=900 ./scripts/install-daemon.sh
```

Use the same variable with `install-daemon-linux.sh` on Linux.

The installed service captures these nonempty variables at installation time:
`VAULT_SEARCH_CONFIG`, `VAULT_SEARCH_VAULT_PATH`, `VAULT_PATH`,
`VAULT_SEARCH_DATA_DIR`, `VAULT_SEARCH_WRITE_LOCK_TIMEOUT_SECONDS`,
`VAULT_SEARCH_ENV`, `VAULT_SEARCH_LOG_LEVEL`, and
`PYTORCH_ENABLE_MPS_FALLBACK`. Rerun the installer after changing one of them.
The complete shell environment is never copied.

Run interactively without installing a service:

```bash
uv run vault-search-daemon
# Equivalent module entry point:
uv run python -m vault_search daemon
```

## Verification

```bash
# These URLs use default configuration.
curl --fail --silent --show-error http://127.0.0.1:9847/health
curl --fail --silent --show-error http://127.0.0.1:9847/stats
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Model identity and readiness |
| GET | `/stats` | Aggregated operational state |
| POST | `/embed/queries` | Query embeddings |
| POST | `/embed/corpus` | Chunk embeddings |
| POST | `/rerank` | Reranking scores |

Normal operation uses MCP tools. These endpoints exist for internal integration
and local diagnosis.

`/health` returns HTTP 200 only in `ready` state. During warmup it returns HTTP 503.
A terminal partial or complete warmup failure closes the process with a
failure status so launchd or systemd can retry it. Inference endpoints reject
calls while the daemon is not ready. Stop it through the service manager or a
local signal. There is no HTTP shutdown endpoint.

The response includes the daemon process ID so installers and operators can
reject a healthy response from an older process that happens to own the same
port:

```json
{
  "status": "ready",
  "pid": 12345,
  "models_loaded": true
}
```

## macOS

```bash
launchctl print "gui/$(id -u)/com.vault-search.daemon"
tail -f "$HOME/Library/Logs/vault-search-daemon.log"
./scripts/uninstall-daemon.sh
```

## Linux

```bash
systemctl --user status vault-search-daemon
journalctl --user -u vault-search-daemon -f
./scripts/uninstall-daemon-linux.sh
```

Uninstallers require `trash` or `trash-put` and preserve logs. On Linux,
`trash-put` is commonly supplied by `trash-cli`. They do not remove the vault
or index.

## Client modes

| Setting | Behavior |
|---|---|
| `daemon.auto_use: true` | Use a healthy daemon when available |
| `VAULT_SEARCH_REQUIRE_DAEMON=1` | Fail instead of loading models locally |
| `--wait-daemon N` | Indexer waits up to N seconds |
| `--wait-daemon 0` | Indexer waits without a fixed deadline |

An open socket is not health proof. The normal client validates response shape
and readiness. Installation adds an exact PID check against systemd or launchd.
Both `127.0.0.1` and `::1` are supported; the daemon selects the matching socket
family.

## Failure handling

### Registered service without a response

1. inspect sanitized logs;
2. call `/health` with a timeout;
3. confirm the expected process owns the port;
4. restart the service once;
5. use local models only when policy permits.

### Port conflict

Never terminate an unknown process automatically. Identify the owner and choose
another port in `config.yaml`, then update the client and service together.

### Memory use

Consumption depends on model, backend, precision, and version. Measure it in the
target environment and retain the [benchmark manifest](performance/benchmarking.md).
