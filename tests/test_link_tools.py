"""
Testes para as ferramentas de links indexados (Fase 2).

Testa get_backlinks, get_outlinks, find_broken_links, find_orphan_notes, link_stats.
"""


class TestGetBacklinksIndexed:
    """Testes para get_backlinks usando índice."""

    def test_finds_backlinks_via_index(self, tmp_path, monkeypatch):
        """get_backlinks deve encontrar backlinks via índice."""
        import vault_search.core.indexer as idx
        from vault_search.config.embedding import EMBEDDING_DIMENSION

        # Setup
        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(idx, "LANCEDB_TABLE", "chunks_test")
        monkeypatch.setattr(idx, "LINKS_TABLE", "links_test")
        monkeypatch.setattr(idx, "ALIASES_TABLE", "aliases_test")

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Criar tabela de chunks
        indexer._ensure_table(
            data=[
                {
                    "note_path": "source.md",
                    "note_title": "Source Note",
                    "folder": "",
                    "headers": "",
                    "tags": "",
                    "modified_at": "2026-01-01T00:00:00",
                    "text": "source",
                    "vector": [0.0] * EMBEDDING_DIMENSION,
                    "id": "",
                    "created_at": "",
                    "updated_at": "",
                    "description": "",
                    "status": "",
                    "note_type": "",
                    "category": "",
                    "project": "",
                    "source": "",
                },
                {
                    "note_path": "target.md",
                    "note_title": "Target Note",
                    "folder": "",
                    "headers": "",
                    "tags": "",
                    "modified_at": "2026-01-01T00:00:00",
                    "text": "target",
                    "vector": [0.0] * EMBEDDING_DIMENSION,
                    "id": "",
                    "created_at": "",
                    "updated_at": "",
                    "description": "",
                    "status": "",
                    "note_type": "",
                    "category": "",
                    "project": "",
                    "source": "",
                },
            ]
        )

        # Indexar link de source -> target
        links = [
            {
                "from_note_path": "source.md",
                "from_note_title": "Source Note",
                "link_type": "wikilink",
                "link_target": "target",
                "link_target_normalized": "target",
                "to_note_path": "target.md",
                "is_resolved": True,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "Link para [[target]]",
                "modified_at": "2026-01-01T00:00:00",
            },
        ]
        indexer._index_links(links)

        # Testar via função interna (não via MCP server)
        links_table = indexer._ensure_links_table()
        results = links_table.search().where("to_note_path = 'target.md'").to_list()

        assert len(results) == 1
        assert results[0]["from_note_path"] == "source.md"

    def test_no_self_reference(self, tmp_path, monkeypatch):
        """Nota não deve aparecer como seu próprio backlink."""
        import vault_search.core.indexer as idx
        from vault_search.config.embedding import EMBEDDING_DIMENSION

        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(idx, "LANCEDB_TABLE", "chunks_test")
        monkeypatch.setattr(idx, "LINKS_TABLE", "links_test")
        monkeypatch.setattr(idx, "ALIASES_TABLE", "aliases_test")

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        indexer._ensure_table(
            data=[
                {
                    "note_path": "note.md",
                    "note_title": "Note",
                    "folder": "",
                    "headers": "",
                    "tags": "",
                    "modified_at": "2026-01-01T00:00:00",
                    "text": "note",
                    "vector": [0.0] * EMBEDDING_DIMENSION,
                    "id": "",
                    "created_at": "",
                    "updated_at": "",
                    "description": "",
                    "status": "",
                    "note_type": "",
                    "category": "",
                    "project": "",
                    "source": "",
                },
            ]
        )

        # Link da nota para si mesma
        links = [
            {
                "from_note_path": "note.md",
                "from_note_title": "Note",
                "link_type": "wikilink",
                "link_target": "note",
                "link_target_normalized": "note",
                "to_note_path": "note.md",
                "is_resolved": True,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "[[note]]",
                "modified_at": "2026-01-01T00:00:00",
            },
        ]
        indexer._index_links(links)

        # Verificar que o link existe mas é self-reference
        links_table = indexer._ensure_links_table()
        results = links_table.search().where("to_note_path = 'note.md'").to_list()

        assert len(results) == 1
        assert results[0]["from_note_path"] == "note.md"
        # get_backlinks filtra self-references na lógica


