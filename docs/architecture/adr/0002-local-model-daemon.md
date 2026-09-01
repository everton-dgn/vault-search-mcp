# ADR-0002: local model daemon

## Status

Accepted.

## Context

MCP clients may launch short-lived processes. Loading models in each process
repeats initialization and memory cost.

## Decision

Provide an optional loopback-only HTTP daemon that keeps embedding and reranking
models resident. The MCP process remains responsible for protocol handling,
the vault, and indexes.

## Consequences

- Several local clients can reuse one model process.
- The client verifies daemon identity and semantic health.
- Daemon failure and restart are normal recovery paths.
- Non-loopback binding stays outside the supported contract until TLS,
  authentication, quotas, and a remote-access threat model exist.
