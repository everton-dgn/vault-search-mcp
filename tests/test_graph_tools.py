"""
Testes para ferramentas de análise de grafo.

Testa graph_data, suggest_links, find_link_clusters, find_bridge_notes.
"""

from unittest.mock import MagicMock, patch


class TestGraphData:
    """Testes para graph_data()."""

    def test_returns_nodes_and_edges(self):
        """Deve retornar estrutura com nodes e edges."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Simular tabela de links
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_query.to_list.return_value = [
            {
                "from_note_path": "nota-a.md",
                "from_note_title": "Nota A",
                "to_note_path": "nota-b.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "nota-b.md",
                "from_note_title": "Nota B",
                "to_note_path": "nota-a.md",
                "is_resolved": True,
            },
        ]
        mock_table.search.return_value.where.return_value.select.return_value.limit.return_value = (
            mock_query
        )
        indexer._ensure_links_table.return_value = mock_table

        # Capturar a função registrada
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
        """Deve incluir notas órfãs quando solicitado."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Simular tabela de links vazia
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
            # Simular nota órfã
            mock_catalog.return_value.list_notes.return_value = (
                [{"path": "orfa.md", "title": "Nota Órfã"}],
                1,
            )
            register_graph_tools(mcp, indexer, searcher)

            graph_data = registered_tools["graph_data"]
            result = graph_data(include_orphans=True)

        assert result["stats"]["orphan_nodes"] == 1
        assert any(n.get("orphan") for n in result["nodes"])


class TestSuggestLinks:
    """Testes para suggest_links()."""

    def test_suggests_similar_unlinked(self):
        """Deve sugerir notas similares não linkadas."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Simular notas similares
        searcher.find_similar.return_value = [
            {"note_path": "similar-1.md", "note_title": "Similar 1", "similarity_score": 0.85},
            {"note_path": "similar-2.md", "note_title": "Similar 2", "similarity_score": 0.75},
        ]

        # Simular tabela de links (sem links)
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
        result = suggest_links("nota.md")

        assert len(result) == 2
        assert result[0]["path"] == "similar-1.md"
        assert result[0]["similarity"] == 0.85

    def test_excludes_already_linked(self):
        """Deve excluir notas já linkadas."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Simular notas similares
        searcher.find_similar.return_value = [
            {
                "note_path": "already-linked.md",
                "note_title": "Already Linked",
                "similarity_score": 0.9,
            },
            {"note_path": "not-linked.md", "note_title": "Not Linked", "similarity_score": 0.8},
        ]

        # Simular tabela de links (já linka para already-linked.md)
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
        result = suggest_links("nota.md")

        # Só deve sugerir not-linked.md
        assert len(result) == 1
        assert result[0]["path"] == "not-linked.md"

    def test_respects_min_similarity(self):
        """Deve respeitar similaridade mínima."""
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
        result = suggest_links("nota.md", min_similarity=0.7)

        # Só deve sugerir high.md (score 0.9 >= 0.7)
        assert len(result) == 1
        assert result[0]["path"] == "high.md"


class TestFindLinkClusters:
    """Testes para find_link_clusters()."""

    def test_finds_clusters(self):
        """Deve encontrar clusters de notas conectadas."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Criar grafo com 2 clusters: {a,b,c} e {d,e}
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
                "to_note_path": "e.md",
                "is_resolved": True,
            },
            {
                "from_note_path": "e.md",
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
        """Deve respeitar tamanho mínimo de cluster."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Criar grafo com cluster de 2 notas
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

            # Com min_size=2, deve encontrar o cluster
            result = find_link_clusters(min_cluster_size=2)
            assert result["total_clusters"] == 1

            # Com min_size=5, não deve encontrar
            result = find_link_clusters(min_cluster_size=5)
            assert result["total_clusters"] == 0


class TestFindBridgeNotes:
    """Testes para find_bridge_notes()."""

    def test_finds_bridges(self):
        """Deve encontrar notas ponte entre clusters."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Criar grafo onde 'bridge.md' conecta dois grupos
        mock_table = MagicMock()
        mock_query = MagicMock()
        mock_query.to_list.return_value = [
            # Grupo A conectado à bridge
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
            # Grupo B conectado à bridge
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

        # Bridge deve ter maior score
        assert result["total_bridge_notes"] > 0
        # bridge.md deve estar no topo
        bridge_note = next((n for n in result["notes"] if "bridge" in n["path"].lower()), None)
        assert bridge_note is not None
        assert bridge_note["bridge_score"] > 0

    def test_no_bridges_in_fully_connected(self):
        """Não deve encontrar bridges em grafo totalmente conectado."""
        from vault_search.server.graph_tools import register_graph_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        # Criar grafo totalmente conectado (clique)
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

        # Em clique, todos os vizinhos estão conectados, então bridge_score = 0
        assert result["total_bridge_notes"] == 0
