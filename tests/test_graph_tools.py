"""
Tests for graph-analysis tools.

Test graph_data, suggest_links, find_link_clusters, find_bridge_notes.
"""

from unittest.mock import MagicMock, patch


class TestGraphData:
    """Tests for graph_data()."""

    def test_returns_nodes_and_edges(self):
        """Must return structure with nodes and edges."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Simulate table of links
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_query.to_list.return_value = [
            {
                "from_note_path": "note-a.md",
                "from_note_title": "Note A",
                "to_note_path": "note-b.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "note-b.md",
                "from_note_title": "Note B",
                "to_note_path": "note-a.md",
                "is_resolved": True,
            },
        ]
        mock_table.search.return_value.where.return_value.select.return_value.limit.return_value = (
            mock_query
        )
        indexer._ensure_links_table.return_value = mock_table

        # Capture a function registered
        registered_tools = {}

        def capture_tool():
            def decorator(func):
                registered_tools[func.__name__] = func
                return func

            return decorator

        mcp.tool = capture_tool

        with patch("vault_search.server.graph_tools.get_catalog") as mock_catalog:
            mock_catalog.return_value.list_notes.return_value = ([], 0)
            register_graph_tools(mcp, indexer, searcher)

        graph_data = registered_tools["graph_data"]
        result = graph_data()

        assert "nodes" in result
        assert "edges" in result
        assert "stats" in result
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 2

    def test_includes_orphans_when_requested(self):
        """Must include notes orphaned when requested."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Simulate table of links empty
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_query.to_list.return_value = []
        mock_table.search.return_value.where.return_value.select.return_value.limit.return_value = (
            mock_query
        )
        indexer._ensure_links_table.return_value = mock_table

        registered_tools = {}

        def capture_tool():
            def decorator(func):
                registered_tools[func.__name__] = func
                return func

            return decorator

        mcp.tool = capture_tool

        with patch("vault_search.server.graph_tools.get_catalog") as mock_catalog:
            # Simulate note orphaned
            mock_catalog.return_value.list_notes.return_value = (
                [{"path": "orphan.md", "title": "Orphaned Note"}],
                1,
            )
            register_graph_tools(mcp, indexer, searcher)

            graph_data = registered_tools["graph_data"]
            result = graph_data(include_orphans=True)

        assert result["stats"]["orphan_nodes"] == 1
        assert any(n.get("orphan") for n in result["nodes"])


