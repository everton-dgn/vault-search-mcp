# Diagramas do núcleo

## Arquitetura geral

```mermaid
flowchart TB
    subgraph MCP["MCP Server"]
        direction TB
        ST[search_tools.py]
        CT[crud_tools.py]
        FM[FastMCP]
        ST --> FM
        CT --> FM
    end

    subgraph Core["Core Layer"]
        direction TB
        SE[Searcher]
        IX[Indexer]
        MM[ModelManager]
        SE --> MM
        IX --> MM
    end

    subgraph Data["Data Layer"]
        direction TB
        LDB[(LanceDB)]
        CAT[(SQLite Catalog)]
        FS[(Filesystem)]
    end

    FM --> SE
    FM --> IX
    SE --> LDB
    IX --> LDB
    CT --> FS
    CT --> CAT
```

---

## Fluxo de busca semântica

```mermaid
sequenceDiagram
    participant C as Client
    participant M as MCP Server
    participant S as Searcher
    participant EC as Embedding Cache
    participant MM as ModelManager
    participant L as LanceDB
    participant RR as Reranker

    C->>M: search_vault(query, top_k)
    M->>S: search(query, top_k)

    S->>EC: check cache(query)
    alt Cache Hit
        EC-->>S: cached embedding
    else Cache Miss
        S->>MM: embed_query(query)
        MM-->>S: embedding[1024]
        S->>EC: store(query, embedding)
    end

    S->>L: ANN search(embedding, candidates)
    L-->>S: top candidates

    S->>RR: rerank(query, candidates)
    RR-->>S: reranked results

    S-->>M: formatted results
    M-->>C: SearchResult[]
```

---

## Fluxo de indexação completa

```mermaid
flowchart TB
    Start([full_reindex]) --> Scan[scan_vault]
    Scan --> |List[Path]| Pool[ThreadPoolExecutor]

    subgraph Parallel["Parallel Processing"]
        Pool --> P1[parse_file]
        Pool --> P2[parse_file]
        Pool --> P3[parse_file]
        Pool --> Pn[parse_file...]
    end

    P1 --> Batch[Batch Collector]
    P2 --> Batch
    P3 --> Batch
    Pn --> Batch

    Batch --> |500 chunks| Embed[embed_corpus]
    Embed --> Store[LanceDB.add]
    Store --> |more chunks?| Batch

    Store --> |done| FTS[create_fts_index]
    FTS --> Optimize[optimize]
    Optimize --> End([Stats])
```

---

## Fluxo de indexação incremental

```mermaid
flowchart TB
    Start([reindex_note]) --> Validate{validate_path}
    Validate -->|invalid| Reject[Return rejected]
    Validate -->|valid| Lock[Acquire write_lock]

    Lock --> Exists{file exists?}
    Exists -->|no| Delete[Delete from index]
    Delete --> Return1[Return deleted]

    Exists -->|yes| Parse[parse_file]
    Parse --> Chunks{has chunks?}
    Chunks -->|no| DeleteEmpty[Delete from index]
    DeleteEmpty --> Return2[Return empty]

    Chunks -->|yes| Embed[embed_corpus]
    Embed --> DeleteOld[Delete old chunks]
    DeleteOld --> Add[Add new chunks]
    Add --> Optimize[try_optimize]
    Optimize --> Return3[Return updated]
```

---

## Busca híbrida

```mermaid
flowchart TB
    Query([Query]) --> Branch{Search Type}

    Branch -->|semantic| Vec[Vector Search]
    Branch -->|hybrid| Both[Both searches]

    Vec --> Embed[embed_query]
    Embed --> ANN[LanceDB ANN]
    ANN --> VecResults[Vector Results]

    Both --> Embed
    Both --> FTS[FTS Search]
    FTS --> FTSResults[FTS Results]

    VecResults --> Merge{Merge?}
    FTSResults --> Merge

    Merge -->|hybrid| RRF[Reciprocal Rank Fusion]
    Merge -->|semantic only| Pass[Pass through]

    RRF --> Rerank[Cross-encoder Rerank]
    Pass --> Rerank

    Rerank --> Format[Format Results]
    Format --> Output([SearchResult[]])
```

---

## Ciclo de vida do `ModelManager`

```mermaid
stateDiagram-v2
    [*] --> Unloaded: init

    Unloaded --> Loading: first request
    Loading --> Loaded: model ready

    Loaded --> Loaded: request (reset timer)
    Loaded --> Unloading: 30min idle

    Unloading --> Unloaded: cleanup complete

    note right of Loaded
        BGE-M3: uso depende de backend e precisão
        Reranker: uso depende de versão e device
    end note

    note right of Unloading
        gc.collect()
        torch.cuda.empty_cache()
    end note
```
