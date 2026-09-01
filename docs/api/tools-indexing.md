# Indexing tools

These six tools operate on the derived LanceDB index. They never replace vault
backups. `reindex_vault`, `reindex_note`, `sync_vault`, and `compact_index`
change derived data. `vault_stats` and `vector_index_status` are read-only.

## `vault_stats`

```python
vault_stats() -> dict
```

An empty index returns:

```python
{
    "total_chunks": 0,
    "unique_notes": 0,
    "last_modified": None,
}
```

In use, counts and timestamps reflect local state at call time.

## `reindex_vault`

```python
reindex_vault(dry_run: bool = False, require_daemon: bool = False) -> dict | str
```

Builds chunks, links, and aliases in staging tables. A parsing or commit failure
preserves the previous index. New tables become active only after the complete
set has been processed.

Start with:

```python
reindex_vault(dry_run=True)
```

The dry run scans files and returns `would_index`, extension distribution, and
calculated batch size. It does not parse files, generate chunks, load models,
or change the index. Its output is not an estimate of duration or future chunk
count.

A real run reports `status`, `total_notes`, `total_chunks`, and
`duration_seconds`. Additional fields describe links, aliases, vector-index
state, parsing errors, or preservation of the old index.

With `require_daemon=True`, the operation waits up to 30 seconds and returns a
sanitized error if the daemon remains unavailable. Dry runs do not require a
model backend.

## `reindex_note`

```python
reindex_note(path: str) -> dict | str
```

Updates one note incrementally. The path must be relative and use an enabled
extension. If the file disappeared, stale index rows are removed.

The result includes `status` and `chunks_indexed`. Status values cover update,
removal, empty content, invalid path or extension, parse errors, and write
failures.

## `sync_vault`

```python
sync_vault(dry_run: bool = False, require_daemon: bool = False) -> dict
```

Compares the current scan with indexed `note_path` and `modified_at` values to
detect new, modified, and deleted files.

```python
{
    "vault_files": 0,
    "indexed_files": 0,
    "new_files": 0,
    "modified_files": 0,
    "deleted_files": 0,
    "synced": 0,
}
```

The values above illustrate an empty vault and index. `dry_run=True` reports
observed differences, keeps `synced` at zero, and performs no reindex. A real
run handles deletions before new and modified files.

`require_daemon` follows the `reindex_vault` contract. The environment variable
`VAULT_SEARCH_REQUIRE_DAEMON=1` also enforces it.

## `compact_index`

```python
compact_index() -> dict | str
```

Requests LanceDB table optimization after many incremental updates. Exact
statistics depend on the backend version. Run it outside a concurrent write
window and inspect the result before automating follow-up decisions.

## `vector_index_status`

```python
vector_index_status() -> dict
```

Returns `exists`, `auto_create_enabled`, `threshold`, `total_chunks`, and
`would_create`. `would_create` describes the configured rebuild decision; this
read does not create an ANN index.

## Operational choice

| Situation | Tool |
|---|---|
| One note changed | `reindex_note` |
| Changes occurred while the server was stopped | `sync_vault(dry_run=True)`, then `sync_vault()` |
| Parsing or embedding configuration changed | `reindex_vault(dry_run=True)`, then `reindex_vault()` |
| Many incremental updates accumulated | `compact_index` |
| ANN state needs inspection | `vector_index_status` |

## Recovery

When a full rebuild returns `previous_index_preserved: true`, the old index
remains canonical. Correct the parsing or write error and retry. After an
interrupted process, inspect `status` and `timed_out` before deciding the next
action.

See [troubleshooting](../operation/troubleshooting.md) for daemon, permission,
and missing-index failures.
