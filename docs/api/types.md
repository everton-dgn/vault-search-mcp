# Public and internal types

Shared contracts live in `src/vault_search/type_defs.py`. CRUD response types
live in `src/vault_search/crud/types.py`. CI runs mypy across the complete
package.

## States

### `ParseStatus`

| Value | Meaning |
|---|---|
| `success` | Parser produced records |
| `empty` | Valid file without chunks |
| `error` | Parsing failed |
| `unsupported` | Dispatcher does not support the format |

### `ReindexStatus`

Values are `updated`, `empty`, `deleted`, `parse_error`, `error_add_failed`,
`rejected_path_traversal`, `rejected_extension`, and `circuit_breaker_open`.

### `FullReindexStatus`

Values are `completed`, `failed`, and `interrupted`.

All three classes inherit from `StrEnum` and serialize as strings.

## Chunks

### `ChunkRecord`

Record produced before embedding:

```python
class ChunkRecord(TypedDict):
    note_path: str
    note_title: str
    folder: str
    headers: str
    tags: str
    modified_at: str
    text: str
```

Optional frontmatter fields include `id`, `created_at`, `updated_at`,
`description`, `status`, `note_type`, `category`, `project`, and `source`.

### `ChunkWithVector`

Extends `ChunkRecord` with:

```python
vector: list[float]
```

The stored vector is not part of the public `SearchResult`.

### `ParseResult`

The slotted dataclass distinguishes empty input from parsing failure:

```python
@dataclass(slots=True)
class ParseResult:
    status: ParseStatus
    chunks: list[ChunkRecord]
    links: list[LinkRecord]
    aliases: list[str]
    error_type: str | None
```

Iteration preserves historical unpacking into `chunks`, `links`, and `aliases`.

## Links

`LinkRecord` contains source, original and normalized target, type, context, and
modification date. Resolution adds `to_note_path` and `is_resolved`. Alias,
heading, and block reference are optional.

```python
class LinkRecord(TypedDict):
    from_note_path: str
    from_note_title: str
    link_type: str
    link_target: str
    link_target_normalized: str
    context: str
    modified_at: str
```

## Search

```python
class SearchResult(TypedDict):
    note_path: str
    note_title: str
    folder: str
    headers: str
    tags: str
    text: str
    score: NotRequired[float]
```

`score` appears when a backend provides distance or reranking. It orders one
execution, is not a calibrated probability, and should not compare quality
between models or versions.

## Indexing

### `ReindexResult`

Required fields:

```python
chunks_indexed: int
status: ReindexStatus
```

Optional fields are `links_indexed`, `aliases_indexed`, `id_added`,
`frontmatter_enriched`, `frontmatter_fields_filled`, and `auto_compacted`.

### `IndexStats`

```python
class IndexStats(TypedDict):
    total_chunks: int
    unique_notes: int
    last_modified: str | None
```

### `FullReindexStats`

```python
class FullReindexStats(TypedDict):
    status: FullReindexStatus
    total_notes: int
    total_chunks: int
    duration_seconds: float
```

Optional fields cover parse errors, preservation of the old index, links,
aliases, interruption, and vector-index creation. Dry runs use the scan-count
shape documented in [indexing tools](tools-indexing.md).

## CRUD

### `NoteContent`

```python
class NoteContent(TypedDict):
    path: str
    content: str
    frontmatter: dict
    body: str
    tags: list[str]
    title: str
    folder: str
    modified_at: str
    size_bytes: int
```

### `NoteMetadata`

The `get_note_metadata` result has the same metadata as `NoteContent`, without
`content` or `body`.

### `NoteListItem` and `NoteListResult`

```python
class NoteListItem(TypedDict):
    path: str
    title: str
    folder: str
    extension: str
    modified_at: str
    size_bytes: int

class NoteListResult(TypedDict):
    notes: list[NoteListItem]
    total: int
    limit: int
    offset: int
    has_more: bool
```

### `OperationResult`

```python
class OperationResult(TypedDict):
    success: bool
    message: str
    path: str
    error_code: NotRequired[str]
    reindex_status: NotRequired[str]
```

`reindex_status` is `queued`, `coalesced`, `queue_full`, or `stopped` when a
mutation schedules an asynchronous update. Operations may also add validation
warnings, suggestions, UUID fields, or an enrichment job ID.

## Compatibility

These types support internal checking and document the current public shape.
During alpha, clients should ignore extra fields and handle optional fields as
absent. Removals and renames require a [changelog](../../CHANGELOG.md) entry and
contract tests.