class TestSuggestLinks:
    """Tests for suggest_links()."""

    def test_suggests_similar_unlinked(self):
        """The tool must suggest similar unlinked notes."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Simulate similar notes.
        searcher.find_similar.return_value = [
            {"note_path": "similar-1.md", "note_title": "Similar 1", "similarity_score": 0.85},
            {"note_path": "similar-2.md", "note_title": "Similar 2", "similarity_score": 0.75},
        ]

        # Simulate table of links (without links)
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_query.to_list.return_value = []
        mock_table.search.return_value.where.return_value.select.return_value.limit.return_value = (
            mock_query
        )
        indexer._ensure_links_table.return_value = mock_table

        registered_tools = {}

        def capture_tool():
            def decorator(func):
                registered_tools[func.__name__] = func
                return func

            return decorator

        mcp.tool = capture_tool
        register_graph_tools(mcp, indexer, searcher)

        suggest_links = registered_tools["suggest_links"]
        result = suggest_links("note.md")

        assert len(result) == 2
        assert result[0]["path"] == "similar-1.md"
        assert result[0]["similarity"] == 0.85

    def test_excludes_already_linked(self):
        """The tool must exclude notes that are already linked."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Simulate similar notes.
        searcher.find_similar.return_value = [
            {
                "note_path": "already-linked.md",
                "note_title": "Already Linked",
                "similarity_score": 0.9,
            },
            {"note_path": "not-linked.md", "note_title": "Not Linked", "similarity_score": 0.8},
        ]

        # Simulate a link table that already links to already-linked.md.
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_query.to_list.return_value = [
            {"link_target_normalized": "already-linked", "to_note_path": "already-linked.md"}
        ]
        mock_table.search.return_value.where.return_value.select.return_value.limit.return_value = (
            mock_query
        )
        indexer._ensure_links_table.return_value = mock_table

        registered_tools = {}

        def capture_tool():
            def decorator(func):
                registered_tools[func.__name__] = func
                return func

            return decorator

        mcp.tool = capture_tool
        register_graph_tools(mcp, indexer, searcher)

        suggest_links = registered_tools["suggest_links"]
        result = suggest_links("note.md")

        # Only not-linked.md should be suggested.
        assert len(result) == 1
        assert result[0]["path"] == "not-linked.md"

    def test_respects_min_similarity(self):
        """The tool must respect the minimum similarity."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        searcher.find_similar.return_value = [
            {"note_path": "high.md", "note_title": "High", "similarity_score": 0.9},
            {"note_path": "low.md", "note_title": "Low", "similarity_score": 0.5},
        ]

        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_query.to_list.return_value = []
        mock_table.search.return_value.where.return_value.select.return_value.limit.return_value = (
            mock_query
        )
        indexer._ensure_links_table.return_value = mock_table

        registered_tools = {}

        def capture_tool():
            def decorator(func):
                registered_tools[func.__name__] = func
                return func

            return decorator

        mcp.tool = capture_tool
        register_graph_tools(mcp, indexer, searcher)

        suggest_links = registered_tools["suggest_links"]
        result = suggest_links("note.md", min_similarity=0.7)

        # Only high.md should be suggested because its score is 0.9 >= 0.7.
        assert len(result) == 1
        assert result[0]["path"] == "high.md"


class TestFindLinkClusters:
    """Tests for find_link_clusters()."""

    def test_finds_clusters(self):
        """The tool must find clusters of connected notes."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Create a graph with two clusters: {a,b,c} and {d,e}.
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_query.to_list.return_value = [
            # Cluster 1
            {
                "from_note_path": "a.md",
                "from_note_title": "A",
                "to_note_path": "b.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "b.md",
                "from_note_title": "B",
                "to_note_path": "c.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "c.md",
                "from_note_title": "C",
                "to_note_path": "a.md",
                "is_resolved": True,
            },
            # Cluster 2
            {
                "from_note_path": "d.md",
                "from_note_title": "D",
                "to_note_path": "and.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "and.md",
                "from_note_title": "E",
                "to_note_path": "d.md",
                "is_resolved": True,
            },
        ]
        mock_table.search.return_value.where.return_value.select.return_value.limit.return_value = (
            mock_query
        )
        indexer._ensure_links_table.return_value = mock_table

        registered_tools = {}

        def capture_tool():
            def decorator(func):
                registered_tools[func.__name__] = func
                return func

            return decorator

        mcp.tool = capture_tool

        with patch("vault_search.server.graph_tools.get_catalog") as mock_catalog:
            mock_catalog.return_value.list_notes.return_value = ([], 0)
            register_graph_tools(mcp, indexer, searcher)

        find_link_clusters = registered_tools["find_link_clusters"]
        result = find_link_clusters(min_cluster_size=2)

        assert result["total_clusters"] == 2
        assert result["largest_cluster_size"] == 3
        assert result["clusters"][0]["density"] == 1.0

    def test_respects_min_size(self):
        """The tool must respect the minimum cluster size."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Create a graph with a two-note cluster.
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_query.to_list.return_value = [
            {
                "from_note_path": "a.md",
                "from_note_title": "A",
                "to_note_path": "b.md",
                "is_resolved": True,
            },
        ]
        mock_table.search.return_value.where.return_value.select.return_value.limit.return_value = (
            mock_query
        )
        indexer._ensure_links_table.return_value = mock_table

        registered_tools = {}

        def capture_tool():
            def decorator(func):
                registered_tools[func.__name__] = func
                return func

            return decorator

        mcp.tool = capture_tool

        with patch("vault_search.server.graph_tools.get_catalog") as mock_catalog:
            mock_catalog.return_value.list_notes.return_value = ([], 0)
            register_graph_tools(mcp, indexer, searcher)

            find_link_clusters = registered_tools["find_link_clusters"]

            # With min_size=2, must find the cluster
            result = find_link_clusters(min_cluster_size=2)
            assert result["total_clusters"] == 1

            # With min_size=5, must not find
            result = find_link_clusters(min_cluster_size=5)
            assert result["total_clusters"] == 0


class TestFindBridgeNotes:
    """Tests for find_bridge_notes()."""

    def test_finds_bridges(self):
        """Find bridge notes between clusters."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Create a graph where bridge.md connects two groups.
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_query.to_list.return_value = [
            # Group A connected to the bridge.
            {
                "from_note_path": "a1.md",
                "from_note_title": "A1",
                "to_note_path": "bridge.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "a2.md",
                "from_note_title": "A2",
                "to_note_path": "bridge.md",
                "is_resolved": True,
            },
            # Group B connected to the bridge.
            {
                "from_note_path": "bridge.md",
                "from_note_title": "Bridge",
                "to_note_path": "b1.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "bridge.md",
                "from_note_title": "Bridge",
                "to_note_path": "b2.md",
                "is_resolved": True,
            },
        ]
        mock_table.search.return_value.where.return_value.select.return_value.limit.return_value = (
            mock_query
        )
        indexer._ensure_links_table.return_value = mock_table

        registered_tools = {}

        def capture_tool():
            def decorator(func):
                registered_tools[func.__name__] = func
                return func

            return decorator

        mcp.tool = capture_tool

        with patch("vault_search.server.graph_tools.get_catalog") as mock_catalog:
            mock_catalog.return_value.list_notes.return_value = ([], 0)
            register_graph_tools(mcp, indexer, searcher)

            find_bridge_notes = registered_tools["find_bridge_notes"]
            result = find_bridge_notes()

        # Bridge must have larger score
        assert result["total_bridge_notes"] > 0
        # bridge.md must be in the top
        bridge_note = next((n for n in result["notes"] if "bridge" in n["path"].lower()), None)
        assert bridge_note is not None
        assert bridge_note["bridge_score"] > 0

    def test_in_bridges_in_fully_connected(self):
        """Find no bridges in a fully connected graph."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Create a fully connected graph, or clique.
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_query.to_list.return_value = [
            {
                "from_note_path": "a.md",
                "from_note_title": "A",
                "to_note_path": "b.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "a.md",
                "from_note_title": "A",
                "to_note_path": "c.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "b.md",
                "from_note_title": "B",
                "to_note_path": "a.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "b.md",
                "from_note_title": "B",
                "to_note_path": "c.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "c.md",
                "from_note_title": "C",
                "to_note_path": "a.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "c.md",
                "from_note_title": "C",
                "to_note_path": "b.md",
                "is_resolved": True,
            },
        ]
        mock_table.search.return_value.where.return_value.select.return_value.limit.return_value = (
            mock_query
        )
        indexer._ensure_links_table.return_value = mock_table

        registered_tools = {}

        def capture_tool():
            def decorator(func):
                registered_tools[func.__name__] = func
                return func

            return decorator

        mcp.tool = capture_tool

        with patch("vault_search.server.graph_tools.get_catalog") as mock_catalog:
            mock_catalog.return_value.list_notes.return_value = ([], 0)
            register_graph_tools(mcp, indexer, searcher)

        find_bridge_notes = registered_tools["find_bridge_notes"]
        result = find_bridge_notes()

        # In a clique, every neighbor is connected, so bridge_score is 0.
        assert result["total_bridge_notes"] == 0
