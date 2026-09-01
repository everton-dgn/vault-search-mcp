# Frontmatter schema validation

vault-search-mcp can validate Markdown frontmatter through a configurable
Pydantic schema.

## Capabilities

- expected type per field;
- automatic UUID v7 and datetime generation;
- explicit type coercion with warnings;
- required, suggested, optional, and ignored fields;
- aliases for alternative field names;
- string, number, enum, URL, and list constraints.

## Configuration

```yaml
frontmatter:
  enabled: true
  mode: "lenient"        # strict | lenient | warn_only
  allow_extra_fields: true

  schema:
    id:
      type: "uuid"
      on_missing: "auto"

    created_at:
      type: "datetime"
      on_missing: "auto"
      aliases: [created, date]

    status:
      type: "enum"
      values: [draft, review, published, archived]
      on_missing: "suggest"

    title:
      type: "string"
      on_missing: "require"
      max_length: 200
```

## Supported types

| Type | Meaning | Constraints |
|---|---|---|
| `string` | Text | `min_length`, `max_length`, `pattern` |
| `int` | Integer | `minimum`, `maximum` |
| `float` | Decimal number | `minimum`, `maximum` |
| `bool` | Boolean | none |
| `date` | ISO date | none |
| `datetime` | ISO date and time | none |
| `uuid` | Valid UUID | none |
| `url` | HTTP or HTTPS URL | none |
| `enum` | Member of an allowed list | `values`, `case_insensitive` |
| `list` | List of values | `item_type`, `min_items`, `max_items` |

## Missing-field behavior

| Value | Behavior |
|---|---|
| `auto` | Generate a value; supported only for `uuid` and `datetime` |
| `suggest` | Accept the object and return a suggestion |
| `require` | Report a blocking error when absent |
| `ignore` | Ignore absence; this is the default |

## Validation modes

| Mode | Behavior |
|---|---|
| `strict` | Blocking errors make the result invalid |
| `lenient` | Blocking errors remain invalid and warnings are reported |
| `warn_only` | Validation errors become warnings |

In the current implementation, `strict` and `lenient` compute `valid` the same
way. The difference remains documentary until a tested contract changes it.

## Type coercion

```yaml
priority: "3"     # string to int
active: "yes"     # string to bool
tags: "a, b, c"   # string to list
```

| Input | Target | Result |
|---|---|---|
| numeric string | `int` or `float` | parsed number |
| float | `int` | truncated integer with warning |
| `true`, `yes`, `1`, `on` | `bool` | `True` |
| `false`, `no`, `0`, `off` | `bool` | `False` |
| date | `datetime` | midnight is added |
| comma-separated string | `list` | trimmed items |
| case variant | `enum` | canonical allowed spelling |

NaN and infinity are rejected for integers and floats so JSON and search
behavior stay deterministic.

## Aliases

```yaml
schema:
  created_at:
    type: "datetime"
    aliases: [created, date, timestamp]
```

An alias is normalized to the canonical field. If both are present, the
canonical value wins and validation emits a warning.

```python
{"created_at": "2026-01-15", "created": "2026-01-20"}
# validated_data["created_at"] uses 2026-01-15
```

## Validation result

```python
{
    "valid": True,
    "errors": [],
    "warnings": [],
    "suggestions": [],
    "auto_generated": {
        "id": "019c503c-08e7-707f-9441-f4e6c5d0dd61",
        "created_at": "2026-01-15T10:30:00",
    },
    "validated_data": {},
}
```

## MCP and Python APIs

Provide exactly one selector to `validate_frontmatter`:

```python
validate_frontmatter(path="notes/example.md")
validate_frontmatter(frontmatter={"status": "draft", "tags": ["ai", "python"]})
```

Direct Python use:

```python
from vault_search.frontmatter import FrontmatterValidator
from vault_search.frontmatter.schema import FieldSchema, FrontmatterSchemaConfig

config = FrontmatterSchemaConfig(
    enabled=True,
    mode="lenient",
    schema={
        "title": FieldSchema(type="string", on_missing="require"),
    },
)

validator = FrontmatterValidator(config)
result = validator.validate({"title": "Example note"})
```

## CRUD integration

`create_note` and `update_frontmatter` always apply configured validation. The
MCP tools do not expose a per-call bypass.

```python
create_note(
    path="notes/new.md",
    content="# Content",
    frontmatter={"status": "draft"},
)

update_frontmatter(
    path="notes/existing.md",
    metadata={"status": "published"},
    merge=True,
)
```

`strict` and `lenient` block errors; `warn_only` continues with warnings.

For existing notes, use `generate_missing_ids(dry_run=True)` to preview UUID
generation or `reindex_note` for one note. Fields with `on_missing: auto` are
persisted during supported write flows.

## Example schemas

### Published content

```yaml
schema:
  id:
    type: "uuid"
    on_missing: "auto"
  title:
    type: "string"
    on_missing: "require"
    max_length: 200
  status:
    type: "enum"
    values: [draft, review, published]
    on_missing: "suggest"
  tags:
    type: "list"
    item_type: "string"
    on_missing: "ignore"
  source:
    type: "url"
    on_missing: "ignore"
```

### Technical documentation

```yaml
schema:
  id:
    type: "uuid"
    on_missing: "auto"
  version:
    type: "string"
    pattern: "^\\d+\\.\\d+\\.\\d+$"
    on_missing: "suggest"
  deprecated:
    type: "bool"
    on_missing: "ignore"
    default: false
```

## Package map

```text
src/vault_search/frontmatter/
├── __init__.py       # public exports
├── types.py          # validation result contracts
├── schema.py         # Pydantic field and schema models
├── coercion.py       # type-specific conversions
├── validator.py      # validation engine
└── enrichment.py     # optional external command
```

See [UUID v7](uuid-system.md), [CRUD tools](../api/tools-crud.md), and
[YAML configuration](../config/yaml.md).
