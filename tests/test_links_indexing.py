"""
Integration tests for the indexed-link system.

Test a extraction, indexing and resolution of links during the reindex.
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
    """Tests for extraction of aliases of the frontmatter."""

    def test_aliases_list(self):
        fm = {"aliases": ["API Docs", "Documentation"]}
        aliases = extract_aliases(fm)
        assert aliases == ["API Docs", "Documentation"]

    def test_aliases_string_csv(self):
        fm = {"aliases": "API Docs, Documentation"}
        aliases = extract_aliases(fm)
        assert aliases == ["API Docs", "Documentation"]

    def test_alias_singular(self):
        fm = {"alias": "API Docs"}
        aliases = extract_aliases(fm)
        assert aliases == ["API Docs"]

    def test_alias_singular_list(self):
        fm = {"alias": ["A", "B"]}
        aliases = extract_aliases(fm)
        assert aliases == ["A", "B"]

    def test_aliases_and_alias_are_combined(self):
        fm = {"aliases": ["A", "B"], "alias": "C"}
        aliases = extract_aliases(fm)
        assert "A" in aliases
        assert "B" in aliases
        assert "C" in aliases

    def test_aliases_empty(self):
        fm = {}
        aliases = extract_aliases(fm)
        assert aliases == []

    def test_aliases_none(self):
        fm = {"aliases": None}
        aliases = extract_aliases(fm)
        assert aliases == []


class TestParseNoteWithLinks:
    """Tests for parse_note with extraction of links."""

    def test_note_with_wikilinks(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "source.md"
        note.write_text(
            """---
title: Source
---
# Source

Link for [[Target]] and [[Other|alias]].
""",
            encoding="utf-8",
        )

        chunks, links, aliases = parse_note(note, vault)

        assert len(chunks) > 0
        assert len(links) >= 2

        # Verify structure of the links
        link_targets = [link["link_target"] for link in links]
        assert "Target" in link_targets
        assert "Other" in link_targets

    def test_note_with_markdown_links(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "source.md"
        note.write_text(
            """---
title: Source
---
# Source

See [documentation](docs/manual.md).
""",
            encoding="utf-8",
        )

        chunks, links, aliases = parse_note(note, vault)

        assert len(links) >= 1
        md_links = [link for link in links if link["link_type"] == "markdown"]
        assert len(md_links) >= 1
        assert "docs/manual.md" in [link["link_target"] for link in md_links]

    def test_note_with_embeds(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "source.md"
        note.write_text(
            """---
title: Source
---
# Source

Image: ![[image.png]]
""",
            encoding="utf-8",
        )

        chunks, links, aliases = parse_note(note, vault)

        assert len(links) >= 1
        embeds = [link for link in links if link["link_type"] == "embed"]
        assert len(embeds) >= 1
        assert "image.png" in [link["link_target"] for link in embeds]

    def test_note_with_aliases(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "api.md"
        note.write_text(
            """---
title: API Documentation
aliases: [API Docs, Documentation of the API]
---
# API

Documentation of the API.
""",
            encoding="utf-8",
        )

        chunks, links, aliases = parse_note(note, vault)

        assert "API Docs" in aliases
        assert "Documentation of the API" in aliases

    def test_link_fields_are_complete(self, tmp_path):
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

        # Fields required
        assert "from_note_path" in link
        assert "from_note_title" in link
        assert "link_type" in link
        assert "link_target" in link
        assert "link_target_normalized" in link
        assert "context" in link
        assert "modified_at" in link

        # Values
        assert link["from_note_path"] == "source.md"
        assert link["from_note_title"] == "Source Note"
        assert link["link_type"] == "wikilink"
        assert link["link_target"] == "Target"
        assert link["heading"] == "Section"
        assert link["alias"] == "alias"


class TestNormalizeLinkTarget:
    """Additional tests for normalize_link_target."""

    @pytest.mark.parametrize(
        "input,expected",
        [
            ("My Project", "my-project"),
            ("docs/API.md", "docs/api"),
            ("  note  ", "note"),
            ("UPPER CASE", "upper-case"),
            ("already-normalized", "already-normalized"),
            ("folder/sub/note.md", "folder/sub/note"),
            ("image.png", "image"),
            ("video.mp4", "video.mp4"),  # Keep non-indexable extensions.
        ],
    )
    def test_normalization(self, input, expected):
        assert normalize_link_target(input) == expected


class TestParseWikilinkParts:
    """Tests for parsing complete wikilinks."""

    @pytest.mark.parametrize(
        "input,expected",
        [
            ("Note", {"target": "Note", "alias": None, "heading": None, "block_ref": None}),
            (
                "Note|alias",
                {"target": "Note", "alias": "alias", "heading": None, "block_ref": None},
            ),
            (
                "Note#Section",
                {"target": "Note", "alias": None, "heading": "Section", "block_ref": None},
            ),
            (
                "Note^block",
                {"target": "Note", "alias": None, "heading": None, "block_ref": "block"},
            ),
            (
                "Note#Section|alias",
                {"target": "Note", "alias": "alias", "heading": "Section", "block_ref": None},
            ),
        ],
    )
    def test_parsing(self, input, expected):
        result = parse_wikilink_parts(input)
        assert result == expected


class TestExtractLinkContext:
    """Tests for extraction of context of links."""

    def test_basic_context(self):
        content = "This is a text with [[link]] in the middle."
        context = extract_link_context(content, "[[link]]")
        assert "[[link]]" in context
        assert "text with" in context

    def test_truncated_context(self):
        content = "A" * 100 + "[[link]]" + "B" * 100
        context = extract_link_context(content, "[[link]]", window=20)
        assert "[[link]]" in context
        assert "..." in context

    def test_link_not_found(self):
        content = "Text without link."
        context = extract_link_context(content, "[[nonexistent]]")
        assert context == ""


class TestExtractAllLinksStructure:
    """Tests for the return structure of extract_all_links."""

    def test_wikilinks_structure(self):
        text = "Link [[Note#H1|alias]] here."
        result = extract_all_links(text)

        assert len(result["wikilinks"]) == 1
        wl = result["wikilinks"][0]
        assert wl["target"] == "Note"
        assert wl["alias"] == "alias"
        assert wl["heading"] == "H1"
        assert "[[Note#H1|alias]]" in wl["raw"]

    def test_markdown_links_structure(self):
        text = "See [docs](path/to/file.md) here."
        result = extract_all_links(text)

        assert len(result["markdown_links"]) == 1
        ml = result["markdown_links"][0]
        assert ml["target"] == "path/to/file.md"
        assert ml["text"] == "docs"

    def test_embeds_structure(self):
        text = "Image ![[image.png]] here."
        result = extract_all_links(text)

        assert len(result["embeds"]) == 1
        emb = result["embeds"][0]
        assert emb["target"] == "image.png"

    def test_external_urls_when_enabled(self):
        text = "Link https://example.com here."
        result = extract_all_links(text, include_external=True)

        assert "external" in result
        assert len(result["external"]) == 1
        assert result["external"][0]["url"] == "https://example.com"

    def test_external_urls_ignored_by_default(self):
        text = "Link https://example.com here."
        result = extract_all_links(text, include_external=False)

        assert "external" not in result or len(result.get("external", [])) == 0


class TestIndexerLinksIntegration:
    """Tests for integration of the indexer with links."""

    def test_full_reindex_indexes_links(self, tmp_path, monkeypatch):
        """full_reindex must extract and index links."""
        vault = tmp_path / "vault"
        vault.mkdir()

        # Create notes with links
        (vault / "source.md").write_text(
            """---
