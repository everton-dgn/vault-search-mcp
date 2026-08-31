# Architecture decision records

ADRs preserve context for decisions that affect several modules. A change that
invalidates a decision creates a new ADR and marks the previous one superseded.

| ADR | Status | Decision |
|---|---|---|
| [0001](adr/0001-vault-as-source-of-truth.md) | Accepted | The vault is primary |
| [0002](adr/0002-local-model-daemon.md) | Accepted | Optional local model daemon |
| [0003](adr/0003-canonical-configuration.md) | Accepted | One YAML example aligned with the schema |
| [0004](adr/0004-performance-evidence.md) | Accepted | Numeric benchmarks require a manifest |

## When to create an ADR

- trust-boundary changes;
- new storage or persistent formats;
- MCP compatibility changes;
- central dependency decisions;
- migration or concurrency strategy;
- performance or privacy policy.

An ADR contains context, decision, consequences, and considered alternatives.
Transient implementation detail belongs near the code.
