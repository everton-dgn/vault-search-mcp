# Local daemon diagrams

## Daemon architecture

```mermaid
flowchart TB
    subgraph Daemon["Optional daemon (loopback HTTP)"]
        direction TB
        BGE["BGE-M3<br/>resident"]
        RR["Reranker<br/>resident"]
        HTTP[HTTP Server]
        BGE --> HTTP
        RR --> HTTP
    end

    subgraph MCP["MCP server (started by the client)"]
        direction TB
        MM[ModelManager]
        SE[Searcher]
        IX[Indexer]
        MM --> SE
        MM --> IX
    end

    HTTP -->|"POST /embed/queries<br/>POST /embed/corpus<br/>POST /rerank"| MM

    subgraph Fallback["Fallback (when the daemon is unavailable)"]
        LocalBGE["Local BGE-M3<br/>loaded in the process"]
        LocalRR["Local reranker"]
    end

    MM -.->|"_check_daemon() = false"| LocalBGE
    MM -.->|"_check_daemon() = false"| LocalRR
```

---

## Startup sequence

```mermaid
sequenceDiagram
    participant OS as Service manager (launchd/systemd)
    participant D as Daemon Server
    participant MM as ModelManager
    participant BGE as BGE-M3
    participant RR as Reranker
    participant HTTP as HTTP Server
    participant P as Probe local

    OS->>D: Start daemon
    D->>HTTP: bind(host, port)
    HTTP-->>D: loopback socket
    D->>MM: warmup() em thread
    P->>HTTP: GET /health
    HTTP-->>P: 503, status starting

    par Load models
        MM->>BGE: SentenceTransformer()
        Note over BGE: load according to backend and cache
        BGE-->>MM: Model ready

        MM->>RR: FlagReranker()
        Note over RR: load according to backend and cache
        RR-->>MM: Model ready
    end

    MM-->>D: Models loaded
    D->>D: status ready
    P->>HTTP: GET /health
    HTTP-->>P: 200, status ready
```

---

## MCP-to-daemon communication

```mermaid
sequenceDiagram
    participant CC as MCP client
    participant MCP as MCP Server
    participant MM as ModelManager
    participant DC as DaemonClient
    participant DS as Daemon Server

    CC->>MCP: search_vault("query")
    MCP->>MM: embed_queries(["query"])

    MM->>MM: _check_daemon()

    alt Health ready and models loaded
        MM->>DC: embed_queries(texts)
        DC->>DS: POST /embed/queries
        DS-->>DC: {"embeddings": [[...]]}
        DC-->>MM: embeddings
    else Daemon absent or not ready
        MM->>MM: _get_embed_model()
        Note over MM: Load BGE-M3 locally
        MM-->>MM: embeddings
    end

    MM-->>MCP: embeddings
    MCP-->>CC: SearchResult[]
```

---

## Daemon HTTP API

```mermaid
flowchart LR
    subgraph Endpoints
        H["/health<br/>GET"]
        S["/stats<br/>GET"]
        EQ["/embed/queries<br/>POST"]
        EC["/embed/corpus<br/>POST"]
        R["/rerank<br/>POST"]
    end

    subgraph Responses
        H --> |"200 when ready"| HR["status: ready<br/>models_loaded: bool<br/>model_status: {...}"]
        H --> |"503 otherwise"| HU["status: starting, degraded, or failed"]
        S --> |200| SR["requests_served: N<br/>embed_queries_count: N<br/>rerank_count: N"]
        EQ --> |200| EQR["embeddings: [[1024 floats]]"]
        EC --> |200| ECR["embeddings: [[1024 floats]]"]
        R --> |200| RR["scores: [(idx, score), ...]"]
    end
```

---

## Graceful shutdown

```mermaid
sequenceDiagram
    participant SIG as Signal (SIGTERM/SIGINT)
    participant D as Daemon Server
    participant SM as ShutdownManager
    participant HTTP as HTTP Server
    participant MM as ModelManager

    SIG->>D: SIGTERM
    D->>SM: request_shutdown()
    SM->>SM: Set shutdown_event

    loop serve_forever
        D->>SM: shutdown_requested()?
        SM-->>D: True
    end

    D->>HTTP: server_close()
    D->>MM: cleanup()
    MM->>MM: Cancel timers
    MM->>MM: Release models

    Note over D: Daemon stopped
```

---

## Startup `sync_check`

```mermaid
flowchart TB
    Start([MCP server starts]) --> Check{Daemon available?}

    Check -->|Yes| UseDaemon[Use daemon for embedding and reranking]
    Check -->|No| LoadLocal[Load models locally]

    UseDaemon --> SyncCheck[sync_check]
    LoadLocal --> SyncCheck

    SyncCheck --> Scan[Scan filesystem]
    Scan --> Compare{Compare mtime<br/>with indexed state}

    Compare -->|New or modified files| Reindex[Reindex incrementally]
    Compare -->|No change| Ready[Server ready]

    Reindex --> Ready

    Ready --> Watch[Start filesystem watcher]
    Watch --> Serve([Serve requests])
```
