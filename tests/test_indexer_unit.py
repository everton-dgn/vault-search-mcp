"""
Testes unitários para indexer.py — classe VaultIndexer.

Testes rápidos que NÃO precisam de modelos ML nem LanceDB.
Testes de parsing, chunking e scanning estão em test_parser.py,
test_chunker.py e test_scanner.py respectivamente.
"""

import threading

from vault_search.core.fts_builder import create_fts_index
from vault_search.core.indexer import VaultIndexer


class TestVaultIndexerClass:
    def test_write_lock_existe(self):
        assert hasattr(VaultIndexer, "_write_lock")
        assert isinstance(VaultIndexer._write_lock, type(threading.Lock()))

    def test_reindex_note_rejeita_extensao_invalida(self):
        indexer = VaultIndexer()
        result = indexer.reindex_note("arquivo.jpg")
        assert result["status"] == "rejected_extension"

    def test_reindex_note_aceita_extensao_maiuscula(self):
        """Extensão .MD deve ser aceita (case-insensitive)."""
        indexer = VaultIndexer()
        result = indexer.reindex_note("nota.MD")
        assert result["status"] != "rejected_extension"

    def test_reindex_note_aceita_pdf(self):
        """Extensão .pdf deve ser aceita."""
        indexer = VaultIndexer()
        result = indexer.reindex_note("doc.pdf")
        assert result["status"] != "rejected_extension"

    def test_reindex_note_aceita_canvas(self):
        """Extensão .canvas deve ser aceita."""
        indexer = VaultIndexer()
        result = indexer.reindex_note("diagram.canvas")
        assert result["status"] != "rejected_extension"

    def test_reindex_note_aceita_pdf_maiuscula(self):
        """Extensão .PDF deve ser aceita (case-insensitive)."""
        indexer = VaultIndexer()
        result = indexer.reindex_note("doc.PDF")
        assert result["status"] != "rejected_extension"


class TestCreateFtsIndex:
    """Testes para create_fts_index com FTS_LANGUAGE configurável."""

    def test_fts_com_language(self):
        """FTS com language deve usar stemming."""
        from unittest.mock import MagicMock, patch

        mock_table = MagicMock()

        with patch("vault_search.core.fts_builder.FTS_LANGUAGE", "Portuguese"):
            create_fts_index(mock_table)

        mock_table.create_fts_index.assert_called_once_with(
            "text", language="Portuguese", replace=True
        )

    def test_fts_sem_language(self):
        """FTS sem language (None) deve usar tokenizador neutro."""
        from unittest.mock import MagicMock, patch

        mock_table = MagicMock()

        with patch("vault_search.core.fts_builder.FTS_LANGUAGE", None):
            create_fts_index(mock_table)

        mock_table.create_fts_index.assert_called_once_with("text", replace=True)

    def test_fts_erro_nao_crasheia(self):
        """Erro ao criar FTS deve logar warning, não crashear."""
        from unittest.mock import MagicMock, patch

        mock_table = MagicMock()
        mock_table.create_fts_index.side_effect = RuntimeError("FTS error")

        with patch("vault_search.core.fts_builder.FTS_LANGUAGE", "Portuguese"):
            # Não deve levantar exceção
            create_fts_index(mock_table)


