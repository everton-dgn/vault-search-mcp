# API errors

The 0.x line has three public failure shapes. Clients must accept all three
until a single envelope becomes a stable contract.

## Sanitized message

Unexpected exceptions captured by a tool return:

```text
Error [internal_error]: The operation could not be completed. Reference: a1b2c3d4.
```

The reference is an eight-character hexadecimal identifier generated for each
failure. Current codes include `invalid_request`, `search_unavailable`,
`daemon_unavailable`, and `internal_error`.

The public string omits exception type, traceback, absolute path, query, and
note content. Local logs retain operation name, reference, and exception type
for correlation.

## Direct validation message

Small guardrails can return a string before work begins:

```text
Error: query cannot be empty.
```

Other cases cover empty paths or folders, unsupported extensions, and mutually
exclusive selectors. Human-readable wording can change during alpha.

## Operation result

CRUD uses an object for expected domain failures:

```python
{
    "success": False,
    "message": "Timed out while waiting for another write. Try again.",
    "path": "notes/example.md",
    "error_code": "write_lock_timeout",
}
```

The path is the relative value supplied by the client. Unexpected wrapper
failures still use the sanitized-message form.

| `error_code` | Meaning | Client action |
|---|---|---|
| `write_lock_timeout` | Another cooperative writer held the lock past the deadline | Reread and retry only when the effect is still needed |
| `write_conflict` | The file revision changed during the operation | Reread before producing replacement content |

## Resource envelopes

Resources convert unexpected exceptions to:

```python
{
    "error": "Error [internal_error]: The operation could not be completed. Reference: a1b2c3d4.",
    "code": "internal_error",
}
```

Focused validation in `vault://notes/{path*}` may return `error` and `code`
without a reference, for example `invalid_path` or `not_found`.

## Incremental reindex states

`reindex_note` reports expected outcomes through `status`:

| Status | Meaning |
|---|---|
| `updated` | Note chunks were replaced |
| `empty` | Valid file with no indexable content |
| `deleted` | File is absent and old rows were removed |
| `parse_error` | Parsing failed; old rows remain when possible |
| `error_add_failed` | Index write failed |
| `rejected_path_traversal` | Path escaped the allowed root |
| `rejected_extension` | Extension is outside configuration |
| `circuit_breaker_open` | Repeated failures paused new index writes |

Optional fields can describe links, aliases, identifiers, enrichment, and
automatic compaction.

## Client normalization

```python
def failure_message(result: object) -> str | None:
    if isinstance(result, str) and result.startswith("Error"):
        return result
    if isinstance(result, dict):
        if result.get("success") is False:
            return str(result.get("message", "Operation failed"))
        if "error" in result:
            return str(result["error"])
    return None
```

Do not parse human text to decide whether a retry is safe. Prefer `code`,
`error_code`, `status`, and `success`. Read the current note revision before
repeating any mutation.

## Private diagnostics

When opening an issue:

1. include the code and reference;
2. identify the operation and project version;
3. replace paths with synthetic names;
4. omit local configuration, vault data, indexes, and complete logs;
5. report vulnerabilities through the private [security policy](../../SECURITY.md).

The [troubleshooting guide](../operation/troubleshooting.md) lists checks that
avoid publishing local machine data.
