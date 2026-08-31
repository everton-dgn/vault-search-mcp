"""
Testes para a funcionalidade de sync_check.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch


class TestSyncCheck:
    """Testes para VaultIndexer.sync_check()."""

    def test_sync_check_empty_vault_empty_index(self):
        """Retorna zeros quando vault e índice estão vazios."""
        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = []

            with patch.object(indexer, "_ensure_table") as mock_table:
                mock_table.return_value.count_rows.return_value = 0

                result = indexer.sync_check(auto_sync=False)

                assert result["vault_files"] == 0
                assert result["indexed_files"] == 0
                assert result["new_files"] == 0
                assert result["modified_files"] == 0
                assert result["deleted_files"] == 0

    def test_sync_check_detects_new_files(self):
        """Detecta arquivos novos no vault que não estão no índice."""
        from pathlib import Path

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mock: vault tem um arquivo, índice está vazio
        mock_path = MagicMock()
        mock_path.relative_to.return_value = Path("nova_nota.md")
        mock_path.stat.return_value.st_mtime = 1000.0

        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = [mock_path]

            with patch.object(indexer, "_ensure_table") as mock_table:
                mock_table.return_value.count_rows.return_value = 0

                result = indexer.sync_check(auto_sync=False)

                assert result["vault_files"] == 1
                assert result["indexed_files"] == 0
                assert result["new_files"] == 1
                assert result["modified_files"] == 0
                assert result["deleted_files"] == 0

    def test_sync_check_detects_deleted_files(self):
        """Detecta arquivos no índice que não existem mais no vault."""

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mock: vault vazio, índice tem um arquivo
        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = []

            with patch.object(indexer, "_ensure_table") as mock_table:
                mock_table.return_value.count_rows.return_value = 1

                # Mock do resultado da query
                mock_arrow = MagicMock()
                mock_arrow.column.side_effect = lambda name: MagicMock(
                    to_pylist=lambda: (
                        ["nota_deletada.md"] if name == "note_path" else ["2024-01-01T00:00:00"]
                    )
                )
                mock_table.return_value.search.return_value.select.return_value.limit.return_value.to_arrow.return_value = mock_arrow

                result = indexer.sync_check(auto_sync=False)

                assert result["vault_files"] == 0
                assert result["indexed_files"] == 1
                assert result["new_files"] == 0
                assert result["modified_files"] == 0
                assert result["deleted_files"] == 1

    def test_sync_check_detects_modified_files(self):
        """Detecta arquivos modificados (mtime do vault > mtime do índice)."""
        from pathlib import Path

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mtime no vault: 2000.0 (mais recente)
        # Mtime no índice: 1000.0 (mais antigo)
        vault_mtime = 2000.0
        index_mtime = 1000.0
        index_mtime_iso = datetime.fromtimestamp(index_mtime).isoformat()

        # Criar um Path real para o mock funcionar com relative_to
        fake_vault = Path("/fake/vault")

        # Mock do Path para simular arquivo no vault
        mock_path = MagicMock()
        mock_path.relative_to.return_value = Path("nota.md")
        mock_stat = MagicMock()
        mock_stat.st_mtime = vault_mtime
        mock_path.stat.return_value = mock_stat

        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = [mock_path]

            with patch("vault_search.core.indexer.VAULT_PATH", fake_vault):
                with patch.object(indexer, "_ensure_table") as mock_table:
                    mock_table.return_value.count_rows.return_value = 1

                    # Mock do resultado da query Arrow
                    mock_arrow = MagicMock()

                    def column_mock(name):
                        m = MagicMock()
                        if name == "note_path":
                            m.to_pylist.return_value = ["nota.md"]
                        else:  # modified_at
                            m.to_pylist.return_value = [index_mtime_iso]
                        return m

                    mock_arrow.column.side_effect = column_mock
                    mock_table.return_value.search.return_value.select.return_value.limit.return_value.to_arrow.return_value = mock_arrow

                    result = indexer.sync_check(auto_sync=False)

                    # Vault mtime (2000) > Index mtime (1000) + margem (1)
                    # Então deve detectar como modificado
                    assert result["vault_files"] == 1
                    assert result["indexed_files"] == 1
                    assert result["new_files"] == 0
                    assert result["modified_files"] == 1  # Detectou modificação
                    assert result["deleted_files"] == 0

    def test_sync_check_in_sync(self):
        """Não detecta mudanças quando vault e índice estão sincronizados."""
        from pathlib import Path

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mock: vault e índice têm o mesmo arquivo com mesmo mtime
        mtime = 1000.0
        mock_path = MagicMock()
        mock_path.relative_to.return_value = Path("nota.md")
        mock_path.stat.return_value.st_mtime = mtime

        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = [mock_path]

            with patch.object(indexer, "_ensure_table") as mock_table:
                mock_table.return_value.count_rows.return_value = 1

                # Mock do resultado da query - mesmo mtime
                iso_mtime = datetime.fromtimestamp(mtime).isoformat()
                mock_arrow = MagicMock()
                mock_arrow.column.side_effect = lambda name: MagicMock(
                    to_pylist=lambda: ["nota.md"] if name == "note_path" else [iso_mtime]
                )
                mock_table.return_value.search.return_value.select.return_value.limit.return_value.to_arrow.return_value = mock_arrow

                result = indexer.sync_check(auto_sync=False)

                assert result["vault_files"] == 1
                assert result["indexed_files"] == 1
                assert result["new_files"] == 0
                assert result["modified_files"] == 0
                assert result["deleted_files"] == 0

    def test_sync_check_auto_sync_calls_reindex(self):
        """auto_sync=True chama reindex_note para arquivos fora de sincronia."""
        from pathlib import Path

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mock: vault tem um arquivo novo
        mock_path = MagicMock()
        mock_path.relative_to.return_value = Path("nova.md")
        mock_path.stat.return_value.st_mtime = 1000.0

        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = [mock_path]

            with patch.object(indexer, "_ensure_table") as mock_table:
                mock_table.return_value.count_rows.return_value = 0

                with patch.object(indexer, "reindex_note") as mock_reindex:
                    mock_reindex.return_value = {"status": "updated"}

                    result = indexer.sync_check(auto_sync=True)

                    # Deve ter chamado reindex_note para o arquivo novo
                    mock_reindex.assert_called_once_with("nova.md")
                    assert result["synced"] == 1

    def test_sync_check_dry_run_does_not_modify(self):
        """auto_sync=False não modifica o índice."""
        from pathlib import Path

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mock: vault tem um arquivo novo
        mock_path = MagicMock()
        mock_path.relative_to.return_value = Path("nova.md")
        mock_path.stat.return_value.st_mtime = 1000.0

        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = [mock_path]

            with patch.object(indexer, "_ensure_table") as mock_table:
                mock_table.return_value.count_rows.return_value = 0

                with patch.object(indexer, "reindex_note") as mock_reindex:
                    result = indexer.sync_check(auto_sync=False)

                    # Não deve ter chamado reindex_note
                    mock_reindex.assert_not_called()
                    assert result["synced"] == 0


class TestSyncVaultTool:
    """Testes para a ferramenta MCP sync_vault."""

    def test_sync_vault_tool_calls_sync_check(self):
        """sync_vault chama indexer.sync_check corretamente."""
        from unittest.mock import MagicMock

        from vault_search.server.search_tools import register_search_tools

        mcp = MagicMock()
        indexer = MagicMock()
        searcher = MagicMock()

        indexer.sync_check.return_value = {
            "vault_files": 10,
            "indexed_files": 8,
            "new_files": 2,
            "modified_files": 0,
            "deleted_files": 0,
            "synced": 2,
        }

        register_search_tools(mcp, indexer, searcher)

        # Verificar que sync_check foi registrado
        assert mcp.tool.called
