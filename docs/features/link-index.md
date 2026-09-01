# Indexed links

The link index avoids reparsing the complete vault for backlink, outlink, and
graph queries.

## Extraction

Indexing recognizes:

- wikilinks: `[[note]]`, `[[note|alias]]`, `[[note#heading]]`, `[[note^block]]`;
- Markdown links with relative or external targets;
- embeds such as `![[image.png]]` and `![[note]]`;
- external URLs when enabled.

Numeric payloads below are synthetic and document shape only.

## Tables

### `links_index`

| Field | Meaning |
|---|---|
| `from_note_path` | Source note path |
| `from_note_title` | Source note title |
| `link_type` | `wikilink`, `markdown`, `embed`, or `external` |
| `link_target` | Original target |
| `link_target_normalized` | Target normalized for matching |
| `to_note_path` | Resolved target note, when present |
| `is_resolved` | Whether a note target was found |
| `alias` | Display alias |
| `heading` | Heading fragment |
| `block_ref` | Block reference |
| `context` | Bounded source context |
| `modified_at` | Source-note modification time |

### `note_aliases`

| Field | Meaning |
|---|---|
| `note_path` | Note path |
| `alias` | Original frontmatter alias |
| `alias_normalized` | Matching form |

## Navigation tools

### `get_backlinks`

```python
get_backlinks(path="example-note.md", include_context=True)
```

```json
[
  {
    "path": "related-note.md",
    "title": "Related note",
    "link_type": "wikilink",
    "link_target": "example-note",
    "context": "...text with [[example-note]]..."
  }
]
```

### `get_outlinks`

```python
get_outlinks(path="example-note.md")
```

```json
{
  "path": "example-note.md",
  "wikilinks": [{"target": "related", "resolved": true, "resolved_path": "related.md"}],
  "markdown_links": [],
  "embeds": [{"target": "image.png", "resolved": false}],
  "external": [{"url": "https://example.com"}],
  "total": 3,
  "broken_count": 1
}
```

### `find_broken_links`

Returns complete filtered totals plus a bounded note list. `returned_notes` is
the list size and `has_more` reports truncation. There is no offset.

### `find_orphan_notes`

Returns complete filtered note/orphan totals plus a bounded list of notes with
no known links. There is no offset.

### `link_stats`

Aggregates total, resolved, broken, and external links; unique sources and
targets; and bounded most-referenced and most-outgoing lists.

Detailed shapes are in [navigation tools](../api/tools-navigation.md).

## Normalization and resolution

| Original | Normalized |
|---|---|
| `Example Project` | `example-project` |
| `docs/API.md` | `docs/api` |
| `  note  ` | `note` |
| `UPPER CASE` | `upper-case` |

Resolution order:

1. exact normalized path;
2. normalized note stem;
3. normalized frontmatter alias.

A resolved row sets `is_resolved=true` and `to_note_path`.

Complex wikilinks retain their parts:

```text
[[Note#Heading|alias]]
  target: Note
  heading: Heading
  alias: alias

[[Note^block-id]]
  target: Note
  block_ref: block-id
```

Frontmatter aliases are indexed:

```yaml
---
title: API Documentation
aliases: [API Docs, API Reference]
---
```

`[[API Docs]]` can then resolve to that note.

## Consistency and bounds

Queries read indexed rows and avoid full-vault reparsing. Internal read and
return limits protect memory. In a vault beyond those limits, a field called
`total` may describe the processed view rather than a census. Validate coverage
before using it as a global count.

After editing links, wait for the watcher or run `reindex_note`. A complete
rebuild recreates both link and alias tables.

```bash
uv run python -m vault_search.core.indexer
uv run python -m vault_search
```

Measure comparisons with the [benchmark protocol](../performance/benchmarking.md).
