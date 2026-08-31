# Diagramas de arquitetura

Diagramas Mermaid do sistema vault-search-mcp, organizados por tema.

## Índice de diagramas

| Arquivo | Conteúdo |
|---------|----------|
| [diagrams-core.md](./diagrams-core.md) | Arquitetura geral, busca semântica, indexação, busca híbrida |
| [diagrams-daemon.md](./diagrams-daemon.md) | Daemon HTTP, startup, comunicação MCP↔Daemon, shutdown |
| [diagrams-features.md](./diagrams-features.md) | Cache, watcher, parsing, chunking, prewarm |

---

## Visão geral rápida

```mermaid
flowchart TB
    subgraph Daemon["⚡ Daemon :9847"]
        BGE["BGE-M3"]
        RR["Reranker"]
    end

    subgraph MCP["MCP Server"]
        ST[search_tools]
        CT[crud_tools]
        GT[graph_tools]
    end

    subgraph Core["Core"]
        SE[Searcher]
        IX[Indexer]
        MM[ModelManager]
    end

    subgraph Data["Data"]
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

---

## Diagramas por categoria

### Core (diagrams-core.md)
- Arquitetura Geral
- Fluxo de Busca Semântica
- Fluxo de Indexação Completa
- Fluxo de Indexação Incremental
- Busca Híbrida
- ModelManager Lifecycle

### Daemon (diagrams-daemon.md)
- Daemon Architecture
- Daemon Startup Sequence
- MCP ↔ Daemon Communication
- Daemon HTTP API
- Graceful Shutdown Flow
- sync_check no Startup

### Features (diagrams-features.md)
- Sistema de Cache Multi-Camada
- Escopo de Confiança dos Dados
- File Watcher com Debounce
- Reconciliação do Catálogo
- Pipeline de Parsing
- Chunking Recursivo
- Server Startup com Prewarm
- Prewarm Decision Flow
- Sistema de Links Indexados
- Fluxo de get_backlinks
- Análise de Grafo
- Resolução de Links