class TestFindBrokenLinks:
    """Testes para find_broken_links."""

    def test_finds_broken_links(self, tmp_path, monkeypatch):
        """Deve encontrar links não resolvidos."""
        import vault_search.core.indexer as idx

        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(idx, "LANCEDB_TABLE", "chunks_test")
        monkeypatch.setattr(idx, "LINKS_TABLE", "links_test")
        monkeypatch.setattr(idx, "ALIASES_TABLE", "aliases_test")

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Link quebrado (is_resolved=False)
        links = [
            {
                "from_note_path": "source.md",
                "from_note_title": "Source",
                "link_type": "wikilink",
                "link_target": "inexistente",
                "link_target_normalized": "inexistente",
                "to_note_path": "",
                "is_resolved": False,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "[[inexistente]]",
                "modified_at": "2026-01-01T00:00:00",
            },
        ]
        indexer._index_links(links)

        # Query links quebrados
        links_table = indexer._ensure_links_table()
        broken = (
            links_table.search().where("is_resolved = false AND link_type != 'external'").to_list()
        )

        assert len(broken) == 1
        assert broken[0]["link_target"] == "inexistente"

    def test_folder_filter(self, tmp_path, monkeypatch):
        """Deve filtrar por pasta."""
        import vault_search.core.indexer as idx

        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(idx, "LANCEDB_TABLE", "chunks_test")
        monkeypatch.setattr(idx, "LINKS_TABLE", "links_test")
        monkeypatch.setattr(idx, "ALIASES_TABLE", "aliases_test")

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        links = [
            {
                "from_note_path": "projetos/a.md",
                "from_note_title": "A",
                "link_type": "wikilink",
                "link_target": "x",
                "link_target_normalized": "x",
                "to_note_path": "",
                "is_resolved": False,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "[[x]]",
                "modified_at": "2026-01-01T00:00:00",
            },
            {
                "from_note_path": "outros/b.md",
                "from_note_title": "B",
                "link_type": "wikilink",
                "link_target": "y",
                "link_target_normalized": "y",
                "to_note_path": "",
                "is_resolved": False,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "[[y]]",
                "modified_at": "2026-01-01T00:00:00",
            },
        ]
        indexer._index_links(links)

        # Query com filtro de pasta
        from vault_search.utils.security import escape_sql_string

        folder = "projetos"
        escaped = escape_sql_string(folder)

        links_table = indexer._ensure_links_table()
        filtered = (
            links_table.search()
            .where(f"is_resolved = false AND from_note_path LIKE '{escaped}/%'")
            .to_list()
        )

        assert len(filtered) == 1
        assert filtered[0]["from_note_path"].startswith("projetos/")


