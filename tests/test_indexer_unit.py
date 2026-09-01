"""
Unit tests for the VaultIndexer class.

Fast tests that do not require ML models or LanceDB.
Tests for parsing, chunking and scanning are in test_parser.py,
test_chunker.py and test_scanner.py respectively.
"""

import threading

from vault_search.core.fts_builder import create_fts_index
from vault_search.core.indexer import VaultIndexer


class TestVaultIndexerClass:
    def test_write_lock_exists(self):
        assert hasattr(VaultIndexer, "_write_lock")
        assert isinstance(VaultIndexer._write_lock, type(threading.Lock()))

    def test_reindex_note_rejects_extension_invalid(self):
        indexer = VaultIndexer()
        result = indexer.reindex_note("file.jpg")
        assert result["status"] == "rejected_extension"

    def test_reindex_note_accepts_extension_uppercase(self):
        """The .MD extension is accepted case-insensitively."""
        indexer = VaultIndexer()
        result = indexer.reindex_note("note.MD")
        assert result["status"] != "rejected_extension"

    def test_reindex_note_accepts_pdf(self):
        """The .pdf extension is accepted."""
        indexer = VaultIndexer()
        result = indexer.reindex_note("doc.pdf")
        assert result["status"] != "rejected_extension"

    def test_reindex_note_accepts_canvas(self):
        """The .canvas extension is accepted."""
        indexer = VaultIndexer()
        result = indexer.reindex_note("diagram.canvas")
        assert result["status"] != "rejected_extension"

    def test_reindex_note_accepts_pdf_uppercase(self):
        """The .PDF extension is accepted case-insensitively."""
        indexer = VaultIndexer()
        result = indexer.reindex_note("doc.PDF")
        assert result["status"] != "rejected_extension"


class TestCreateFtsIndex:
    """Tests for create_fts_index with FTS_LANGUAGE configurable."""

    def test_fts_with_language(self):
        """FTS with language must use stemming."""
        from unittest.mock import MagicMock, patch

        mock_table = MagicMock()

        with patch("vault_search.core.fts_builder.FTS_LANGUAGE", "Portuguese"):
            create_fts_index(mock_table)

        mock_table.create_fts_index.assert_called_once_with(
            "text", language="Portuguese", replace=True
        )

    def test_fts_without_language(self):
        """FTS without a language uses the neutral tokenizer."""
        from unittest.mock import MagicMock, patch

        mock_table = MagicMock()

        with patch("vault_search.core.fts_builder.FTS_LANGUAGE", None):
            create_fts_index(mock_table)

        mock_table.create_fts_index.assert_called_once_with(
            "text",
            language="English",
            stem=False,
            remove_stop_words=False,
            ascii_folding=True,
            replace=True,
        )

    def test_fts_error_not_crashes(self):
        """An FTS creation error logs a warning without crashing."""
        from unittest.mock import MagicMock, patch

        mock_table = MagicMock()
        mock_table.create_fts_index.side_effect = RuntimeError("FTS error")

        with patch("vault_search.core.fts_builder.FTS_LANGUAGE", "Portuguese"):
            # Must not raise exception
            create_fts_index(mock_table)


