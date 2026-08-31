# Graph tools

Four tools derive a note graph from the link index. External URLs do not become
graph edges. Examples are synthetic and document shape only.

## `graph_data`

```python
graph_data(folder: str | None = None, include_orphans: bool = False) -> dict | str
```

```python
{
    "nodes": [
        {"id": "notes/a.md", "label": "A", "outlinks": 1, "backlinks": 0},
        {"id": "notes/b.md", "label": "B", "outlinks": 0, "backlinks": 1},
    ],
    "edges": [{"source": "notes/a.md", "target": "notes/b.md"}],
    "stats": {"total_nodes": 2, "total_edges": 1, "orphan_nodes": 0},
}
```

`folder` restricts source notes. `include_orphans=True` adds catalog notes with
no known links. The link read is capped at 100,000 rows and the orphan catalog
at 10,000 notes, so larger vaults can produce a partial view.

The node-edge shape adapts easily to visualization libraries. It is not a
native Gephi or Obsidian file.

## `suggest_links`

```python
suggest_links(
    path: str,
    limit: int = 10,
    min_similarity: float = 0.7,
) -> list[dict] | str
```

Finds semantically related notes, then removes the source and already-linked
targets. `limit` is clamped from 1 through 50. Each item contains `path`,
`title`, `similarity`, and `folder`.

```python
[
    {
        "path": "notes/b.md",
        "title": "B",
        "similarity": 0.78,
        "folder": "notes",
    }
]
```

The similarity value is not a calibrated probability. Review both notes before
creating a link.

## `find_link_clusters`

```python
find_link_clusters(
    min_cluster_size: int = 3,
    folder: str | None = None,
) -> dict | str
```

Converts edges to an undirected graph and finds connected components with BFS.
`min_cluster_size` is clamped from 2 through 100. The response includes up to
20 clusters and 20 notes per cluster, with `truncated` when more exist.

```python
{
    "total_clusters": 0,
    "largest_cluster_size": 0,
    "clusters": [],
}
```

`density` uses the undirected simple-graph definition
`m / (n * (n - 1) / 2)`. Repeated edges are deduplicated and self-loops do not
count toward `m`, keeping the value between zero and one.

## `find_bridge_notes`

```python
find_bridge_notes(limit: int = 20, folder: str | None = None) -> dict | str
```

Runs iterative Tarjan traversal in `O(V + E)` and returns articulation points:
notes whose removal increases the number of connected components. `limit` is
clamped from 1 through 100.

```python
{
    "total_bridge_notes": 3,
    "returned_notes": 1,
    "has_more": True,
    "notes": [
        {
            "path": "notes/bridge.md",
            "title": "Bridge",
            "bridge_score": 2,
            "separated_branches": 2,
            "connections": 4,
        }
    ],
}
```

`total_bridge_notes` covers the observed graph. `returned_notes` and `has_more`
describe the bounded response. `separated_branches` is the structural score;
`bridge_score` remains a compatibility alias. If `graph_data` hit a read cap,
unseen edges are outside this calculation.

## Consistency

All graph tools read the latest indexed generation. After editing links, wait
for the watcher or call `reindex_note`. See [the link index](../features/link-index.md)
and [indexing tools](tools-indexing.md).
