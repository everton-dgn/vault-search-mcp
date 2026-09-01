# Monitoring

Monitoring means detecting availability, queued work, synchronization failure,
and resource pressure without recording vault content.

## Available signals

| Signal | Interface | Purpose |
|---|---|---|
| `health_check` | MCP tool | Aggregated server and index state |
| `system_stats` | MCP tool | Internal metrics and caches |
| `vault_stats` | MCP tool | Notes, chunks, and index update state |
| `/health` | Local daemon HTTP | Model identity and readiness |
| `/stats` | Local daemon HTTP | Aggregated daemon use |
| structured logs | stderr, journald, or local file | Sanitized events and failures |

## Local daemon check

```bash
curl --fail --silent --show-error --max-time 5 \
  http://127.0.0.1:9847/health
```

Do not call this endpoint over a network. A valid HTTP response must also match
the expected identity and schema. HTTP 200 means both models are loaded and the
state is `ready`; startup, degradation, and failure return 503. The `pid` field
identifies the serving process and should match the service manager during
installation or incident diagnosis. Clients require literal JSON booleans for
model readiness and a JSON integer for strict PID matching.

## Index state

Record at least:

- note and chunk counts;
- last update timestamp;
- new, modified, and deleted files from synchronization;
- active index generation, when exposed;
- ANN and FTS state.

Investigate a sudden count change before discarding a derived index.

## Safe logs

Events may contain stable code, duration, counts, and component names. Exclude:

- complete queries;
- note excerpts;
- absolute paths or usernames;
- private frontmatter, tags, or unnecessary UUIDs;
- tokens, complete environments, or client-visible tracebacks.

Before sharing diagnostics, replace paths with synthetic names and review every
line.

## Alerts

The project publishes no universal alert thresholds. Build alerts from a
baseline in the target environment. Useful conditions include:

- consecutive health-check failure;
- synchronization pending beyond the normal local window;
- index note loss without corresponding vault changes;
- search p95 growth against the same environment's baseline;
- repeated daemon restarts;
- available memory crossing an operator-defined floor.

Record the [benchmark protocol](../performance/benchmarking.md) before turning
an observation into an SLO.