title: Source
---
# Source

Link for [[target]] and [[other]].
""",
            encoding="utf-8",
        )

        (vault / "target.md").write_text(
            """---
title: Target
---
# Target

Content.
""",
            encoding="utf-8",
        )

        # Monkeypatch in the module of the indexer (where VAULT_PATH was imported)
        import vault_search.core.indexer as idx

        monkeypatch.setattr(idx, "VAULT_PATH", vault)
        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")

        # Index
        indexer = VaultIndexer()
        stats = indexer.full_reindex()

        assert stats["total_notes"] >= 2
        assert stats.get("total_links", 0) >= 2

    def test_full_reindex_indexes_aliases(self, tmp_path, monkeypatch):
        """full_reindex must extract and index aliases."""
        vault = tmp_path / "vault"
        vault.mkdir()

        (vault / "api.md").write_text(
            """---
title: API Documentation
aliases: [API Docs, Documentation]
---
# API

Content.
""",
            encoding="utf-8",
        )

        # Monkeypatch in the module of the indexer (where VAULT_PATH was imported)
        import vault_search.core.indexer as idx

        monkeypatch.setattr(idx, "VAULT_PATH", vault)
        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")

        indexer = VaultIndexer()
        stats = indexer.full_reindex()

        assert stats.get("total_aliases", 0) >= 2

    def test_resolve_does_not_collapse_distinct_links(self, tmp_path, monkeypatch):
        """
        Regression test: link resolution must not collapse distinct links.

        Bug fixed: when two links (ex: wikilink and markdown) pointed for
        the same normalized target, resolution deleted both and added only one,
        losing information.

        The unique key is now (from_note_path, link_type, link_target).
        """
        # Configure temporary paths.
        import vault_search.core.indexer as idx
        from vault_search.config.embedding import EMBEDDING_DIMENSION

        monkeypatch.setattr(idx, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(idx, "LANCEDB_TABLE", "chunks_test")
        monkeypatch.setattr(idx, "LINKS_TABLE", "links_test")
        monkeypatch.setattr(idx, "ALIASES_TABLE", "aliases_test")

        indexer = VaultIndexer()

        # Create table of chunks with note target
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

        # Index two distinct links for the same normalized target.
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

        # Verify state before of the resolution
        links_table = indexer._ensure_links_table()
        count_before = links_table.count_rows()
        assert count_before == 2, f"Expected 2 links before, obtained {count_before}"

        # Resolve links
        resolved = indexer._resolve_link_targets()
        assert resolved == 2, f"Expected 2 resolved, obtained {resolved}"

        # CRITICAL: Verify that both the links still exist after resolution
        count_after = links_table.count_rows()
        assert count_after == 2, (
            f"BUG: Resolution collapsed links! Expected 2 links after, obtained {count_after}"
        )

        # Verify that both the types are present
        rows = links_table.search().limit(10).to_list()
        link_types = set(r["link_type"] for r in rows)
        assert link_types == {"wikilink", "markdown"}, (
            f"BUG: Faltam types after resolution. Obtained: {link_types}"
        )

        # Verify that both were resolved
        resolved_flags = [r["is_resolved"] for r in rows]
        assert all(resolved_flags), "All the links must be resolved"
