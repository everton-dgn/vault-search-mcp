# Architecture diagrams

Mermaid diagrams are grouped by subsystem.

| File | Content |
|---|---|
| [diagrams-core.md](diagrams-core.md) | Topology, semantic search, indexing, hybrid search, model lifecycle |
| [diagrams-daemon.md](diagrams-daemon.md) | Loopback daemon, startup, MCP communication, HTTP API, shutdown |
| [diagrams-features.md](diagrams-features.md) | Cache, trust, watcher, parsing, chunking, prewarm, links, graph |

## Quick topology

```mermaid
flowchart TB
    subgraph Daemon["Optional daemon :9847"]
        BGE["BGE-M3"]
        RR["Reranker"]
    end

    subgraph MCP["MCP server"]
        ST[search_tools]
        CT[crud_tools]
        GT[graph_tools]
    end

    subgraph Core["Core"]
        SE[Searcher]
        IX[Indexer]
        MM[ModelManager]
    end

    subgraph Data["Derived data"]
        LDB[(LanceDB)]
        CAT[(SQLite)]
        LNK[(links_index)]
    end

    MCP --> SE --> MM
    MCP --> IX --> MM
    MM -->|auto-detect| Daemon
    SE --> LDB
    IX --> LDB
    IX --> LNK
    GT --> LNK
    CT --> CAT
```

## Diagram groups

### Core

- General architecture
- Semantic search flow
- Full and incremental indexing
- Hybrid search
- `ModelManager` lifecycle

### Daemon

- Daemon architecture and startup
- MCP-to-daemon communication
- Internal HTTP API
- Graceful shutdown
- Startup synchronization

### Features

- Layered caches and data trust
- Debounced filesystem watcher and catalog reconciliation
- Parsing and recursive chunking
- Server prewarming
- Indexed links, backlinks, graph analysis, and target resolution
