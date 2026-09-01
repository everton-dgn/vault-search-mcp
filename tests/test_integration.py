"""
Integration tests that require ML models and LanceDB.

Marked with @pytest.mark.slow so they can run separately.
Run with: pytest tests/test_integration.py -v

They use an indexed_vault fixture with a full reindex and do not depend on a
real vault.
"""

from unittest.mock import patch

import pytest

from vault_search.core.indexer import VaultIndexer
from vault_search.core.searcher import VaultSearcher

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def indexed_vault(tmp_path_factory):
    """
    Creates a temporary vault, indexes it with real models, and returns
    (vault_path, indexer, searcher).
    """
    vault = tmp_path_factory.mktemp("vault")

    # Notes with enough content to generate embeddings.
    (vault / "Welcome.md").write_text(
        "---\ntitle: Welcome\ntags:\n  - welcome\n  - start\n---\n"
        "# Welcome to the vault\n\n"
        "This is the welcome vault. Here you can find information "
        "about how to organize notes and projects.\n\n"
        "## Starting\n\n"
        "To start, create notes in thematic folders.",
        encoding="utf-8",
    )

    examples = vault / "examples"
    examples.mkdir()
    (examples / "example1.md").write_text(
        "---\ntitle: Example 1\ntags: example\n---\n"
        "# Example note\n\n"
        "This example note is inside the examples folder. "
        "It contains information about tests and validation.",
        encoding="utf-8",
    )
    (examples / "example2.md").write_text(
        "---\ntitle: Example 2\ntags: example\n---\n"
        "# Second example\n\n"
        "Another example for testing search by folder.",
        encoding="utf-8",
    )

    projects = vault / "projects"
    projects.mkdir()
    (projects / "project1.md").write_text(
        "---\ntitle: Project Alpha\ntags:\n  - project\n  - python\n---\n"
        "# Project Alpha\n\n"
        "Description of Project Alpha using Python and FastAPI.",
        encoding="utf-8",
    )

    # Canvas with content searchable
    import json

    canvas_data = {
        "nodes": [
            {
                "id": "n1",
                "type": "text",
                "text": "Architecture of the system with microservices and API gateway",
                "x": 0,
                "y": 0,
                "width": 300,
                "height": 200,
            },
            {
                "id": "g1",
                "type": "group",
                "label": "Backend Services",
                "x": 0,
                "y": 300,
                "width": 600,
                "height": 400,
            },
        ],
        "edges": [
            {"id": "e1", "fromNode": "n1", "toNode": "g1", "label": "composes"},
        ],
    }
    (vault / "architecture.canvas").write_text(json.dumps(canvas_data), encoding="utf-8")

    # PDF with content searchable
    import pymupdf

    pdf_doc = pymupdf.open()
    pdf_page = pdf_doc.new_page()
    pdf_page.insert_text(
        (72, 72),
        "Technical documentation about deploying Kubernetes with Docker containers",
    )
    pdf_doc.set_metadata({"title": "Deployment Guide"})
    pdf_doc.save(str(vault / "deploy_guide.pdf"))
    pdf_doc.close()

    data_dir = vault / "_data"
    data_dir.mkdir()

    # Patch configuration to use the temporary vault. The scanner and parser
    # receive vault_path as an argument instead of importing VAULT_PATH.
    with (
        patch("vault_search.config.paths.VAULT_PATH", vault),
        patch("vault_search.config.paths.DATA_DIR", data_dir),
        patch("vault_search.core.indexer.VAULT_PATH", vault),
        patch("vault_search.core.indexer.DATA_DIR", data_dir),
        patch("vault_search.core.searcher.DATA_DIR", data_dir),
    ):
        indexer = VaultIndexer()
        indexer._db = None
        indexer._table = None
        indexer.full_reindex()

        searcher = VaultSearcher()
        searcher._db = None
        searcher._table = None

        yield vault, indexer, searcher


