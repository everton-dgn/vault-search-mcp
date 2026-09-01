# ADR-0003: canonical YAML configuration

## Status

Accepted.

## Context

Examples with different schemas caused documentation and runtime behavior to
drift. Some legacy fields were silently accepted or ignored.

## Decision

`config.example.yaml` is the only complete example and mirrors
`VaultSearchConfig`. Local configuration uses `config.yaml` or `config.yml`,
both ignored by Git. `VAULT_SEARCH_CONFIG` explicitly selects another file.
Relative paths are anchored to the selected file's directory.

## Consequences

- A new field updates schema, example, documentation, and tests together.
- Legacy examples do not remain as alternatives.
- Environment overrides are limited to operational needs and documented
  separately.
- Unknown configuration fails visibly under strict extra-field validation.
