"""
Testes para MCP Resources (vault://...).
"""

from unittest.mock import MagicMock, patch


class TestResourceRegistration:
    """Testes para registro de resources."""

    def test_register_resources_creates_six_resources(self):
        """register_resources deve criar 6 resources."""
        from vault_search.server.resource_tools import register_resources

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        register_resources(mcp, indexer, searcher)

        # Verifica que mcp.resource foi chamado 6 vezes
        assert mcp.resource.call_count == 6

    def test_resource_uris_are_correct(self):
        """Resources devem ter URIs corretas."""
        from vault_search.server.resource_tools import register_resources

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        register_resources(mcp, indexer, searcher)

        # Coletar URIs registradas
        uris = [call[0][0] for call in mcp.resource.call_args_list]

        assert "vault://stats" in uris
        assert "vault://folders" in uris
        assert "vault://notes" in uris
        assert "vault://notes/{path*}" in uris
        assert "vault://search/recent" in uris
        assert "vault://tags" in uris


class TestVaultStatsResource:
    """Testes para vault://stats."""

    def test_returns_dict_with_uri_and_type(self):
        """vault://stats deve retornar dict com uri e type."""
        from vault_search.server.resource_tools import register_resources

        mcp = MagicMock()
        indexer = MagicMock()
        indexer.get_stats.return_value = {"total_chunks": 100, "unique_notes": 10}
        searcher = MagicMock()

        # Capturar a função registrada
        captured_func = None

        def capture_resource(uri):
            def decorator(func):
                nonlocal captured_func
                if uri == "vault://stats":
                    captured_func = func
                return func

            return decorator

        mcp.resource = capture_resource
        register_resources(mcp, indexer, searcher)

        # Chamar a função com mock de Context
        ctx = MagicMock()
        result = captured_func(ctx)

        assert result["uri"] == "vault://stats"
        assert result["type"] == "statistics"
        assert "data" in result


class TestVaultNotesResource:
    """Testes para vault://notes/{path*}."""

    def test_validates_path(self):
        """vault://notes/{path} deve validar path."""
        from vault_search.server.resource_tools import register_resources

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        captured_func = None

        def capture_resource(uri):
            def decorator(func):
                nonlocal captured_func
                if uri == "vault://notes/{path*}":
                    captured_func = func
                return func

            return decorator

        mcp.resource = capture_resource
        register_resources(mcp, indexer, searcher)

        ctx = MagicMock()

        # Path traversal deve ser rejeitado pelo resolvedor real.
        with patch("vault_search.server.resource_tools.resolve_path") as mock_resolve:
            mock_resolve.side_effect = ValueError("fora do vault")
            result = captured_func("../../../etc/passwd", ctx)

            assert "error" in result
            assert "inválido" in result["error"]

    def test_rejects_non_readable_extensions(self):
        """vault://notes/{path} deve rejeitar extensões não legíveis."""
        from vault_search.server.resource_tools import register_resources

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        captured_func = None

        def capture_resource(uri):
            def decorator(func):
                nonlocal captured_func
                if uri == "vault://notes/{path*}":
                    captured_func = func
                return func

            return decorator

        mcp.resource = capture_resource
        register_resources(mcp, indexer, searcher)

        ctx = MagicMock()

        with patch("vault_search.server.resource_tools.resolve_path"):
            result = captured_func("nota.pdf", ctx)

            assert "error" in result
            assert ".pdf" in result["error"]


