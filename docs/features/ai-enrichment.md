# External frontmatter enrichment

This integration fills missing required frontmatter fields through an external
command. It starts disabled and requires explicit consent because note content
can leave the main process.

## Before enabling it

Confirm:

- which process receives content;
- the provider's retention and training policy;
- how the command obtains credentials;
- which fields may be sent;
- the effect of timeout, invalid output, and partial output.

Use a synthetic vault for first validation.

## Safe configuration

```yaml
frontmatter:
  enabled: true
  mode: "lenient"
  ai:
    enabled: true
    allow_external_processing: true
    provider: "provider-cli-local"
    allow_defer_required_on_create: true
    command:
      - "provider-cli"
      - "--model"
      - "{model}"
    primary_model: "primary-model"
    fallback_model: null
    timeout_seconds: 8.0
    max_attempts: 2
    max_note_chars: 12000
  schema:
    summary:
      type: "string"
      on_missing: "require"
      max_length: 500
```

The command template accepts `{model}` and rejects `{prompt}`. Note content is
sent through `stdin`, keeping it out of process arguments and ordinary process
diagnostics.

## Data flow

1. Validation finds empty fields with `on_missing: require`.
2. The job bounds the note body to `max_note_chars`.
3. The command receives the model identifier in `argv` and the prompt on
   `stdin`.
4. Output must contain one JSON object.
5. Only required fields that were absent can be merged.
6. A primary-model failure can trigger the configured fallback.
7. Generated values pass through frontmatter validation before persistence.

## MCP tools

### `enrich_frontmatter`

Schedules one or more notes. A successful job can update frontmatter. Repeated
paths are deduplicated without changing order. A job accepts at most 1,000
Markdown paths. Stable rejection codes are `too_many_paths`, `queue_full`, and
`stopped`.

### `enrich_frontmatter_status`

Reads one job or lists recent state. Public responses omit note content and
internal process detail. A job retains at most 100 detailed results and reports
`returned` plus `truncated`; `processed`, `succeeded`, and `failed` cover the
complete job.

## Queue bounds

- Up to 200 jobs may wait in addition to the running job.
- `queued` and `running` jobs are never pruned from history.
- Up to 200 terminal jobs remain in process memory.
- One-note errors become controlled failures and do not terminate the worker.
- Shutdown stops accepting work and attempts to drain pending jobs within its
  deadline.

A rejected request was not processed. Check `accepted` before retaining its
`job_id`.

## Expected failures

| Condition | Result |
|---|---|
| `enabled: false` | Enrichment unavailable |
| Missing consent | Configuration rejected |
| Empty provider | Configuration rejected |
| `{prompt}` in command | Configuration rejected |
| Missing command or primary model | Configuration rejected |
| Timeout | Attempt ends and bounded retry policy applies |
| Output without a JSON object | Output discarded |
| More than 1,000 deduplicated paths | `too_many_paths` |
| 200 jobs waiting | `queue_full` |
| Shutdown started | `stopped` |

## Operational privacy

- Never put a token in YAML or the `command` array.
- Never log `stdin`, complete `stdout`, note bodies, or absolute paths.
- Send only the minimum content required by the provider.
- Disable enrichment before switching vaults or data policies.
- Treat generated text as untrusted until schema validation succeeds.
