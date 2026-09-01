# Search tools

Seven tools cover semantic, hybrid, exact-tag, filtered, and duplicate search.

## `search_vault`

Semantic retrieval followed by reranking.

```python
search_vault(query: str, top_k: int = 10) -> list[dict]
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | required | Search text in any language |
| `top_k` | `int` | `10` | Number of results, capped at 100 |

Synthetic result:

```python
[
    {
        "note_path": "projects/example.md",
        "note_title": "Example project",
        "folder": "projects",
        "headers": "## Introduction",
        "tags": "#project #architecture",
        "text": "Relevant content...",
        "score": 0.89,
    }
]
```

`score` orders results from that execution. It is not a calibrated probability.

## `search_vault_hybrid`

Combines semantic retrieval with keyword-oriented FTS.

```python
search_vault_hybrid(query: str, top_k: int = 10) -> list[dict]
```

Use it for technical terms, acronyms, identifiers, and proper names that should
retain exact-text evidence.

## `search_by_folder`

Semantic search restricted to a folder and its descendants.

```python
search_by_folder(query: str, folder: str, top_k: int = 10) -> list[dict]
```

| Parameter | Type | Description |
|---|---|---|
| `folder` | `str` | Relative folder such as `projects` or `studies/python` |

The folder is normalized and validated inside the vault boundary.

## `search_advanced`

Combines semantic retrieval with structured filters, exclusions, and optional
highlighting.

```python
search_advanced(
    query: str,
    top_k: int = 10,
    tags: list[str] | None = None,
    folder: str | None = None,
    extension: str | None = None,
    date_range: str | None = None,  # today, week, month, year
    date_from: str | None = None,   # ISO date
    date_to: str | None = None,     # ISO date
    status: str | None = None,
    note_type: str | None = None,
    category: str | None = None,
    project: str | None = None,
    exclude: list[str] | None = None,
    highlight: bool = False,
) -> list[dict]
```

| Parameter | Meaning |
|---|---|
| `tags` | Match any supplied tag |
| `folder` | Include the folder and descendants |
| `extension` | One enabled extension, with or without the leading dot |
| `date_range` | Named relative window: `today`, `week`, `month`, or `year` |
| `date_from`, `date_to` | Inclusive ISO date boundaries |
| `status` | Exact `status` frontmatter value |
| `note_type` | Exact `type` frontmatter value |
| `category` | Exact `category` frontmatter value |
| `project` | Exact `project` frontmatter value |
| `exclude` | Terms that remove matching results |
| `highlight` | Wrap meaningful query terms with `**` markers |

```python
search_advanced("python", exclude=["django", "flask"])
search_advanced("REST API", highlight=True)
```

## `find_similar_notes`

Uses one indexed note as the semantic reference.

```python
find_similar_notes(path: str, top_k: int = 5) -> list[dict]
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | `str` | required | Relative path of the reference note |
| `top_k` | `int` | `5` | Number of related notes, capped at 20 |

```python
[
    {
        "note_path": "projects/related.md",
        "note_title": "Related project",
        "folder": "projects",
        "tags": "project",
        "similarity_score": 0.87,
    }
]
```

## `search_by_tags`

Exact tag filtering without semantic retrieval.

```python
search_by_tags(
    tags: list[str],
    match_all: bool = False,
    limit: int = 50,
) -> list[dict] | str
```

| Parameter | Default | Contract |
|---|---|---|
| `tags` | required | One to 20 tags |
| `match_all` | `False` | `True` uses AND; `False` uses OR |
| `limit` | `50` | Maximum results, capped at 200 |

Use it for deterministic navigation or as a follow-up to `tag_stats`.

## `search_duplicates`

Groups duplicate and near-duplicate notes.

```python
search_duplicates(
    threshold: float = 0.90,
    max_notes: int = 500,
    folder: str | None = None,
) -> list[dict] | str
```

| Parameter | Default | Contract |
|---|---|---|
| `threshold` | `0.90` | Cosine-similarity cutoff from 0.50 to 0.99 |
| `max_notes` | `500` | Number of notes processed, from 10 to 1,000 |
| `folder` | `None` | Optional folder restriction |

The threshold compares mean note embeddings. Lower values produce more
candidates. No universal cutoff proves duplication; calibrate it against a
reviewed sample from the target vault.