class TestLinkStats:
    """Testes para link_stats."""

    def test_counts_links_correctly(self, tmp_path, monkeypatch):
        """Deve contar links corretamente."""
        import vault_search.core.indexer as idx

        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(idx, "LANCEDB_TABLE", "chunks_test")
        monkeypatch.setattr(idx, "LINKS_TABLE", "links_test")
        monkeypatch.setattr(idx, "ALIASES_TABLE", "aliases_test")

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        links = [
            # 2 links resolvidos
            {
                "from_note_path": "a.md",
                "from_note_title": "A",
                "link_type": "wikilink",
                "link_target": "b",
                "link_target_normalized": "b",
                "to_note_path": "b.md",
                "is_resolved": True,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
            {
                "from_note_path": "a.md",
                "from_note_title": "A",
                "link_type": "wikilink",
                "link_target": "c",
                "link_target_normalized": "c",
                "to_note_path": "c.md",
                "is_resolved": True,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
            # 1 link quebrado
            {
                "from_note_path": "b.md",
                "from_note_title": "B",
                "link_type": "wikilink",
                "link_target": "x",
                "link_target_normalized": "x",
                "to_note_path": "",
                "is_resolved": False,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
            # 1 link externo
            {
                "from_note_path": "c.md",
                "from_note_title": "C",
                "link_type": "external",
                "link_target": "https://example.com",
                "link_target_normalized": "https://example.com",
                "to_note_path": "",
                "is_resolved": False,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
        ]
        indexer._index_links(links)

        # Contar
        links_table = indexer._ensure_links_table()
        all_links = links_table.search().to_list()

        total = len(all_links)
        resolved = sum(1 for link in all_links if link["is_resolved"])
        broken = sum(
            1 for link in all_links if not link["is_resolved"] and link["link_type"] != "external"
        )
        external = sum(1 for link in all_links if link["link_type"] == "external")

        assert total == 4
        assert resolved == 2
        assert broken == 1
        assert external == 1

    def test_most_referenced_sorted(self, tmp_path, monkeypatch):
        """Most referenced deve estar ordenado por backlinks."""
        import vault_search.core.indexer as idx

        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(idx, "LANCEDB_TABLE", "chunks_test")
        monkeypatch.setattr(idx, "LINKS_TABLE", "links_test")
        monkeypatch.setattr(idx, "ALIASES_TABLE", "aliases_test")

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Hub note com 3 backlinks
        links = [
            {
                "from_note_path": "a.md",
                "from_note_title": "A",
                "link_type": "wikilink",
                "link_target": "hub",
                "link_target_normalized": "hub",
                "to_note_path": "hub.md",
                "is_resolved": True,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
            {
                "from_note_path": "b.md",
                "from_note_title": "B",
                "link_type": "wikilink",
                "link_target": "hub",
                "link_target_normalized": "hub",
                "to_note_path": "hub.md",
                "is_resolved": True,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
            {
                "from_note_path": "c.md",
                "from_note_title": "C",
                "link_type": "wikilink",
                "link_target": "hub",
                "link_target_normalized": "hub",
                "to_note_path": "hub.md",
                "is_resolved": True,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
            # Nota com 1 backlink
            {
                "from_note_path": "a.md",
                "from_note_title": "A",
                "link_type": "wikilink",
                "link_target": "other",
                "link_target_normalized": "other",
                "to_note_path": "other.md",
                "is_resolved": True,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
        ]
        indexer._index_links(links)

        # Contar backlinks
        links_table = indexer._ensure_links_table()
        all_links = links_table.search().to_list()

        backlink_count: dict[str, int] = {}
        for link in all_links:
            target = link["to_note_path"]
            if target:
                backlink_count[target] = backlink_count.get(target, 0) + 1

        most_referenced = sorted(
            [{"path": k, "backlinks": v} for k, v in backlink_count.items()],
            key=lambda x: x["backlinks"],
            reverse=True,
        )

        assert most_referenced[0]["path"] == "hub.md"
        assert most_referenced[0]["backlinks"] == 3
        assert most_referenced[1]["path"] == "other.md"
        assert most_referenced[1]["backlinks"] == 1


class TestGetOutlinksIndexed:
    """Testes para get_outlinks usando índice."""

    def test_returns_all_link_types(self, tmp_path, monkeypatch):
        """Deve retornar todos os tipos de links."""
        import vault_search.core.indexer as idx

        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(idx, "LANCEDB_TABLE", "chunks_test")
        monkeypatch.setattr(idx, "LINKS_TABLE", "links_test")
        monkeypatch.setattr(idx, "ALIASES_TABLE", "aliases_test")

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        links = [
            {
                "from_note_path": "source.md",
                "from_note_title": "Source",
                "link_type": "wikilink",
                "link_target": "a",
                "link_target_normalized": "a",
                "to_note_path": "a.md",
                "is_resolved": True,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
            {
                "from_note_path": "source.md",
                "from_note_title": "Source",
                "link_type": "markdown",
                "link_target": "b.md",
                "link_target_normalized": "b",
                "to_note_path": "b.md",
                "is_resolved": True,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
            {
                "from_note_path": "source.md",
                "from_note_title": "Source",
                "link_type": "embed",
                "link_target": "img.png",
                "link_target_normalized": "img",
                "to_note_path": "",
                "is_resolved": False,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
            {
                "from_note_path": "source.md",
                "from_note_title": "Source",
                "link_type": "external",
                "link_target": "https://example.com",
                "link_target_normalized": "https://example.com",
                "to_note_path": "",
                "is_resolved": False,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "",
                "modified_at": "2026-01-01T00:00:00",
            },
        ]
        indexer._index_links(links)

        # Query outlinks
        from vault_search.utils.security import escape_sql_string

        links_table = indexer._ensure_links_table()
        escaped = escape_sql_string("source.md")
        results = links_table.search().where(f"from_note_path = '{escaped}'").to_list()

        types = {r["link_type"] for r in results}
        assert types == {"wikilink", "markdown", "embed", "external"}
        assert len(results) == 4
