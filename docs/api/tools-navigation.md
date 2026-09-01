# Navigation tools

Ten tools navigate links, recency, tags, folders, random notes, and daily notes.
Numeric payloads on this page are synthetic fixtures that document shape only.

## `get_backlinks`

```python
get_backlinks(path: str, include_context: bool = True) -> list[dict] | str
```

Returns indexed links that point to the requested note.

```python
[
    {
        "path": "projects/roadmap.md",
        "title": "Roadmap",
        "link_type": "wikilink",
        "link_target": "example-note",
        "context": "...related to [[example-note]]...",
    }
]
```

`link_type` can be `wikilink`, `markdown`, or `embed`. Context is omitted when
`include_context=False`.

## `get_outlinks`

```python
get_outlinks(path: str) -> dict | str
```

```python
{
    "path": "projects/example.md",
    "wikilinks": [
        {"target": "roadmap", "resolved": True, "resolved_path": "roadmap.md"}
    ],
    "markdown_links": [{"target": "docs/manual.md", "resolved": True}],
    "embeds": [{"target": "diagram.png", "resolved": False}],
    "external": [{"url": "https://example.com"}],
    "total": 4,
    "broken_count": 1,
}
```

## `find_broken_links`

```python
find_broken_links(folder: str | None = None, limit: int = 100) -> dict | str
```

Finds indexed links without a resolved note target. `limit` is capped at 500.

```python
{
    "total_broken_links": 5,
    "notes_with_broken_links": 3,
    "returned_notes": 1,
    "has_more": True,
    "notes": [
        {
            "path": "project.md",
            "title": "Project",
            "broken_links": [
                {"target": "missing", "type": "wikilink", "context": "..."}
            ],
        }
    ],
}
```

The totals cover the complete filter, while `notes` respects `limit`.
`has_more` reports truncation. There is no `offset`.

## `find_orphan_notes`

```python
find_orphan_notes(folder: str | None = None, limit: int = 100) -> dict | str
```

Finds catalog notes without known incoming or outgoing links. The limit is
capped at 500.

```python
{
    "total_notes": 500,
    "total_orphans": 42,
    "orphan_percentage": 8.4,
    "returned_notes": 1,
    "has_more": True,
    "notes": [
        {"path": "isolated.md", "title": "Isolated note", "modified_at": "2026-01-01"}
    ],
}
```

Totals cover the complete filter. The note array is bounded and has no offset.

## `link_stats`

```python
link_stats(limit: int = 50) -> dict | str
```

Returns global link counts, resolution rate, unique sources and targets, and
bounded `most_referenced` and `most_outlinks` lists.

```python
{
    "total_links": 1234,
    "total_resolved": 1100,
    "total_broken": 34,
    "total_external": 100,
    "resolution_rate": 97.0,
    "unique_sources": 200,
    "unique_targets": 150,
    "most_referenced": [{"path": "hub.md", "backlinks": 50}],
    "most_outlinks": [{"path": "index.md", "outlinks": 30}],
}
```

## `get_recent_notes`

```python
get_recent_notes(
    days: int = 7,
    limit: int = 20,
    folder: str | None = None,
) -> list[dict] | str
```

`days` is capped at 365 and `limit` at 100. Each item contains `path`, `title`,
`modified_at`, `folder`, and `days_ago`.

## `tag_stats`

```python
tag_stats(limit: int = 50, folder: str | None = None) -> dict | str
```

Returns `total_tags`, `total_notes_with_tags`, and a frequency-sorted `tags`
array. `limit` is capped at 500.

```python
{
    "total_tags": 3,
    "total_notes_with_tags": 8,
    "tags": [
        {"tag": "project", "count": 5},
        {"tag": "architecture", "count": 3},
    ],
}
```

## `folder_tree`

```python
folder_tree(include_counts: bool = True, max_depth: int = 10) -> dict | str
```

Returns `total_folders`, `total_notes`, and a hierarchical `tree`. Counts use
the reserved `_count` key. `max_depth` is capped at 50.

```python
{
    "total_folders": 3,
    "total_notes": 12,
    "tree": {
        "projects": {
            "_count": 8,
            "web": {"_count": 5},
            "mobile": {"_count": 3},
        }
    },
}
```

## `random_note`

```python
random_note(
    folder: str | None = None,
    extension: str | None = None,
) -> dict | str
```

Returns one catalog item with `path`, `title`, `folder`, `extension`,
`modified_at`, and `size_bytes`. Filters are optional.

## `daily_note`

```python
daily_note(date: str | None = None, folder: str = "daily") -> dict | str
```

`date` is an ISO `YYYY-MM-DD` value and defaults to the current local date.
`folder` is validated inside the vault.

Existing note:

```python
{
    "exists": True,
    "path": "daily/2026-01-15.md",
    "title": "2026-01-15",
    "folder": "daily",
    "date": "2026-01-15",
    "modified_at": "2026-01-15T10:30:00",
    "size_bytes": 1024,
}
```

Missing note:

```python
{
    "exists": False,
    "expected_path": "daily/2026-01-15.md",
    "date": "2026-01-15",
}
```
