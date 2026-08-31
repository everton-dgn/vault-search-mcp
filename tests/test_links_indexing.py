"""
Testes de integração para o sistema de links indexados.

Testa a extração, indexação e resolução de links durante o reindex.
"""

import pytest

from vault_search.core.indexer import VaultIndexer
from vault_search.parsers.markdown import extract_aliases, parse_note
from vault_search.utils.links import (
    extract_all_links,
    extract_link_context,
    normalize_link_target,
    parse_wikilink_parts,
)


class TestExtractAliases:
    """Testes para extração de aliases do frontmatter."""

    def test_aliases_lista(self):
        fm = {"aliases": ["API Docs", "Documentação"]}
        aliases = extract_aliases(fm)
        assert aliases == ["API Docs", "Documentação"]

    def test_aliases_string_csv(self):
        fm = {"aliases": "API Docs, Documentação"}
        aliases = extract_aliases(fm)
        assert aliases == ["API Docs", "Documentação"]

    def test_alias_singular(self):
        fm = {"alias": "API Docs"}
        aliases = extract_aliases(fm)
        assert aliases == ["API Docs"]

    def test_alias_singular_lista(self):
        fm = {"alias": ["A", "B"]}
        aliases = extract_aliases(fm)
        assert aliases == ["A", "B"]

    def test_aliases_e_alias_combinados(self):
        fm = {"aliases": ["A", "B"], "alias": "C"}
        aliases = extract_aliases(fm)
        assert "A" in aliases
        assert "B" in aliases
        assert "C" in aliases

    def test_aliases_vazio(self):
        fm = {}
        aliases = extract_aliases(fm)
        assert aliases == []

    def test_aliases_none(self):
        fm = {"aliases": None}
        aliases = extract_aliases(fm)
        assert aliases == []