class TestVectorIndexConfig:
    """Tests for dynamic vector-index configuration."""

    def test_config_none_below_threshold(self):
        """A total below the threshold returns None."""
        from vault_search.config.search import get_vector_index_config

        config = get_vector_index_config(1000)  # Well below the threshold.
        assert config is None

    def test_config_none_at_threshold_minus_one(self):
        """A total immediately below the threshold returns None."""
        from vault_search.config.search import VECTOR_INDEX_MIN_CHUNKS, get_vector_index_config

        config = get_vector_index_config(VECTOR_INDEX_MIN_CHUNKS - 1)
        assert config is None

    def test_config_valid_at_threshold(self):
        """The exact threshold returns a valid configuration."""
        from vault_search.config.search import VECTOR_INDEX_MIN_CHUNKS, get_vector_index_config

        config = get_vector_index_config(VECTOR_INDEX_MIN_CHUNKS)
        assert config is not None
        assert "index_type" in config
        assert "num_partitions" in config
        assert "distance_type" in config

    def test_config_valid_above_threshold(self):
        """A total above the threshold returns a valid configuration."""
        from vault_search.config.search import get_vector_index_config

        config = get_vector_index_config(10000)
        assert config is not None
        assert config["index_type"] in ("IVF_PQ", "IVF_HNSW_SQ")

    def test_partitions_scale_with_size(self):
        """The partition count scales with dataset size."""
        from vault_search.config.search import get_vector_index_config

        config_small = get_vector_index_config(5000)
        config_large = get_vector_index_config(100000)

        assert config_small is not None
        assert config_large is not None
        # Dataset larger must have more partitions
        assert config_large["num_partitions"] >= config_small["num_partitions"]

    def test_partitions_min_is_one(self):
        """Partitions minimums must be 1."""
        from vault_search.config.search import get_vector_index_config

        config = get_vector_index_config(5000)
        assert config is not None
        assert config["num_partitions"] >= 1

    def test_partitions_max_is_256(self):
        """Partitions maximums must be 256."""
        from vault_search.config.search import get_vector_index_config

        # 256 * 8192 = 2M+ chunks
        config = get_vector_index_config(3_000_000)
        assert config is not None
        assert config["num_partitions"] <= 256

    def test_distance_type_is_cosine(self):
        """Distance default must be cosine (for BGE-M3 normalized)."""
        from vault_search.config.search import get_vector_index_config

        config = get_vector_index_config(10000)
        assert config is not None
        assert config["distance_type"] == "cosine"

    def test_num_sub_vectors_divide_embedding_dimension(self):
        """IVF_PQ requires that a dimension be divisible by the number of subvectors."""
        from vault_search.config.embedding import EMBEDDING_DIMENSION
        from vault_search.config.search import get_vector_index_config

        config = get_vector_index_config(10000)

        assert config is not None
        assert EMBEDDING_DIMENSION % config["num_sub_vectors"] == 0

    def test_config_disabled_returns_none(self):
        """With auto-create disabled must return None."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from vault_search.config.search import get_vector_index_config

        runtime_config = SimpleNamespace(
            vector_index=SimpleNamespace(auto_create=False),
        )
        with patch("vault_search.config.search.get_config", return_value=runtime_config):
            config = get_vector_index_config(100000)
            assert config is None

    def test_runtime_config_drives_ann_parameters(self):
        """The YAML runtime object populates the complete ANN configuration."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from vault_search.config.search import get_vector_index_config

        runtime_config = SimpleNamespace(
            vector_index=SimpleNamespace(
                auto_create=True,
                min_chunks=10,
                index_type="IVF_PQ",
                num_sub_vectors=128,
                distance_type="cosine",
            ),
        )
        with patch("vault_search.config.search.get_config", return_value=runtime_config):
            config = get_vector_index_config(10)

        assert config == {
            "index_type": "IVF_PQ",
            "num_partitions": 1,
            "num_sub_vectors": 128,
            "distance_type": "cosine",
        }


