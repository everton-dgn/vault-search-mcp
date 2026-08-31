# YAML configuration

`config.example.yaml` is the only complete example and follows
`VaultSearchConfig`. Copy it to `config.yaml` and keep the local copy out of Git.

## Precedence

1. `VAULT_SEARCH_CONFIG`, when it points to an existing file;
2. `config.yaml` in the working directory;
3. `config.yml` in the working directory;
4. `config.yaml` or `config.yml` in the installation root, when different;
5. Pydantic defaults.

The object loads once and remains cached. Restart MCP and daemon processes after
changing the file.

## Sections

| Section | Responsibility |
|---|---|
| `paths` | Vault, data directory, and LanceDB table |
| `search` | Results, candidates, score precision, and pagination |
| `indexing` | Batches, workers, extensions, and ignored folders |
| `fts` | Neutral tokenization or opt-in language stemming |
| `prewarm` | Early index loading into memory |
| `embedding` | Models, device, precision, and dimensions |
| `chunking` | Size, overlap, headers, and separators |
| `security` | Input, path, and frontmatter limits |
| `watcher` | Debounce and thread shutdown |
| `pdf` | OCR, languages, and DPI |
| `vector_index` | ANN creation and parameters |
| `navigation` | Folder-tree depth |
| `daemon` | Loopback host, port, timeout, and auto-detection |
| `frontmatter` | Schema, validation mode, and external enrichment |

## Minimal example

```yaml
paths:
  vault_path: "vaults/obsidian_vault"
  data_dir: "data"

embedding:
  device: "auto"
  use_fp16: null

frontmatter:
  enabled: false
  ai:
    enabled: false
    allow_external_processing: false
    provider: null
```

Omitted fields receive defaults. The complete example explains each value.

## Paths

Relative paths use the directory containing the selected YAML file. Without a
file, defaults use the working directory. The runtime expands `~` and accepts
absolute paths. Avoid publishing resolved values in issues or documentation.

`VAULT_SEARCH_VAULT_PATH` overrides only the vault. Legacy `VAULT_PATH` applies
when the modern variable is absent. `VAULT_SEARCH_DATA_DIR` overrides the data
directory. These aliases are captured on first import; restart after changing
the environment. `VAULT_SEARCH_DB_DIR` is not recognized.

## Device and precision

`auto` chooses an available backend and `null` selects compatible precision at
runtime. To force CPU:

```yaml
embedding:
  device: "cpu"
  use_fp16: false
```

Validate the combination on target hardware. A schema-valid device may still
fail because of a driver, backend version, or unsupported operation.

## Extensions

The public set is `.md`, `.mdx`, `.txt`, `.pdf`, and `.canvas`. A custom
`indexing.extensions` must be a subset. Unsupported formats, duplicates,
missing dots, and uppercase values fail during configuration loading.

`indexing.ignored_folders` compares simple folder names at every level. Values
containing `/`, `\\`, `.` or `..` are invalid. `.git`, `.obsidian`,
`.smart-env`, and `.trash` start ignored.

## Full-text search

`fts.language: null` disables language-specific stemming and stop-word removal,
while retaining lowercase and accent folding for predictable multilingual
matching. Set a backend-supported language only when its analyzer is desired.
Rebuild FTS before expecting the new policy to affect indexed content.

## Frontmatter and external processing

Schema validation and external enrichment start disabled. External processing
requires `allow_external_processing: true`, an explicit provider, and a safe
command. The template accepts only `{model}`. Note content travels through
`stdin`, never through shell interpolation.

Never store tokens or credentials in YAML. Use the external process's secret
mechanism.

## Early validation

The schema rejects combinations that would otherwise fail later:

- `search.candidates` above `candidates_max`;
- `top_k` outside its configured range;
- default `list_notes` limit above its maximum;
- default `folder_tree` depth above its public maximum;
- `num_sub_vectors` incompatible with embedding dimension for `IVF_PQ`;
- enrichment without consent, provider, command, or model.

Unknown fields also fail. Remove legacy names such as `security.rate_limit`,
`security.reindex_timeout`, and `security.log_query_max_length`; they never had
runtime effects and are outside the current public contract.

## Programmatic validation

```python
from pathlib import Path

from vault_search.config import load_config_from_file

config = load_config_from_file(Path("config.example.yaml"))
print(config.search.top_k)
```

Type and range failures stop loading the selected configuration. Treat any
fallback log as an operational failure until effective vault and data paths are
confirmed locally.
