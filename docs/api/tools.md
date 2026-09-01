# MCP catalog

The server registers 43 tools and 6 resources. This catalog follows the
decorators in `src/vault_search/server/` and is checked during publication.

## Choose a search tool

| Need | Tool |
|---|---|
| Semantic relationships | `search_vault` |
| Exact terms plus semantics | `search_vault_hybrid` |
| One folder and its descendants | `search_by_folder` |
| Structured filters | `search_advanced` |
| Notes similar to a reference note | `find_similar_notes` |
| Exact tag filtering | `search_by_tags` |
| Duplicate or near-duplicate content | `search_duplicates` |

Details: [tools-search.md](tools-search.md).

## Search: 7 tools

| Tool | Purpose |
|---|---|
| `search_vault` | Vector retrieval with reranking |
| `search_vault_hybrid` | Vector retrieval combined with FTS |
| `search_by_folder` | Semantic search below one folder |
| `find_similar_notes` | Use one note as the search reference |
| `search_duplicates` | Group notes by similarity |
| `search_advanced` | Apply folder, date, and frontmatter filters |
| `search_by_tags` | Select notes by exact tags |

## Navigation: 10 tools

| Tool | Purpose |
|---|---|
| `get_backlinks` | Links that point to a note |
| `get_outlinks` | Links that leave a note |
| `find_broken_links` | Targets without a resolved note |
| `find_orphan_notes` | Notes without known links |
| `link_stats` | Link-index statistics |
| `get_recent_notes` | Notes changed within a time window |
| `tag_stats` | Tag frequency and distribution |
| `folder_tree` | Aggregated folder tree |
| `random_note` | Sample one note with optional filters |
| `daily_note` | Find or describe a daily note |

Details: [tools-navigation.md](tools-navigation.md).

## Indexing: 6 tools

| Tool | Effect |
|---|---|
| `vault_stats` | Read index statistics |
| `reindex_vault` | Rebuild the complete derived index |
| `reindex_note` | Incrementally update one note |
| `sync_vault` | Compare vault and index, with dry-run support |
| `compact_index` | Compact LanceDB artifacts |
| `vector_index_status` | Read ANN index state |

Details: [tools-indexing.md](tools-indexing.md).

## CRUD and frontmatter: 13 tools

| Tool | Effect |
|---|---|
| `read_note` | Read content and metadata |
| `get_note_metadata` | Read metadata without the full body |
| `list_notes` | List notes with filters and pagination |
| `create_note` | Create a new note |
| `write_note` | Replace complete note content |
| `append_note` | Append content |
| `update_frontmatter` | Merge or replace frontmatter |
| `delete_note` | Move a note into `.trash` |
| `move_note` | Move or rename a note |
| `generate_missing_ids` | Add UUIDs to notes without identifiers |
| `validate_frontmatter` | Validate a note or object against the schema |
| `enrich_frontmatter` | Schedule explicitly authorized enrichment |
| `enrich_frontmatter_status` | Read enrichment job state |

Details: [tools-crud.md](tools-crud.md).

## Graph: 4 tools

| Tool | Purpose |
|---|---|
| `graph_data` | Export bounded nodes and edges |
| `suggest_links` | Suggest relationships by semantic similarity |
| `find_link_clusters` | Connected components and simple-graph density |
| `find_bridge_notes` | Articulation points using iterative Tarjan traversal |

Details: [tools-graph.md](tools-graph.md).

## System: 3 tools

| Tool | Purpose |
|---|---|
| `system_stats` | Internal, model, and cache metrics |
| `health_check` | Aggregated component health |
| `benchmark_search` | Sample latency in the current environment |

Details: [tools-system.md](tools-system.md).

## Resources: 6

| URI | Content |
|---|---|
| `vault://stats` | Summarized index state |
| `vault://folders` | Folder tree |
| `vault://notes` | Bounded note list |
| `vault://notes/{path*}` | Note content by relative path |
| `vault://search/recent` | Recently changed notes |
| `vault://tags` | Tag statistics |

Details: [tools-resources.md](tools-resources.md).

## Effects and authorization

Read tools do not change notes. Indexing changes only derived artifacts unless
UUID generation is explicitly requested. CRUD, ID generation, and enrichment
can modify the vault and should pass through the client's authorization policy.

`delete_note` uses the vault's `.trash` directory. The public contract has no
permanent-delete operation.

## Errors

Clients should treat success and failure values as structured data. A public
message must not contain a stack trace, absolute path, complete query, or note
body. See [errors.md](errors.md).

## Compatibility

The 0.x line is alpha. A change to a name, argument, default, or return shape
must appear in the changelog and contract tests. Backward-compatible stability
becomes mandatory at version 1.0.