class TestVectorIndexMethods:
    """Tests for methods of index vector of the VaultIndexer."""

    def test_has_vector_index_in_table(self):
        """Without table must return False."""
        indexer = VaultIndexer()
        indexer._table = None
        assert indexer._has_vector_index() is False

    def test_has_vector_index_with_mock(self):
        """With table mock without index must return False."""
        from unittest.mock import MagicMock

        indexer = VaultIndexer()
        mock_table = MagicMock()
        mock_table.list_indices.return_value = []
        indexer._table = mock_table

        assert indexer._has_vector_index() is False

    def test_has_vector_index_exists(self):
        """With index vector must return True."""
        from unittest.mock import MagicMock

        indexer = VaultIndexer()
        mock_table = MagicMock()
        mock_table.list_indices.return_value = [{"name": "vector_idx", "columns": ["vector"]}]
        indexer._table = mock_table

        assert indexer._has_vector_index() is True

    def test_maybe_create_in_table(self):
        """Without table must return not created."""
        indexer = VaultIndexer()
        indexer._table = None
        result = indexer._maybe_create_vector_index()
        assert result["created"] is False
        assert "table_not_initialized" in result["reason"]

    def test_maybe_create_below_threshold(self):
        """A total below the threshold does not create an index."""
        from unittest.mock import MagicMock

        indexer = VaultIndexer()
        mock_table = MagicMock()
        mock_table.count_rows.return_value = 100  # Well below
        mock_table.list_indices.return_value = []
        indexer._table = mock_table

        result = indexer._maybe_create_vector_index()
        assert result["created"] is False
        assert "below_threshold" in result["reason"]

    def test_maybe_create_already_exists(self):
        """With index existing must return not created."""
        from unittest.mock import MagicMock

        from vault_search.config.search import VECTOR_INDEX_MIN_CHUNKS

        indexer = VaultIndexer()
        mock_table = MagicMock()
        mock_table.count_rows.return_value = VECTOR_INDEX_MIN_CHUNKS + 1000
        mock_table.list_indices.return_value = [{"name": "vector_idx", "columns": ["vector"]}]
        indexer._table = mock_table

        result = indexer._maybe_create_vector_index()
        assert result["created"] is False
        assert "already_exists" in result["reason"]

    def test_get_vector_index_status_structure(self):
        """Status has the expected structure."""
        from unittest.mock import MagicMock

        indexer = VaultIndexer()
        mock_table = MagicMock()
        mock_table.count_rows.return_value = 1000
        mock_table.list_indices.return_value = []
        indexer._table = mock_table

        status = indexer.get_vector_index_status()

        assert "exists" in status
        assert "threshold" in status
        assert "total_chunks" in status
        assert "would_create" in status
        assert isinstance(status["exists"], bool)
        assert isinstance(status["threshold"], int)

    def test_maybe_create_vector_index_uses_lancedb_029_sync_contract(self):
        """A API synchronous 0.29.2 receives configuration by kwargs legacy."""
        from unittest.mock import MagicMock

        from vault_search.config.search import VECTOR_INDEX_MIN_CHUNKS

        indexer = VaultIndexer()
        table = MagicMock()
        table.count_rows.return_value = VECTOR_INDEX_MIN_CHUNKS
        table.list_indices.return_value = []
        indexer._table = table

        result = indexer._maybe_create_vector_index()

        assert result["created"] is True
        table.create_index.assert_called_once()
        kwargs = table.create_index.call_args.kwargs
        assert kwargs["metric"] == "cosine"
        assert kwargs["vector_column_name"] == "vector"
        assert kwargs["index_type"] == "IVF_PQ"
        assert kwargs["num_sub_vectors"] == 128


