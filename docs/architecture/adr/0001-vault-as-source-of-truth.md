# ADR-0001: the vault is the source of truth

## Status

Accepted.

## Context

The system maintains LanceDB and auxiliary catalogs to speed up retrieval and
navigation. Those derived artifacts can become incomplete after a failure,
schema change, or interrupted process.

## Decision

Notes in the vault are primary. Indexes and catalogs are derived and
rebuildable. A maintenance operation must never treat an index as the only copy
of user content.

## Consequences

- Backup and recovery focus on the vault.
- A rebuild may discard only derived artifacts.
- Index generation changes preserve the active generation until the replacement
  is ready.
- Metadata stored only in an index should be avoided or explicitly classified
  as ephemeral.