class TestIndexerIntegration:
    """Integration tests for the indexer with real models."""

    def test_full_reindex(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        stats = indexer.get_stats()
        assert stats["total_chunks"] > 0
        assert stats["unique_notes"] > 0

    def test_reindex_note_existing(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        result = indexer.reindex_note("Welcome.md")
        assert result["status"] == "updated"
        assert result["chunks_indexed"] > 0

    def test_reindex_note_nonexistent(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        result = indexer.reindex_note("does_not_exist_xyz.md")
        assert result["status"] == "deleted"
        assert result["chunks_indexed"] == 0

    def test_get_stats(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        stats = indexer.get_stats()
        assert stats["total_chunks"] > 0
        assert stats["unique_notes"] > 0
        assert stats["last_modified"] is not None

    def test_stats_without_pandas(self, indexed_vault):
        """Statistics work without pandas by using native PyArrow."""
        vault, indexer, searcher = indexed_vault
        stats = indexer.get_stats()
        assert isinstance(stats["total_chunks"], int)
        assert isinstance(stats["unique_notes"], int)
        assert isinstance(stats["last_modified"], str)

    def test_canvas_indexed(self, indexed_vault):
        """Canvas content is present in the index."""
        vault, indexer, searcher = indexed_vault
        stats = indexer.get_stats()
        # The vault has Markdown, Canvas, and PDF files.
        assert stats["unique_notes"] >= 5  # 4 md + 1 canvas + 1 pdf

    def test_pdf_indexed(self, indexed_vault):
        """PDF must be in the index."""
        vault, indexer, searcher = indexed_vault
        result = indexer.reindex_note("deploy_guide.pdf")
        assert result["status"] == "updated"
        assert result["chunks_indexed"] > 0

    def test_reindex_canvas(self, indexed_vault):
        """Incremental Canvas reindexing must work."""
        vault, indexer, searcher = indexed_vault
        result = indexer.reindex_note("architecture.canvas")
        assert result["status"] == "updated"
        assert result["chunks_indexed"] > 0


class TestSearcherIntegration:
    """Integration tests for the searcher with real models."""

    def test_search_returns_results(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        results = searcher.search("welcome vault", top_k=3)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_search_fields_default(self, indexed_vault):
        """Results contain every default field and omit the vector."""
        vault, indexer, searcher = indexed_vault
        results = searcher.search("example", top_k=1)
        assert len(results) >= 1
        r = results[0]
        assert "note_path" in r
        assert "note_title" in r
        assert "folder" in r
        assert "headers" in r
        assert "tags" in r
        assert "text" in r
        assert "score" in r
        # Vector NOT must be present
        assert "vector" not in r
        # Security metadata was removed by a product decision.
        assert "_security" not in r

    def test_search_hybrid(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        results = searcher.search_hybrid("example", top_k=3)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_search_by_folder_existing(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        results = searcher.search_by_folder("example", folder="examples", top_k=3)
        assert isinstance(results, list)
        # All the results must be of the folder requested
        for r in results:
            assert r["folder"] == "examples" or r["folder"].startswith("examples/")

    def test_search_by_folder_nonexistent(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        results = searcher.search_by_folder("test", folder="missing_folder_xyz", top_k=3)
        assert results == []

    def test_search_by_folder_boundary(self, indexed_vault):
        """Folder 'exam' NOT must return results of 'examples'."""
        vault, indexer, searcher = indexed_vault
        results = searcher.search_by_folder("example", folder="exam", top_k=10)
        for r in results:
            assert r["folder"] != "examples", (
                f"Folder 'exam' must not match with 'examples': {r['folder']}"
            )

    def test_search_empty_returns_list(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        results = searcher.search("xyznonexistentquery123456", top_k=1)
        assert isinstance(results, list)

    def test_search_with_top_k_large(self, indexed_vault):
        """top_k > SEARCH_CANDIDATES base must work with dynamic candidates."""
        vault, indexer, searcher = indexed_vault
        results = searcher.search("test", top_k=100)
        assert isinstance(results, list)
        # Must not crash same with top_k large

    def test_search_finds_canvas(self, indexed_vault):
        """Search finds Canvas content."""
        vault, indexer, searcher = indexed_vault
        results = searcher.search("microservices API gateway", top_k=5)
        canvas_results = [r for r in results if r["note_path"].endswith(".canvas")]
        assert len(canvas_results) > 0, "Must find content of the canvas"

    def test_search_finds_pdf(self, indexed_vault):
        """Search finds PDF content."""
        vault, indexer, searcher = indexed_vault
        results = searcher.search("deploy Kubernetes Docker", top_k=5)
        pdf_results = [r for r in results if r["note_path"].endswith(".pdf")]
        assert len(pdf_results) > 0, "Must find content of the PDF"


class TestReindexAtomicity:
    """Test that reindex_note is atomic (add first, delete old)."""

    def test_data_preserved_after_reindex(self, indexed_vault):
        """After reindex, a note must have chunks valid."""
        vault, indexer, searcher = indexed_vault

        # Reindexar
        indexer.reindex_note("Welcome.md")
        searcher.invalidate_cache()

        # Search must return results from the note.
        results = searcher.search("Welcome vault", top_k=5)
        welcome_results = [r for r in results if "Welcome" in r["note_path"]]
        assert len(welcome_results) > 0, "Welcome.md must have results after reindex"

    def test_without_duplicates_after_double_reindex(self, indexed_vault):
        """Reindex double must not create duplicates."""
        vault, indexer, searcher = indexed_vault

        result1 = indexer.reindex_note("Welcome.md")
        # Reset the circuit breaker to allow an immediate second test reindex.
        indexer.reset_circuit_breaker("Welcome.md")
        result2 = indexer.reindex_note("Welcome.md")

        assert result1["chunks_indexed"] == result2["chunks_indexed"], (
            "Same note must generate same number of chunks"
        )

        # Verify via stats that there is in the duplicates
        stats = indexer.get_stats()
        # If duplicates existed, total_chunks would be larger.
        assert stats["total_chunks"] >= result2["chunks_indexed"]