class TestReindexIntegrity:
    """Regressions of preservation of the index incremental."""

    def test_missing_staging_tables_use_create_mode(self):
        """Staging missing not uses overwrite, that exposes the path in the Lance stderr."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        db = MagicMock()
        db.list_tables.return_value = SimpleNamespace(tables=[])
        indexer = VaultIndexer()

        indexer._reset_staging_tables(db)

        assert db.create_table.call_count == 3
        assert [call.kwargs["mode"] for call in db.create_table.call_args_list] == [
            "create",
            "create",
            "create",
        ]

    def test_full_reindex_dry_run_reports_only_observed_counts(self, tmp_path):
        """Preview does not publish estimates without measurements."""
        from unittest.mock import patch

        notes = [tmp_path / "one.md", tmp_path / "two.pdf"]
        indexer = VaultIndexer()
        with patch("vault_search.core.indexer.scan_vault", return_value=notes):
            result = indexer.full_reindex(dry_run=True)

        assert result["would_index"] == 2
        assert result["notes_by_extension"] == {".md": 1, ".pdf": 1}
        assert "estimated_chunks" not in result
        assert "estimated_time_seconds" not in result

    def test_parse_error_preserves_previous_chunks(self, tmp_path):
        from unittest.mock import MagicMock, patch

        from vault_search.type_defs import ParseResult, ParseStatus

        note = tmp_path / "note.md"
        note.write_text("content", encoding="utf-8")
        table = MagicMock()
        indexer = VaultIndexer()

        parsed = ParseResult(status=ParseStatus.ERROR, error_type="OSError")
        with (
            patch("vault_search.core.indexer.VAULT_PATH", tmp_path),
            patch("vault_search.core.indexer.parse_file_result", return_value=parsed),
            patch.object(indexer, "_ensure_table", return_value=table),
        ):
            result = indexer.reindex_note("note.md", auto_generate_id=False)

        assert result["status"] == "parse_error"
        table.delete.assert_not_called()
        table.add.assert_not_called()

    def test_add_error_restores_previous_table_version(self, tmp_path):
        from unittest.mock import MagicMock, patch

        from vault_search.type_defs import ParseResult, ParseStatus

        note = tmp_path / "note.md"
        note.write_text("content", encoding="utf-8")
        chunk = {
            "note_path": "note.md",
            "note_title": "note",
            "folder": "",
            "headers": "",
            "tags": "",
            "modified_at": "2026-01-01T00:00:00",
            "text": "content",
        }
        table = MagicMock()
        table.version = 7
        table.add.side_effect = RuntimeError("write failed")
        indexer = VaultIndexer()
        indexer._models = MagicMock()
        indexer._models.embed_corpus.return_value = [[0.1] * 1024]
        parsed = ParseResult(
            status=ParseStatus.SUCCESS,
            chunks=[chunk],
            links=[],
            aliases=[],
        )

        with (
            patch("vault_search.core.indexer.VAULT_PATH", tmp_path),
            patch("vault_search.core.indexer.parse_file_result", return_value=parsed),
            patch.object(indexer, "_ensure_table", return_value=table),
        ):
            result = indexer.reindex_note("note.md", auto_generate_id=False)

        assert result["status"] == "error_add_failed"
        table.restore.assert_called_once_with(7)

    def test_full_reindex_embedding_error_keeps_canonical_table(self, tmp_path):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from vault_search.config.paths import LANCEDB_TABLE
        from vault_search.type_defs import ParseResult, ParseStatus

        note = tmp_path / "note.md"
        note.write_text("content", encoding="utf-8")
        parsed = ParseResult(
            status=ParseStatus.SUCCESS,
            chunks=[
                {
                    "note_path": "note.md",
                    "note_title": "note",
                    "folder": "",
                    "headers": "",
                    "tags": "",
                    "modified_at": "2026-01-01T00:00:00",
                    "text": "content",
                }
            ],
        )
        db = MagicMock()
        db.list_tables.return_value = SimpleNamespace(tables=[LANCEDB_TABLE])
        staging = MagicMock()
        db.create_table.return_value = staging
        indexer = VaultIndexer()
        indexer._models = MagicMock()
        indexer._models.embed_corpus.side_effect = RuntimeError("embedding failed")

        with (
            patch("vault_search.core.indexer.VAULT_PATH", tmp_path),
            patch("vault_search.core.indexer.scan_vault", return_value=[note]),
            patch.object(indexer, "_connect_db", return_value=db),
            patch.object(indexer, "_parse_note", return_value=parsed),
            patch("vault_search.core.indexer.get_optimal_batch_size", return_value=1),
        ):
            result = indexer.full_reindex()

        assert result["status"] == "failed"
        db.drop_table.assert_not_called()
        db.open_table.return_value.add.assert_not_called()

    def test_failed_generation_preserves_previous_lancedb_rows(self, tmp_path):
        from unittest.mock import MagicMock, patch

        vault = tmp_path / "vault"
        data_dir = tmp_path / "data"
        vault.mkdir()
        (vault / "note.md").write_text("# title\n\ncontent stable", encoding="utf-8")

        indexer = VaultIndexer()
        indexer._models = MagicMock()
        indexer._models.embed_corpus.side_effect = lambda texts: [[0.1] * 1024 for _ in texts]

        with (
            patch("vault_search.core.indexer.VAULT_PATH", vault),
            patch("vault_search.core.indexer.DATA_DIR", data_dir),
            patch("vault_search.core.indexer.REINDEX_WORKERS", 1),
        ):
            first = indexer.full_reindex()
            previous_rows = indexer._table.count_rows()
            indexer._models.embed_corpus.side_effect = RuntimeError("embedding failed")
            second = indexer.full_reindex()

        assert first["status"] == "completed"
        assert second["status"] == "failed"
        assert second["previous_index_preserved"] is True
        assert indexer._table.count_rows() == previous_rows
