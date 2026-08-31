# SQLite catalog

The catalog stores metadata needed for listing, folders, and recent notes. It
avoids scanning the complete vault for each query but remains derived. Tag
statistics come from LanceDB, not the SQLite catalog.

## Stored data

- relative path and folder;
- title derived from the filename;
- extension, size, and `mtime_ns`.

Tags, UUIDs, and other frontmatter fields live in LanceDB.

SQLite indexes support folder, extension, and modification filters. Real cost
includes index lookup and returned rows, so `list_notes` is not described as
constant-time.

## Lifecycle

1. Startup creates the schema and reconciles the vault.
2. The watcher upserts or deletes after processed events.
3. A reconciliation thread corrects missed events.
4. If the catalog is unavailable, `list_notes` can scan the filesystem.

## Consistency

The catalog can briefly lag behind the vault. A consumer requiring immediate
state after a write should use that operation's result or a direct read rather
than assume instant reconciliation.

## Recovery

After confirming corruption, stop processes and move only the rebuildable
catalog to trash:

```bash
trash data/notes_catalog.db
```

Restart to rebuild it. Vault content is never part of this cleanup.

## Evidence

Compare catalog and fallback against the same synthetic vault, filters, limit,
and cache state. Record median, p95, and returned rows using
[benchmarking.md](benchmarking.md).
