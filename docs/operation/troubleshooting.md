# Troubleshooting

## Short diagnostic path

```bash
uv --version
uv run python --version
uv sync --locked
uv run python scripts/check_publication.py
```

Then validate configuration, index, and daemon in that order. Sanitize every
output before sharing it.

## Configuration is not applied

1. Check `VAULT_SEARCH_CONFIG`.
2. Verify that the file exists and contains valid YAML.
3. Restart MCP and daemon processes because configuration is cached.
4. Inspect only the required fields from `get_config()` locally.

`VAULT_SEARCH_VAULT_PATH` overrides the vault. Legacy `VAULT_PATH` applies when
the modern variable is absent. `VAULT_SEARCH_DATA_DIR` overrides derived data.
They are captured on first import. `VAULT_SEARCH_DB_DIR` is not recognized.

## Vault is missing

```bash
uv run python -c "from vault_search.config.paths import VAULT_PATH; print(VAULT_PATH.exists())"
```

Do not publish the printed path. If the result is `False`, update
`paths.vault_path` or `VAULT_SEARCH_VAULT_PATH`.

Check the data directory without printing it:

```bash
uv run python -c "from vault_search.config.paths import DATA_DIR; print(DATA_DIR.exists())"
```

## Index is missing or empty

```bash
uv run python -m vault_search.core.indexer
```

Preserve the active generation when a rebuild fails. Do not discard the index
before understanding the failure. Move a confirmed rebuildable artifact to
trash before rebuilding:

```bash
trash data/vault_chunks.lance
uv run python -m vault_search.core.indexer
```

Resolve the exact target first. Vault content is never part of this cleanup.

## Daemon is unavailable

```bash
curl --fail --max-time 5 http://127.0.0.1:9847/health
```

If it fails:

- macOS: `launchctl print "gui/$(id -u)/com.vault-search.daemon"`;
- Linux: `systemctl --user status vault-search-daemon`;
- confirm host and port in YAML;
- remove `VAULT_SEARCH_REQUIRE_DAEMON=1` only when local fallback is acceptable.

An open port does not prove that the correct service is healthy.

## Search returns no result

1. Read `vault_stats`.
2. Confirm the extension is `.md`, `.mdx`, `.txt`, `.pdf`, or `.canvas`.
3. Check `indexing.ignored_folders`.
4. Run `sync_vault` in dry-run mode.
5. Compare semantic and hybrid search using a synthetic phrase.

## PDF has no text

Determine whether the file has a text layer. For a scanned image:

```bash
tesseract --version
tesseract --list-langs
```

Install every language declared by `pdf.ocr_languages`, then restart.

## MPS or CUDA fails

Temporarily use the portable setting:

```yaml
embedding:
  device: "cpu"
  use_fp16: false
```

If CPU works, record driver, backend, model, PyTorch version, and failing
operation before opening an issue.

## Tool or resource is missing

```bash
uv run python scripts/check_publication.py
```

The expected registry contains 43 tools and 6 resources. Drift indicates an
incomplete checkout, interrupted import, or stale documentation.

## A note write fails

Check extension, size, schema, and path containment with a synthetic fixture.
Do not weaken path validation or share real content.

`write_lock_timeout` means another cooperative writer held the lock past the
deadline. Reread before retrying. Set
`VAULT_SEARCH_WRITE_LOCK_TIMEOUT_SECONDS` from 0 through 300 seconds; invalid
values use five seconds.

`write_conflict` means the observed revision changed during the operation.
Reread and reconcile. On platforms without `fcntl`, separate processes do not
share the advisory lock.

## Opening a useful issue

Include:

- project version or commit;
- operating system and Python version;
- exact command;
- sanitized error;
- minimal synthetic fixture;
- whether the daemon was active;
- validations already run.

Use the private [security process](../../SECURITY.md) for vulnerabilities.
