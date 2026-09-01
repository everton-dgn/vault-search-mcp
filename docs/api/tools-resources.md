# MCP resources

The server registers six read-only resources. Each reflects local vault,
catalog, or index state at call time.

| Registered URI | Source | Current bound |
|---|---|---|
| `vault://stats` | LanceDB | no pagination |
| `vault://folders` | SQLite catalog | all known folders |
| `vault://notes` | SQLite catalog | first 5,000 entries |
| `vault://notes/{path*}` | `.md` file | one note |
| `vault://search/recent` | SQLite catalog | 50 notes from seven days |
| `vault://tags` | LanceDB | all known tags |

## `vault://stats`

```python
{
    "uri": "vault://stats",
    "type": "statistics",
    "data": {
        "total_chunks": 0,
        "unique_notes": 0,
        "last_modified": None,
    },
}
```

The zero values represent an empty index.

## `vault://folders`

```python
{
    "uri": "vault://folders",
    "type": "folder_tree",
    "total_folders": 2,
    "tree": {"projects": {"atlas": {}}},
}
```

The synthetic example documents shape only.

## `vault://notes`

Lists the first 5,000 catalog entries with `path`, `title`, `folder`, and
`modified_at`.

```python
{
    "uri": "vault://notes",
    "type": "note_list",
    "total": 0,
    "returned": 0,
    "limit": 5000,
    "has_more": False,
    "notes": [],
}
```

`total` is the complete catalog count. `returned` is the snapshot size and
`has_more` reports truncation. This resource has no arguments, cursor, or
`offset`. Use `list_notes` for pagination or filtering.

## `vault://notes/{path*}`

The wildcard preserves subfolders in a relative path:

```text
vault://notes/projects/atlas/decision.md
```

Only `.md` content can be read in full. The result includes `title`, `content`,
`frontmatter`, `modified_at`, and `size_bytes`. Invalid paths, unreadable
extensions, and missing notes return public errors.

## `vault://search/recent`

Returns at most 50 notes modified during the last seven days.

```python
{
    "uri": "vault://search/recent",
    "type": "recent_notes",
    "days": 7,
    "total": 0,
    "notes": [],
}
```

The window is fixed. Use `get_recent_notes` to choose days, folder, or limit.

## `vault://tags`

```python
{
    "uri": "vault://tags",
    "type": "tag_stats",
    "total_unique_tags": 0,
    "tags": [],
}
```

## Consistency and errors

Catalog-backed resources can lag behind an external filesystem change until
the watcher reconciles it. Wait for the watcher or call `sync_vault`. Internal
failures use the sanitized public envelope; details remain in local logs.

See [catalog behavior](../performance/catalog.md) and [API errors](errors.md).