class TestVectorIndexConfig:
    """Testes para configuração dinâmica de índices vetoriais."""

    def test_config_none_below_threshold(self):
        """Abaixo do threshold deve retornar None."""
        from vault_search.config.search import get_vector_index_config

        config = get_vector_index_config(1000)  # Bem abaixo do threshold
        assert config is None

    def test_config_none_at_threshold_minus_one(self):
        """Logo abaixo do threshold deve retornar None."""
        from vault_search.config.search import VECTOR_INDEX_MIN_CHUNKS, get_vector_index_config

        config = get_vector_index_config(VECTOR_INDEX_MIN_CHUNKS - 1)
        assert config is None

    def test_config_valid_at_threshold(self):
        """No threshold exato deve retornar config válida."""
        from vault_search.config.search import VECTOR_INDEX_MIN_CHUNKS, get_vector_index_config

        config = get_vector_index_config(VECTOR_INDEX_MIN_CHUNKS)
        assert config is not None
        assert "index_type" in config
        assert "num_partitions" in config
        assert "distance_type" in config

    def test_config_valid_above_threshold(self):
        """Acima do threshold deve retornar config válida."""
        from vault_search.config.search import get_vector_index_config

        config = get_vector_index_config(10000)
        assert config is not None
        assert config["index_type"] in ("IVF_PQ", "IVF_HNSW_SQ")

    def test_partitions_scale_with_size(self):
        """Partições devem escalar com tamanho do dataset."""
        from vault_search.config.search import get_vector_index_config

        config_small = get_vector_index_config(5000)
        config_large = get_vector_index_config(100000)

        assert config_small is not None
        assert config_large is not None
        # Dataset maior deve ter mais partições
        assert config_large["num_partitions"] >= config_small["num_partitions"]

    def test_partitions_min_is_one(self):
        """Partições mínimas devem ser 1."""
        from vault_search.config.search import get_vector_index_config

        config = get_vector_index_config(5000)
        assert config is not None
        assert config["num_partitions"] >= 1

    def test_partitions_max_is_256(self):
        """Partições máximas devem ser 256."""
        from vault_search.config.search import get_vector_index_config

        # 256 * 8192 = 2M+ chunks
        config = get_vector_index_config(3_000_000)
        assert config is not None
        assert config["num_partitions"] <= 256

    def test_distance_type_is_cosine(self):
        """Distância padrão deve ser cosine (para BGE-M3 normalizado)."""
        from vault_search.config.search import get_vector_index_config

        config = get_vector_index_config(10000)
        assert config is not None
        assert config["distance_type"] == "cosine"

    def test_num_sub_vectors_divide_embedding_dimension(self):
        """IVF_PQ exige que a dimensão seja divisível pelo número de subvectors."""
        from vault_search.config.embedding import EMBEDDING_DIMENSION
        from vault_search.config.search import get_vector_index_config

        config = get_vector_index_config(10000)

        assert config is not None
        assert EMBEDDING_DIMENSION % config["num_sub_vectors"] == 0

    def test_config_disabled_returns_none(self):
        """Com auto-create desabilitado deve retornar None."""
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
        """O objeto carregado do YAML deve alimentar toda a configuração ANN."""
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
    """Testes para métodos de índice vetorial do VaultIndexer."""

    def test_has_vector_index_no_table(self):
        """Sem tabela deve retornar False."""
        indexer = VaultIndexer()
        indexer._table = None
        assert indexer._has_vector_index() is False

    def test_has_vector_index_with_mock(self):
        """Com tabela mock sem índice deve retornar False."""
        from unittest.mock import MagicMock

        indexer = VaultIndexer()
        mock_table = MagicMock()
        mock_table.list_indices.return_value = []
        indexer._table = mock_table

        assert indexer._has_vector_index() is False

    def test_has_vector_index_exists(self):
        """Com índice vetorial deve retornar True."""
        from unittest.mock import MagicMock

        indexer = VaultIndexer()
        mock_table = MagicMock()
        mock_table.list_indices.return_value = [{"name": "vector_idx", "columns": ["vector"]}]
        indexer._table = mock_table

        assert indexer._has_vector_index() is True

    def test_maybe_create_no_table(self):
        """Sem tabela deve retornar não criado."""
        indexer = VaultIndexer()
        indexer._table = None
        result = indexer._maybe_create_vector_index()
        assert result["created"] is False
        assert "table_not_initialized" in result["reason"]

    def test_maybe_create_below_threshold(self):
        """Abaixo do threshold deve retornar não criado."""
        from unittest.mock import MagicMock

        indexer = VaultIndexer()
        mock_table = MagicMock()
        mock_table.count_rows.return_value = 100  # Bem abaixo
        mock_table.list_indices.return_value = []
        indexer._table = mock_table

        result = indexer._maybe_create_vector_index()
        assert result["created"] is False
        assert "below_threshold" in result["reason"]

    def test_maybe_create_already_exists(self):
        """Com índice existente deve retornar não criado."""
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
        """Status deve ter estrutura esperada."""
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
        """A API síncrona 0.29.2 recebe configuração por kwargs legados."""
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
    """Regressões de preservação do índice incremental."""

    def test_missing_staging_tables_use_create_mode(self):
        """Staging ausente não usa overwrite, que expõe o path no Lance stderr."""
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
        """Preview não publica estimativas sem medição."""
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
        note.write_text("conteúdo", encoding="utf-8")
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
        note.write_text("conteúdo", encoding="utf-8")
        chunk = {
            "note_path": "note.md",
            "note_title": "note",
            "folder": "",
            "headers": "",
            "tags": "",
            "modified_at": "2026-01-01T00:00:00",
            "text": "conteúdo",
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
        note.write_text("conteúdo", encoding="utf-8")
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
                    "text": "conteúdo",
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
        (vault / "note.md").write_text("# título\n\nconteúdo estável", encoding="utf-8")

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
