"""
MCP tools for link-graph analysis.

Inclui: graph_data, suggest_links, find_link_clusters, find_bridge_notes.
"""

import logging
from collections import defaultdict
from pathlib import Path
from typing import NotRequired, TypedDict

from vault_search.crud.catalog import get_catalog
from vault_search.server.errors import public_error
from vault_search.utils.links import normalize_link_target
from vault_search.utils.security import escape_sql_string

logger = logging.getLogger("vault-search-mcp")


class GraphNode(TypedDict):
    """Serializable public graph node."""

    id: str
    label: str
    outlinks: int
    backlinks: int
    orphan: NotRequired[bool]


class GraphEdge(TypedDict):
    """Directed public graph edge."""

    source: str
    target: str


class GraphStats(TypedDict):
    """Aggregate counts for a graph response."""

    total_nodes: int
    total_edges: int
    orphan_nodes: int


class GraphPayload(TypedDict):
    """Payload produced by ``graph_data``."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    stats: GraphStats


def register_graph_tools(mcp, indexer, searcher):
    """
    Register graph-analysis tools on the MCP server.

    Parameters:
        mcp: FastMCP instance
        indexer: VaultIndexer instance
        searcher: VaultSearcher instance
    """

    @mcp.tool()
    def graph_data(
        folder: str | None = None,
        include_orphans: bool = False,
    ) -> GraphPayload | str:
        """
        Export link-graph data for visualization.

        The node-and-edge format can feed D3.js, Obsidian Graph, or Gephi.

        Parameters:
            folder: optional folder filter
            include_orphans: include notes without links

        Returns:
            Dictionary of visualization-ready nodes and edges.
        """
        logger.info(
            "graph_data folder_filter=%s include_orphans=%s",
            bool(folder),
            include_orphans,
        )

        try:
            links_table = indexer._ensure_links_table()
            catalog = get_catalog()

            # Read all internal links.
            where_clause = "link_type != 'external'"
            if folder:
                escaped = escape_sql_string(folder)
                where_clause += f" AND from_note_path LIKE '{escaped}/%'"

            query = (
                links_table.search()
                .where(where_clause)
                .select(
                    [
                        "from_note_path",
                        "from_note_title",
                        "to_note_path",
                        "is_resolved",
                    ]
                )
                .limit(100000)
            )

            links = query.to_list()

            # Build nodes.
            nodes_map: dict[str, GraphNode] = {}

            # Always include source nodes.
            for link in links:
                path = link["from_note_path"]
                if path not in nodes_map:
                    nodes_map[path] = {
                        "id": path,
                        "label": link["from_note_title"],
                        "outlinks": 0,
                        "backlinks": 0,
                    }
                nodes_map[path]["outlinks"] += 1

            # Include resolved destination nodes.
            for link in links:
                if link["is_resolved"] and link["to_note_path"]:
                    path = link["to_note_path"]
                    if path not in nodes_map:
                        # Derive a title from the path stem.
                        title = Path(path).stem.replace("-", " ").replace("_", " ").title()
                        nodes_map[path] = {
                            "id": path,
                            "label": title,
                            "outlinks": 0,
                            "backlinks": 0,
                        }
                    nodes_map[path]["backlinks"] += 1

            # Include orphan notes when requested.
            if include_orphans:
                all_notes, _ = catalog.list_notes(folder=folder, limit=10000)
                for note in all_notes:
                    if note["path"] not in nodes_map:
                        nodes_map[note["path"]] = {
                            "id": note["path"],
                            "label": note.get("title", note["path"]),
                            "outlinks": 0,
                            "backlinks": 0,
                            "orphan": True,
                        }

            # Build deduplicated edges.
            edges: list[GraphEdge] = []
            edge_set: set[tuple[str, str]] = set()

            for link in links:
                if link["is_resolved"] and link["to_note_path"]:
                    edge_key = (link["from_note_path"], link["to_note_path"])
                    if edge_key not in edge_set:
                        edge_set.add(edge_key)
                        edges.append(
                            {
                                "source": link["from_note_path"],
                                "target": link["to_note_path"],
                            }
                        )

            nodes = list(nodes_map.values())

            return {
                "nodes": nodes,
                "edges": edges,
                "stats": {
                    "total_nodes": len(nodes),
                    "total_edges": len(edges),
                    "orphan_nodes": sum(1 for n in nodes if n.get("orphan")),
                },
            }

        except Exception as e:
            return public_error(logger, "graph_data", e)

    @mcp.tool()
    def suggest_links(
        path: str,
        limit: int = 10,
        min_similarity: float = 0.7,
    ) -> list[dict[str, object]] | str:
        """
        Suggest links for a note based on semantic similarity.

        Finds similar notes that are not already linked.

        Parameters:
            path: note path
            limit: maximum number of suggestions
            min_similarity: minimum similarity score

        Returns:
            Suggested notes with similarity scores.
        """
        if not path or not path.strip():
            return "Error: path cannot be empty."

        path = path.strip()
        limit = max(1, min(limit, 50))
        logger.info(
            "suggest_links limit=%d min_similarity=%s",
            limit,
            min_similarity,
        )

        try:
            # Find similar notes.
            similar = searcher.find_similar(path, top_k=limit * 3)

            if isinstance(similar, str):
                return similar

            # Read the note's current outlinks from the index.
            links_table = indexer._ensure_links_table()
            escaped_path = escape_sql_string(path)

            outlinks_query = (
                links_table.search()
                .where(f"from_note_path = '{escaped_path}' AND link_type != 'external'")
                .select(["link_target_normalized", "to_note_path"])
                .limit(1000)
            )

            outlinks = outlinks_query.to_list()

            # Collect notes already linked from the source.
            already_linked = set()
            for link in outlinks:
                if link.get("to_note_path"):
                    already_linked.add(link["to_note_path"])
                already_linked.add(link["link_target_normalized"])

            # Filter suggestions.
            suggestions: list[dict[str, object]] = []
            for note in similar:
                note_path = note.get("note_path", note.get("path", ""))

                # Exclude the source note.
                if note_path == path:
                    continue

                # Exclude existing links.
                if note_path in already_linked:
                    continue
                if normalize_link_target(note_path) in already_linked:
                    continue

                # Apply the minimum similarity.
                score = note.get("similarity_score", note.get("score", 0))
                if score < min_similarity:
                    continue

                suggestions.append(
                    {
                        "path": note_path,
                        "title": note.get("note_title", note.get("title", note_path)),
                        "similarity": round(score, 3),
                        "folder": note.get("folder", str(Path(note_path).parent)),
                    }
                )

                if len(suggestions) >= limit:
                    break

            return suggestions

        except Exception as e:
            return public_error(logger, "suggest_links", e)

    @mcp.tool()
    def find_link_clusters(
        min_cluster_size: int = 3,
        folder: str | None = None,
    ) -> dict[str, object] | str:
        """
        Detect clusters of densely connected notes.

        Uses connected components to find isolated knowledge groups.

        Parameters:
            min_cluster_size: minimum cluster size
            folder: optional folder filter

        Returns:
            Clusters with notes and statistics.
        """
        min_cluster_size = max(2, min(min_cluster_size, 100))
        logger.info(
            "find_link_clusters min_size=%d folder_filter=%s",
            min_cluster_size,
            bool(folder),
        )

        try:
            # Read the graph.
            graph_result = graph_data(folder=folder, include_orphans=False)
            if isinstance(graph_result, str):
                return graph_result

            nodes = graph_result["nodes"]
            edges = graph_result["edges"]

            if not edges:
                return {
                    "total_clusters": 0,
                    "largest_cluster_size": 0,
                    "clusters": [],
                }

            # Build an undirected graph for connected components.
            adjacency: defaultdict[str, set[str]] = defaultdict(set)
            for edge in edges:
                adjacency[edge["source"]].add(edge["target"])
                adjacency[edge["target"]].add(edge["source"])

            # Find connected components with breadth-first search.
            visited: set[str] = set()
            clusters: list[list[str]] = []

            def bfs(start: str) -> list[str]:
                cluster: list[str] = []
                queue = [start]
                while queue:
                    node = queue.pop(0)
                    if node in visited:
                        continue
                    visited.add(node)
                    cluster.append(node)
                    for neighbor in adjacency[node]:
                        if neighbor not in visited:
                            queue.append(neighbor)
                return cluster

            for node in adjacency.keys():
                if node not in visited:
                    cluster = bfs(node)
                    if len(cluster) >= min_cluster_size:
                        clusters.append(cluster)

            # Enrich clusters with note data.
            node_map = {n["id"]: n for n in nodes}
            result_clusters: list[dict[str, object]] = []
            sorted_clusters = sorted(clusters, key=len, reverse=True)

            for i, cluster_paths in enumerate(sorted_clusters):
                cluster_nodes = [
                    node_map.get(
                        path,
                        {
                            "id": path,
                            "label": path,
                            "outlinks": 0,
                            "backlinks": 0,
                        },
                    )
                    for path in cluster_paths
                ]

                # Calculate internal edges divided by possible edges.
                cluster_set = set(cluster_paths)
                internal_edges = {
                    frozenset((edge["source"], edge["target"]))
                    for edge in edges
                    if edge["source"] in cluster_set
                    and edge["target"] in cluster_set
                    and edge["source"] != edge["target"]
                }
                possible_edges = len(cluster_paths) * (len(cluster_paths) - 1) / 2
                density = len(internal_edges) / max(possible_edges, 1)

                result_clusters.append(
                    {
                        "id": i + 1,
                        "size": len(cluster_paths),
                        "density": round(density, 3),
                        "notes": [
                            {"path": n["id"], "title": n["label"]} for n in cluster_nodes[:20]
                        ],
                        "truncated": len(cluster_paths) > 20,
                    }
                )

            return {
                "total_clusters": len(result_clusters),
                "largest_cluster_size": len(sorted_clusters[0]) if sorted_clusters else 0,
                "clusters": result_clusters[:20],
            }

        except Exception as e:
            return public_error(logger, "find_link_clusters", e)

    @mcp.tool()
    def find_bridge_notes(
        limit: int = 20,
        folder: str | None = None,
    ) -> dict[str, object] | str:
        """
        Find articulation points in the note graph.

        Removing an articulation point increases the number of connected
        components.

        Parameters:
            limit: maximum number of notes
            folder: optional folder filter

        Returns:
            Bridge notes ordered by impact.
        """
        limit = max(1, min(limit, 100))
        logger.info(
            "find_bridge_notes limit=%d folder_filter=%s",
            limit,
            bool(folder),
        )

        try:
            # Read the graph.
            graph_result = graph_data(folder=folder, include_orphans=False)
            if isinstance(graph_result, str):
                return graph_result

            nodes = graph_result["nodes"]
            edges = graph_result["edges"]

            if not edges:
                return {
                    "total_bridge_notes": 0,
                    "returned_notes": 0,
                    "has_more": False,
                    "notes": [],
                }

            # Build the graph.
            adjacency: defaultdict[str, set[str]] = defaultdict(set)
            for edge in edges:
                adjacency[edge["source"]].add(edge["target"])
                adjacency[edge["target"]].add(edge["source"])

            # Tarjan articulation points in O(V + E).
            discovery: dict[str, int] = {}
            low: dict[str, int] = {}
            parent: dict[str, str | None] = {}
            child_count: dict[str, int] = defaultdict(int)
            separated_count: dict[str, int] = defaultdict(int)
            articulation_scores: dict[str, int] = {}
            clock = 0

            for root in sorted(adjacency):
                if root in discovery:
                    continue
                parent[root] = None
                clock += 1
                discovery[root] = low[root] = clock
                stack = [(root, iter(sorted(adjacency[root])))]

                while stack:
                    node_id, neighbors = stack[-1]
                    try:
                        neighbor = next(neighbors)
                    except StopIteration:
                        stack.pop()
                        parent_id = parent[node_id]
                        if parent_id is None:
                            if child_count[node_id] > 1:
                                articulation_scores[node_id] = child_count[node_id] - 1
                        else:
                            low[parent_id] = min(low[parent_id], low[node_id])
                            if (
                                parent[parent_id] is not None
                                and low[node_id] >= discovery[parent_id]
                            ):
                                separated_count[parent_id] += 1
                            if separated_count[node_id]:
                                articulation_scores[node_id] = separated_count[node_id]
                        continue

                    if neighbor not in discovery:
                        parent[neighbor] = node_id
                        child_count[node_id] += 1
                        clock += 1
                        discovery[neighbor] = low[neighbor] = clock
                        stack.append((neighbor, iter(sorted(adjacency[neighbor]))))
                    elif neighbor != parent[node_id]:
                        low[node_id] = min(low[node_id], discovery[neighbor])

            # Ranking
            node_map = {n["id"]: n for n in nodes}
            ranked = sorted(
                articulation_scores.items(),
                key=lambda item: (-item[1], item[0]),
            )

            result: list[dict[str, object]] = []
            for path, score in ranked[:limit]:
                node = node_map.get(
                    path,
                    {
                        "id": path,
                        "label": path,
                        "outlinks": 0,
                        "backlinks": 0,
                    },
                )
                result.append(
                    {
                        "path": path,
                        "title": node.get("label", path),
                        "bridge_score": score,
                        "separated_branches": score,
                        "connections": len(adjacency[path]),
                    }
                )

            return {
                "total_bridge_notes": len(ranked),
                "returned_notes": len(result),
                "has_more": len(ranked) > len(result),
                "notes": result,
            }

        except Exception as e:
            return public_error(logger, "find_bridge_notes", e)
