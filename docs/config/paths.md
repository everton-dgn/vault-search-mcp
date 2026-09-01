# Paths and persistence

## Source of each path

| Data | Configuration | Role |
|---|---|---|
| Vault | `paths.vault_path`, `VAULT_SEARCH_VAULT_PATH`, or legacy `VAULT_PATH` | Primary note source |
| Data directory | `paths.data_dir` or `VAULT_SEARCH_DATA_DIR` | Rebuildable LanceDB, catalog, and cache |
| Table | `paths.lancedb_table` | Logical LanceDB table name |

Relative paths use the directory containing the selected YAML file. Without a
file, defaults use the process working directory. The runtime expands `~` and
resolves the path without requiring the target to exist first.

`VAULT_SEARCH_DB_DIR` is not read. Recognized overrides are captured on first
import of `vault_search.config.paths`; restart the process to change them.

## Default layout

```text
vault-search-mcp/
├── config.yaml                 # local and ignored by Git
├── data/                       # rebuildable artifacts
│   ├── vault_chunks.lance/
│   └── notes_catalog.db
└── vaults/
    └── obsidian_vault/         # vault or operator-controlled symlink
```

## Containment

Every path supplied to a tool must be relative to the vault. Safe validation:

1. rejects absolute paths, `..`, null bytes, and unsupported extensions;
2. resolves the vault root and target;
3. confirms that the resolved target stays below the root;
4. checks symlinks for boundary escape;
5. repeats the guarantee at operation time when races are possible.

Text-only validation cannot contain a symlink that changes after validation.

## Backup

Back up the vault with the system already used for note recovery. Derived
indexes are rebuildable and do not replace backups. Before maintenance, verify
that one synthetic note can be restored.

## Public repository boundary

Never commit:

- vault content;
- `config.yaml`, `config.yml`, or `.env*`;
- `data/`, `.lance`, `.sqlite`, `.npy`, or model-cache artifacts;
- logs or dumps containing resolved paths.

Use small synthetic fixtures in tests and documentation.

## Switching vaults

Stop the MCP process and daemon, update configuration, and create a separate
index. Do not reuse another vault's index as compatible state. Check
`vault_stats` and a synthetic search before enabling writes.
