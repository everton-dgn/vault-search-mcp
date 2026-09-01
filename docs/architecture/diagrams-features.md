# Feature and operation diagrams

## Layered caches

```mermaid
flowchart TB
    Request([Request]) --> L1{Query Cache}

    L1 -->|hit| L1Hit[Return cached embedding]
    L1 -->|miss| Compute[Compute embedding]
    Compute --> L1Store[Store in cache]
    L1Store --> Continue[Continue search]
    L1Hit --> Continue

    Continue --> L2{Metadata Cache}
    L2 -->|hit| L2Hit[Return cached metadata]
    L2 -->|miss| Read[Read frontmatter]
    Read --> L2Store[Store in cache]

    subgraph CacheKey["Cache Key = (path, mtime_ns, size)"]
        Read
    end

    L2Store --> L3{SQLite Catalog}
    L2Hit --> L3

    L3 -->|available| SQL[Indexed SQLite query]
    L3 -->|unavailable| Scan[Scan the vault]

    SQL --> Response([Response])
    Scan --> Response
```

---

## Data trust boundary

```mermaid
flowchart TB
    User[Operator manages the vault] --> Source[Vault files]
    Source --> Parse[Parser + Chunking]
    Parse --> Index[Indexing]
    Index --> Search[Search]
    Search --> Output[Text-faithful result]

    Note1[No runtime instruction-risk analysis]
    Note2[No per-tool rate limiting]
    Search -.-> Note1
    Search -.-> Note2
```

---

## Debounced filesystem watcher

```mermaid
sequenceDiagram
    participant FS as Filesystem
    participant W as Watcher
    participant Q as Queue
    participant WK as Worker
    participant IX as Indexer
    participant CAT as Catalog

    FS->>W: file modified
    W->>Q: enqueue(path, timestamp)

    Note over Q: Debounce 2s

    FS->>W: file modified (same)
    W->>Q: update timestamp (coalesce)

    Note over Q: Wait 2s since last event

    Q->>WK: dequeue(path)
    WK->>IX: reindex_note(path)
    WK->>CAT: upsert(path)

    IX-->>WK: result
    CAT-->>WK: ok
```

---

## Catalog reconciliation

```mermaid
flowchart TB
    Start([Every 2 minutes]) --> Scan[Scan filesystem]
    Scan --> Current[Set: current_files]

    Query[Query catalog] --> Cataloged[Set: cataloged_files]

    Current --> Compare{Compare sets}
    Cataloged --> Compare

    Compare --> Missing[missing = current - cataloged]
    Compare --> Extra[extra = cataloged - current]

    Missing --> |for each| Upsert[catalog.upsert]
    Extra --> |for each| Delete[catalog.delete]

    Upsert --> Done([Reconciliation complete])
    Delete --> Done
```

---

## Parsing pipeline

```mermaid
flowchart TB
    File([Input File]) --> Ext{Extension?}

    Ext -->|.md| MD[markdown.py]
    Ext -->|.canvas| Canvas[canvas.py]
    Ext -->|.pdf| PDF[pdf.py]
    Ext -->|other| Reject[Return empty]

    subgraph MarkdownParsing["Markdown Pipeline"]
        MD --> FM[Extract frontmatter]
        FM --> Tags[Extract tags]
        Tags --> Headers[Split by headers]
        Headers --> Chunk[chunk_text]
    end

    subgraph CanvasParsing["Canvas Pipeline"]
        Canvas --> Nodes[Extract text nodes]
        Nodes --> Groups[Extract group labels]
        Groups --> Edges[Extract edge labels]
        Edges --> ChunkC[chunk_text]
    end

    subgraph PDFParsing["PDF Pipeline"]
        PDF --> Pages[Extract pages]
        Pages --> OCR{Has images?}
        OCR -->|yes| Tesseract[OCR via Tesseract]
        OCR -->|no| Text[Text extraction]
        Tesseract --> ChunkP[chunk_text]
        Text --> ChunkP
    end

    Chunk --> Output([List[ChunkRecord]])
    ChunkC --> Output
    ChunkP --> Output
```

---

## Recursive chunking

```mermaid
flowchart TB
    Text([Input Text]) --> Size{len > CHUNK_SIZE?}

    Size -->|no| Return[Return as single chunk]

    Size -->|yes| Sep1{Split by '\n\n'?}
    Sep1 -->|success| Recurse1[Recurse on parts]
    Sep1 -->|fail| Sep2{Split by '\n'?}

    Sep2 -->|success| Recurse2[Recurse on parts]
    Sep2 -->|fail| Sep3{Split by '. '?}

    Sep3 -->|success| Recurse3[Recurse on parts]
    Sep3 -->|fail| Sep4{Split by ' '?}

    Sep4 -->|success| Recurse4[Recurse on parts]
    Sep4 -->|fail| Force[Force split at CHUNK_SIZE]

    Recurse1 --> Overlap[Add overlap between chunks]
    Recurse2 --> Overlap
    Recurse3 --> Overlap
    Recurse4 --> Overlap
    Force --> Overlap

    Overlap --> Output([List of chunks])
```

---

## Server startup with prewarming

