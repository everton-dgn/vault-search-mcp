# Diagramas do daemon local

## Arquitetura do daemon

```mermaid
flowchart TB
    subgraph Daemon["Daemon opcional (HTTP em loopback)"]
        direction TB
        BGE["BGE-M3<br/>residente"]
        RR["Reranker<br/>residente"]
        HTTP[HTTP Server]
        BGE --> HTTP
        RR --> HTTP
    end

    subgraph MCP["MCP Server (iniciado pelo cliente)"]
        direction TB
        MM[ModelManager]
        SE[Searcher]
        IX[Indexer]
        MM --> SE
        MM --> IX
    end

    HTTP -->|"POST /embed/queries<br/>POST /embed/corpus<br/>POST /rerank"| MM

    subgraph Fallback["Fallback (se daemon não disponível)"]
        LocalBGE["BGE-M3 local<br/>carregado no processo"]
        LocalRR["Reranker local"]
    end

    MM -.->|"_check_daemon() = false"| LocalBGE
    MM -.->|"_check_daemon() = false"| LocalRR
```

---

## Sequência de inicialização

```mermaid
sequenceDiagram
    participant OS as Sistema (launchd/systemd)
    participant D as Daemon Server
    participant MM as ModelManager
    participant BGE as BGE-M3
    participant RR as Reranker
    participant HTTP as HTTP Server
    participant P as Probe local

    OS->>D: Iniciar daemon (boot)
    D->>HTTP: bind(host, port)
    HTTP-->>D: socket em loopback
    D->>MM: warmup() em thread
    P->>HTTP: GET /health
    HTTP-->>P: 503, status starting

    par Carregar modelos
        MM->>BGE: SentenceTransformer()
        Note over BGE: carrega conforme backend e cache
        BGE-->>MM: Modelo pronto

        MM->>RR: FlagReranker()
        Note over RR: carrega conforme backend e cache
        RR-->>MM: Modelo pronto
    end

    MM-->>D: Modelos carregados
    D->>D: status ready
    P->>HTTP: GET /health
    HTTP-->>P: 200, status ready
```

---

## Comunicação entre MCP e daemon

```mermaid
sequenceDiagram
    participant CC as Cliente MCP
    participant MCP as MCP Server
    participant MM as ModelManager
    participant DC as DaemonClient
    participant DS as Daemon Server

    CC->>MCP: search_vault("query")
    MCP->>MM: embed_queries(["query"])

    MM->>MM: _check_daemon()

    alt Health ready e modelos carregados
        MM->>DC: embed_queries(texts)
        DC->>DS: POST /embed/queries
        DS-->>DC: {"embeddings": [[...]]}
        DC-->>MM: embeddings
    else Daemon ausente ou fora de ready
        MM->>MM: _get_embed_model()
        Note over MM: Carrega BGE-M3 localmente
        MM-->>MM: embeddings
    end

    MM-->>MCP: embeddings
    MCP-->>CC: SearchResult[]
```

---

## API HTTP do daemon

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
        H --> |"200 se ready"| HR["status: ready<br/>models_loaded: bool<br/>model_status: {...}"]
        H --> |"503 fora de ready"| HU["status: starting, degraded ou failed"]
        S --> |200| SR["requests_served: N<br/>embed_queries_count: N<br/>rerank_count: N"]
        EQ --> |200| EQR["embeddings: [[1024 floats]]"]
        EC --> |200| ECR["embeddings: [[1024 floats]]"]
        R --> |200| RR["scores: [(idx, score), ...]"]
    end
```

---

## Encerramento gracioso

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

    Note over D: Daemon finalizado
```

---

## `sync_check` na inicialização

```mermaid
flowchart TB
    Start([MCP Server inicia]) --> Check{Daemon disponível?}

    Check -->|Sim| UseDaemon[Usar daemon para embed/rerank]
    Check -->|Não| LoadLocal[Carregar modelos localmente]

    UseDaemon --> SyncCheck[sync_check]
    LoadLocal --> SyncCheck

    SyncCheck --> Scan[Scan filesystem]
    Scan --> Compare{Comparar mtime<br/>com último index}

    Compare -->|Arquivos novos/modificados| Reindex[Reindexar incrementalmente]
    Compare -->|Nenhuma mudança| Ready[Server pronto]

    Reindex --> Ready

    Ready --> Watch[Iniciar file watcher]
    Watch --> Serve([Servir requests])
```