class TestVaultNotesListResource:
    """Testes para vault://notes."""

    def test_unpacks_catalog_page_and_preserves_total(self):
        """O resource deve separar os itens do total retornado pelo catálogo."""
        from vault_search.server.resource_tools import register_resources

        mcp = MagicMock()
        captured_func = None

        def capture_resource(uri):
            def decorator(func):
                nonlocal captured_func
                if uri == "vault://notes":
                    captured_func = func
                return func

            return decorator

        mcp.resource = capture_resource
        with patch("vault_search.server.resource_tools.get_catalog") as get_catalog:
            catalog = MagicMock()
            catalog.list_notes.return_value = (
                [
                    {
                        "path": "docs/example.md",
                        "title": "Example",
                        "folder": "docs",
                        "modified_at": "2026-01-01T00:00:00",
                    }
                ],
                5_001,
            )
            get_catalog.return_value = catalog
            register_resources(mcp, MagicMock(), MagicMock())

            result = captured_func(MagicMock())

        assert result["total"] == 5_001
        assert result["returned"] == 1
        assert result["limit"] == 5_000
        assert result["has_more"] is True
        assert result["notes"] == [
            {
                "path": "docs/example.md",
                "title": "Example",
                "folder": "docs",
                "modified_at": "2026-01-01T00:00:00",
            }
        ]
        catalog.list_notes.assert_called_once_with(limit=5000)


class TestVaultFoldersResource:
    """Testes para vault://folders."""

    def test_builds_tree_structure(self):
        """vault://folders deve construir árvore de pastas."""
        from vault_search.server.resource_tools import register_resources

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        captured_func = None

        def capture_resource(uri):
            def decorator(func):
                nonlocal captured_func
                if uri == "vault://folders":
                    captured_func = func
                return func

            return decorator

        mcp.resource = capture_resource

        with patch("vault_search.server.resource_tools.get_catalog") as mock_get_catalog:
            catalog = MagicMock()
            catalog.get_all_folders.return_value = ["a", "a/b", "c"]
            mock_get_catalog.return_value = catalog

            register_resources(mcp, indexer, searcher)

            ctx = MagicMock()
            result = captured_func(ctx)

            assert result["uri"] == "vault://folders"
            assert result["type"] == "folder_tree"
            assert result["total_folders"] == 3
            assert "tree" in result
            assert "a" in result["tree"]


class TestVaultTagsResource:
    """Testes para vault://tags."""

    def test_returns_tag_stats(self):
        """vault://tags deve retornar estatísticas de tags."""
        from vault_search.server.resource_tools import register_resources

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        captured_func = None

        def capture_resource(uri):
            def decorator(func):
                nonlocal captured_func
                if uri == "vault://tags":
                    captured_func = func
                return func

            return decorator

        mcp.resource = capture_resource

        table = MagicMock()
        table.count_rows.return_value = 3
        arrow_table = MagicMock()

        def column(name):
            values = {
                "note_path": ["a.md", "a.md", "b.md"],
                "tags": ["python, ai", "python", "ai"],
            }
            result = MagicMock()
            result.to_pylist.return_value = values[name]
            return result

        arrow_table.column.side_effect = column
        table.search.return_value.select.return_value.limit.return_value.to_arrow.return_value = (
            arrow_table
        )
        indexer._ensure_table.return_value = table
        register_resources(mcp, indexer, searcher)

        result = captured_func(MagicMock())

        assert result["uri"] == "vault://tags"
        assert result["type"] == "tag_stats"
        assert result["total_unique_tags"] == 2
        assert result["tags"] == [
            {"tag": "ai", "count": 2},
            {"tag": "python", "count": 1},
        ]


class TestVaultRecentResource:
    """Testes para vault://search/recent."""

    def test_uses_catalog_recent_contract(self):
        from vault_search.server.resource_tools import register_resources

        mcp = MagicMock()
        captured_func = None

        def capture_resource(uri):
            def decorator(func):
                nonlocal captured_func
                if uri == "vault://search/recent":
                    captured_func = func
                return func

            return decorator

        mcp.resource = capture_resource
        with patch("vault_search.server.resource_tools.get_catalog") as get_catalog:
            recent = [
                {
                    "path": "recent.md",
                    "title": "Recent",
                    "folder": "",
                    "extension": ".md",
                    "modified_at": "2026-01-01T00:00:00",
                    "size_bytes": 42,
                }
            ]
            get_catalog.return_value.get_recent_notes.return_value = recent
            register_resources(mcp, MagicMock(), MagicMock())

            result = captured_func(MagicMock())

        assert result["total"] == 1
        assert result["notes"] == recent
        get_catalog.return_value.get_recent_notes.assert_called_once_with(days=7, limit=50)
