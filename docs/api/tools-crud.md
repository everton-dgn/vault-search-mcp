# CRUD and frontmatter tools

This group contains 13 tools. Read operations accept paths relative to the
vault. Write operations validate extension, ignored folders, content size, and
directory traversal before touching a file.

## Effect matrix

| Tool | Reads a file | Writes the vault | Updates the index |
|---|---:|---:|---:|
| `read_note` | yes | no | no |
| `get_note_metadata` | yes | no | no |
| `list_notes` | catalog or filesystem | no | no |
| `create_note` | no | yes | asynchronously |
| `write_note` | when present | yes | asynchronously |
| `append_note` | yes | yes | asynchronously |
| `update_frontmatter` | yes | yes | asynchronously |
| `delete_note` | no | moves to `.trash/` | asynchronously |
| `move_note` | no | moves | source and destination asynchronously |
| `generate_missing_ids` | yes | optional | changed notes asynchronously |
| `validate_frontmatter` | optional | no | no |
| `enrich_frontmatter` | yes | the job may write | after each change |
| `enrich_frontmatter_status` | no | no | no |

Background updates invalidate search caches immediately. The filesystem watcher
can reconcile the index if an asynchronous update fails.

## Concurrent writes

Every note mutation participates in the same path-level lock protocol.
`move_note` acquires source and destination locks in deterministic order. On
systems with `fcntl`, the advisory lock also coordinates separate processes
using this library. Other systems retain thread-level coordination within the
current process.

The default lock timeout is five seconds. Set
`VAULT_SEARCH_WRITE_LOCK_TIMEOUT_SECONDS` to a value from 0 through 300 to
override it. A timeout leaves the file unchanged and returns
`error_code: "write_lock_timeout"`. A revision change between read and
replacement returns `error_code: "write_conflict"`.

External writers that ignore the lock are not serialized. Mutations compare
inode, `mtime_ns`, and size before replacement or movement, but a client should
still reread after a conflict.

## Read tools

### `read_note`

```python
read_note(path: str) -> dict | str
```

Reads a complete `.md` note. The result includes `content`, `frontmatter`,
`body`, `tags`, `title`, `folder`, `modified_at`, and `size_bytes`. Search tools
cover other enabled formats.

### `get_note_metadata`

```python
get_note_metadata(path: str) -> dict | str
```

Reads frontmatter and file metadata without the full body. The cache is
validated by path, modification time, and file size.

### `list_notes`

```python
list_notes(
    folder: str | None = None,
    extension: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict | str
```

Lists enabled extensions. The default limit is 500 and the maximum is 5,000.
A negative `offset` becomes zero. Results include `notes`, `total`, `limit`,
`offset`, and `has_more`, ordered with recently modified notes first.

```python
list_notes(folder="projects", extension="md", limit=50, offset=0)
```

## Markdown writes

### `create_note`

```python
create_note(
    path: str,
    content: str,
    frontmatter: dict | None = None,
) -> dict | str
```

Creates a `.md` note and fails when the destination exists. `content` is the
body without the YAML block. The configured schema is validated and a UUID v7
is added when `id` is absent.

```python
create_note(
    path="projects/atlas/decision.md",
    content="# Decision\n\nRecord of the choice.",
    frontmatter={"status": "draft", "tags": ["architecture"]},
)
```

### `write_note`

```python
write_note(path: str, content: str) -> dict | str
```

Writes complete `.md` content, including frontmatter when present. It can create
or replace a file. Persistence uses a temporary file and an atomic replacement
within the destination directory.

### `append_note`

```python
append_note(path: str, content: str, separator: str = "\n\n") -> dict | str
```

Appends text to an existing `.md` note. The separator is inserted only when the
current content does not already end with it.

### `update_frontmatter`

```python
update_frontmatter(path: str, metadata: dict, merge: bool = True) -> dict | str
```

With `merge=True`, the merge is one level deep; lists and nested objects are
replaced. With `merge=False`, all frontmatter is replaced. The resulting object
passes through the configured schema before persistence.

## Recoverable movement

### `delete_note`

```python
delete_note(path: str) -> dict | str
```

Moves a file into `.trash/` inside the vault while preserving its folder
structure. Collisions receive a random suffix. The API has no permanent-delete
operation.

### `move_note`

```python
move_note(from_path: str, to_path: str) -> dict | str
```

Moves or renames a note. The destination must be unused, retain the source
extension, stay inside the vault, and avoid ignored folders.

## IDs and validation

### `generate_missing_ids`

```python
generate_missing_ids(folder: str | None = None, dry_run: bool = False) -> dict | str
```

Scans `.md` notes without `id`. Start with `dry_run=True`, which returns the
count plus up to 100 paths without writing files. A real run writes UUID v7
values and bounds returned detail to 50 successes and 10 errors.

### `validate_frontmatter`

```python
validate_frontmatter(
    path: str | None = None,
    frontmatter: dict | None = None,
) -> dict | str
```

Provide exactly one selector. The result includes `valid`, `errors`, `warnings`,
`suggestions`, `auto_generated`, and `validated_data`. Validation does not
persist generated values.

## External enrichment

Enrichment is disabled in the public configuration. Enabling it requires an
enabled schema, `allow_external_processing: true`, an explicit `provider`, and
a command. Note content is sent through `stdin`; the command template accepts
only `{model}`.

### `enrich_frontmatter`

```python
enrich_frontmatter(
    path: str | None = None,
    paths: list[str] | None = None,
    folder: str | None = None,
    limit: int = 100,
) -> dict | str
```

Provide exactly one selector. The tool schedules a job and returns `job_id`
without waiting. Folder mode clamps `limit` from 1 through 1,000.

### `enrich_frontmatter_status`

```python
enrich_frontmatter_status(job_id: str | None = None, limit: int = 20) -> dict | str
```

With `job_id`, reads one job. Without it, returns recent jobs kept by the
current process.

## Error handling

Public failures use sanitized messages. Clients should rely on structured
fields when available, not on human-readable text. See [API errors](errors.md)
and the [threat model](../security/threat-model.md).