```mermaid
sequenceDiagram
    participant M as main()
    participant C as Catalog Thread
    participant P as Prewarm Thread
    participant W as Watcher
    participant S as Searcher
    participant L as LanceDB

    M->>M: Create indexer, searcher, and watcher

    par Background Threads
        M->>C: Thread: _init_catalog()
        C->>C: initialize()
        C->>C: start_reconciliation()

        M->>P: Thread: _init_prewarm()
        P->>S: try_prewarm()
        S->>S: PREWARM_ENABLED?
        alt Enabled
            S->>L: list_indices()
            S->>L: count_rows()
            S->>S: estimate_size()
            S->>S: check_memory()
            alt RAM OK
                S->>L: prewarm_index(name)
        Note over L: Index loaded into RAM
            else RAM insufficient
                Note over S: Skip prewarm
            end
        end
        P-->>M: prewarm complete
    end

    M->>W: start()
    M->>M: mcp.run()

    Note over M: Server ready<br/>Queries use RAM if prewarmed
```

---

## Prewarm decision

```mermaid
flowchart TB
    Start([try_prewarm]) --> Enabled{PREWARM_ENABLED?}

    Enabled -->|False| Skip1[Skip: disabled in config]

    Enabled -->|True| Table{Table exists?}
    Table -->|No| Skip2[Skip: index not found]

    Table -->|Yes| Indices[list_indices]
    Indices --> HasIdx{Has indices?}
    HasIdx -->|No| Skip3[Skip: no indices]

    HasIdx -->|Yes| Estimate[Estimate size]
    Estimate --> CheckPsutil{psutil available?}
    CheckPsutil -->|No| Skip4[Skip: can't check RAM]

    CheckPsutil -->|Yes| CheckMin{RAM >= MIN_AVAILABLE?}
    CheckMin -->|No| Skip5[Skip: RAM too low]

    CheckMin -->|Yes| CheckPct{size < MAX_PERCENT * RAM?}
    CheckPct -->|No| Skip6[Skip: index too large]

    CheckPct -->|Yes| Prewarm[prewarm_index for each]
    Prewarm --> Success([Prewarm complete])

    Skip1 --> Status[Update _prewarm_status]
    Skip2 --> Status
    Skip3 --> Status
    Skip4 --> Status
    Skip5 --> Status
    Skip6 --> Status
    Success --> Status
```

---

## Indexed links

```mermaid
flowchart TB
    subgraph Indexing["Link indexing"]
        Parse[parse_note] --> Extract[Extract links]
        Extract --> Wiki[Wikilinks]
        Extract --> MD[Markdown links]
        Extract --> Embed[Embeds]
        Extract --> Ext[External URLs]

        Wiki --> Normalize[Normalize targets]
        MD --> Normalize
        Embed --> Normalize

        Normalize --> Resolve[Resolve to note_path]
        Resolve --> Store[(links_index)]
    end

    subgraph Tables["LanceDB tables"]
        Store --> LinksTable[(links_index)]
        Aliases[(note_aliases)]
        LinksTable -.-> Resolve
        Aliases -.-> Resolve
    end
```

---

## `get_backlinks` flow

```mermaid
sequenceDiagram
    participant C as Client
    participant T as get_backlinks
    participant L as links_index
    participant R as Result

    C->>T: get_backlinks("note.md")
    T->>T: normalize_link_target("note.md")
    T->>L: WHERE to_note_path = 'note.md'
    L-->>T: List[LinkRecord]
    T->>T: Filter self-references
    T->>T: Include context when requested
    T-->>C: List[BacklinkResult]

    Note over T,L: Query uses the link index
```

---

## Graph analysis

```mermaid
flowchart TB
    subgraph Tools["Graph tools"]
        GD[graph_data]
        SL[suggest_links]
        FC[find_link_clusters]
        FB[find_bridge_notes]
    end

    subgraph GraphData["graph_data()"]
        GD --> Nodes[Build nodes]
        GD --> Edges[Build edges]
        Nodes --> Stats[Calculate statistics]
        Edges --> Stats
        Stats --> JSON[Return D3.js-compatible JSON]
    end

    subgraph Clusters["find_link_clusters()"]
        FC --> Adjacency[Build adjacency]
        Adjacency --> BFS[BFS connected components]
        BFS --> Density[Calculate density]
        Density --> Rank[Sort by size]
    end

    subgraph Bridges["find_bridge_notes()"]
        FB --> Undirected[Undirected simple graph]
        Undirected --> Tarjan[Iterative Tarjan O(V + E)]
        Tarjan --> Points[Articulation points]
        Points --> TopK[Sort by separated_branches]
    end
```

---

## Link resolution

```mermaid
flowchart TB
    Link([Link target]) --> Norm[Normalize]

    Norm --> Match1{Exact match?}
    Match1 -->|yes| Found1[note_path found]

    Match1 -->|no| Match2{Stem match?}
    Match2 -->|yes| Found2[note_path found]

    Match2 -->|no| Match3{Alias match?}
    Match3 -->|yes| Found3[note_path through alias]

    Match3 -->|no| Unresolved[is_resolved = false]

    Found1 --> Resolved[is_resolved = true]
    Found2 --> Resolved
    Found3 --> Resolved

    subgraph Normalization["Normalization"]
        Norm --> Lower[lowercase]
        Lower --> NoExt[remove .md]
        NoExt --> Trim[strip spaces]
        Trim --> Hyphen[spaces to hyphens]
    end
```