class TestParseNoteWithLinks:
    """Testes para parse_note com extração de links."""

    def test_nota_com_wikilinks(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "source.md"
        note.write_text(
            """---
title: Source
---
# Source

Link para [[Target]] e [[Other|alias]].
""",
            encoding="utf-8",
        )

        chunks, links, aliases = parse_note(note, vault)

        assert len(chunks) > 0
        assert len(links) >= 2

        # Verificar estrutura dos links
        link_targets = [link["link_target"] for link in links]
        assert "Target" in link_targets
        assert "Other" in link_targets

    def test_nota_com_markdown_links(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "source.md"
        note.write_text(
            """---
title: Source
---
# Source

Veja [documentação](docs/manual.md).
""",
            encoding="utf-8",
        )

        chunks, links, aliases = parse_note(note, vault)

        assert len(links) >= 1
        md_links = [link for link in links if link["link_type"] == "markdown"]
        assert len(md_links) >= 1
        assert "docs/manual.md" in [link["link_target"] for link in md_links]

    def test_nota_com_embeds(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "source.md"
        note.write_text(
            """---
title: Source
---
# Source

Imagem: ![[foto.png]]
""",
            encoding="utf-8",
        )

        chunks, links, aliases = parse_note(note, vault)

        assert len(links) >= 1
        embeds = [link for link in links if link["link_type"] == "embed"]
        assert len(embeds) >= 1
        assert "foto.png" in [link["link_target"] for link in embeds]

    def test_nota_com_aliases(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "api.md"
        note.write_text(
            """---
title: API Documentation
aliases: [API Docs, Documentação da API]
---
# API

Documentação da API.
""",
            encoding="utf-8",
        )

        chunks, links, aliases = parse_note(note, vault)

        assert "API Docs" in aliases
        assert "Documentação da API" in aliases

    def test_link_fields_completos(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "source.md"
        note.write_text(
            """---
title: Source Note
---
# Source

Link: [[Target#Section|alias]]
""",
            encoding="utf-8",
        )

        chunks, links, aliases = parse_note(note, vault)

        assert len(links) >= 1
        link = links[0]

        # Campos obrigatórios
        assert "from_note_path" in link
        assert "from_note_title" in link
        assert "link_type" in link
        assert "link_target" in link
        assert "link_target_normalized" in link
        assert "context" in link
        assert "modified_at" in link

        # Valores
        assert link["from_note_path"] == "source.md"
        assert link["from_note_title"] == "Source Note"
        assert link["link_type"] == "wikilink"
        assert link["link_target"] == "Target"
        assert link["heading"] == "Section"
        assert link["alias"] == "alias"


class TestNormalizeLinkTarget:
    """Testes adicionais para normalize_link_target."""

    @pytest.mark.parametrize(
        "input,expected",
        [
            ("Meu Projeto", "meu-projeto"),
            ("docs/API.md", "docs/api"),
            ("  nota  ", "nota"),
            ("UPPER CASE", "upper-case"),
            ("já-normalizado", "já-normalizado"),
            ("pasta/sub/nota.md", "pasta/sub/nota"),
            ("imagem.png", "imagem"),
            ("video.mp4", "video.mp4"),  # não remove extensões não-indexáveis
        ],
    )
    def test_normalization(self, input, expected):
        assert normalize_link_target(input) == expected


class TestParseWikilinkParts:
    """Testes para parsing de wikilinks completos."""

    @pytest.mark.parametrize(
        "input,expected",
        [
            ("Nota", {"target": "Nota", "alias": None, "heading": None, "block_ref": None}),
            (
                "Nota|alias",
                {"target": "Nota", "alias": "alias", "heading": None, "block_ref": None},
            ),
            (
                "Nota#Seção",
                {"target": "Nota", "alias": None, "heading": "Seção", "block_ref": None},
            ),
            (
                "Nota^block",
                {"target": "Nota", "alias": None, "heading": None, "block_ref": "block"},
            ),
            (
                "Nota#Seção|alias",
                {"target": "Nota", "alias": "alias", "heading": "Seção", "block_ref": None},
            ),
        ],
    )
    def test_parsing(self, input, expected):
        result = parse_wikilink_parts(input)
        assert result == expected


class TestExtractLinkContext:
    """Testes para extração de contexto de links."""

    def test_context_basico(self):
        content = "Este é um texto com [[link]] no meio."
        context = extract_link_context(content, "[[link]]")
        assert "[[link]]" in context
        assert "texto com" in context

    def test_context_truncado(self):
        content = "A" * 100 + "[[link]]" + "B" * 100
        context = extract_link_context(content, "[[link]]", window=20)
        assert "[[link]]" in context
        assert "..." in context

    def test_link_nao_encontrado(self):
        content = "Texto sem link."
        context = extract_link_context(content, "[[inexistente]]")
        assert context == ""


class TestExtractAllLinksStructure:
    """Testes para estrutura de retorno de extract_all_links."""

    def test_wikilinks_structure(self):
        text = "Link [[Nota#H1|alias]] aqui."
        result = extract_all_links(text)

        assert len(result["wikilinks"]) == 1
        wl = result["wikilinks"][0]
        assert wl["target"] == "Nota"
        assert wl["alias"] == "alias"
        assert wl["heading"] == "H1"
        assert "[[Nota#H1|alias]]" in wl["raw"]

    def test_markdown_links_structure(self):
        text = "Veja [docs](path/to/file.md) aqui."
        result = extract_all_links(text)

        assert len(result["markdown_links"]) == 1
        ml = result["markdown_links"][0]
        assert ml["target"] == "path/to/file.md"
        assert ml["text"] == "docs"

    def test_embeds_structure(self):
        text = "Imagem ![[foto.png]] aqui."
        result = extract_all_links(text)

        assert len(result["embeds"]) == 1
        emb = result["embeds"][0]
        assert emb["target"] == "foto.png"

    def test_external_urls_quando_habilitado(self):
        text = "Link https://example.com aqui."
        result = extract_all_links(text, include_external=True)

        assert "external" in result
        assert len(result["external"]) == 1
        assert result["external"][0]["url"] == "https://example.com"

    def test_external_urls_ignoradas_por_padrao(self):
        text = "Link https://example.com aqui."
        result = extract_all_links(text, include_external=False)

        assert "external" not in result or len(result.get("external", [])) == 0


class TestIndexerLinksIntegration:
    """Testes de integração do indexer com links."""

    def test_full_reindex_indexa_links(self, tmp_path, monkeypatch):
        """full_reindex deve extrair e indexar links."""
        vault = tmp_path / "vault"
        vault.mkdir()

        # Criar notas com links
        (vault / "source.md").write_text(
            """---
title: Source
---
# Source

Link para [[target]] e [[other]].
""",
            encoding="utf-8",
        )

        (vault / "target.md").write_text(
            """---
title: Target
---
# Target

Conteúdo.
""",
            encoding="utf-8",
        )

        # Monkeypatch no módulo do indexer (onde VAULT_PATH foi importado)
        import vault_search.core.indexer as idx

        monkeypatch.setattr(idx, "VAULT_PATH", vault)
        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")

        # Indexar
        indexer = VaultIndexer()
        stats = indexer.full_reindex()

        assert stats["total_notes"] >= 2
        assert stats.get("total_links", 0) >= 2

    def test_full_reindex_indexa_aliases(self, tmp_path, monkeypatch):
        """full_reindex deve extrair e indexar aliases."""
        vault = tmp_path / "vault"
        vault.mkdir()

        (vault / "api.md").write_text(
            """---
title: API Documentation
aliases: [API Docs, Documentação]
---
# API

Conteúdo.
""",
            encoding="utf-8",
        )

        # Monkeypatch no módulo do indexer (onde VAULT_PATH foi importado)
        import vault_search.core.indexer as idx

        monkeypatch.setattr(idx, "VAULT_PATH", vault)
        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")

        indexer = VaultIndexer()
        stats = indexer.full_reindex()

        assert stats.get("total_aliases", 0) >= 2

    def test_resolve_nao_colapsa_links_distintos(self, tmp_path, monkeypatch):
        """
        Regression test: resolução de links não deve colapsar links distintos.

        Bug corrigido: quando dois links (ex: wikilink e markdown) apontavam para
        o mesmo target normalizado, a resolução deletava ambos e adicionava apenas
        um, perdendo informação.

        Agora usamos (from_note_path, link_type, link_target) como chave única.
        """
        # Configurar paths temporários
        import vault_search.core.indexer as idx
        from vault_search.config.embedding import EMBEDDING_DIMENSION

        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(idx, "LANCEDB_TABLE", "chunks_test")
        monkeypatch.setattr(idx, "LINKS_TABLE", "links_test")
        monkeypatch.setattr(idx, "ALIASES_TABLE", "aliases_test")

        indexer = VaultIndexer()

        # Criar tabela de chunks com nota target
        indexer._ensure_table(
            data=[
                {
                    "note_path": "source.md",
                    "note_title": "source",
                    "folder": "",
                    "headers": "",
                    "tags": "",
                    "modified_at": "2026-01-01T00:00:00",
                    "text": "source text",
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
                    "note_title": "target",
                    "folder": "",
                    "headers": "",
                    "tags": "",
                    "modified_at": "2026-01-01T00:00:00",
                    "text": "target text",
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

        # Indexar dois links DISTINTOS para o mesmo target normalizado
        links = [
            {
                "from_note_path": "source.md",
                "from_note_title": "source",
                "link_type": "wikilink",
                "link_target": "Target",
                "link_target_normalized": "target",
                "to_note_path": "",
                "is_resolved": False,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "via wikilink",
                "modified_at": "2026-01-01T00:00:00",
            },
            {
                "from_note_path": "source.md",
                "from_note_title": "source",
                "link_type": "markdown",
                "link_target": "target.md",
                "link_target_normalized": "target",
                "to_note_path": "",
                "is_resolved": False,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": "via markdown",
                "modified_at": "2026-01-01T00:00:00",
            },
        ]
        indexer._index_links(links)

        # Verificar estado antes da resolução
        links_table = indexer._ensure_links_table()
        count_before = links_table.count_rows()
        assert count_before == 2, f"Esperado 2 links antes, obtido {count_before}"

        # Resolver links
        resolved = indexer._resolve_link_targets()
        assert resolved == 2, f"Esperado 2 resolvidos, obtido {resolved}"

        # CRÍTICO: Verificar que ambos os links ainda existem após resolução
        count_after = links_table.count_rows()
        assert count_after == 2, (
            f"BUG: Resolução colapsou links! Esperado 2 links após, obtido {count_after}"
        )

        # Verificar que ambos os tipos estão presentes
        rows = links_table.search().limit(10).to_list()
        link_types = set(r["link_type"] for r in rows)
        assert link_types == {"wikilink", "markdown"}, (
            f"BUG: Faltam tipos após resolução. Obtido: {link_types}"
        )

        # Verificar que ambos foram resolvidos
        resolved_flags = [r["is_resolved"] for r in rows]
        assert all(resolved_flags), "Todos os links devem estar resolvidos"
