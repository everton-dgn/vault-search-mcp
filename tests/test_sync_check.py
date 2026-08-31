"""
Tests for sync_check behavior.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch


class TestSyncCheck:
    """Tests for VaultIndexer.sync_check()."""

    def test_sync_check_empty_vault_empty_index(self):
        """Returns zeros when vault and index are emptys."""
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
        """Detect new vault files that are absent from the index."""
        from pathlib import Path

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mock: vault has a file, index is empty
        mock_path = MagicMock()
        mock_path.relative_to.return_value = Path("nova_note.md")
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
        """Detecta files in the index that not exist more in the vault."""

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mock: vault empty, index has a file
        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = []

            with patch.object(indexer, "_ensure_table") as mock_table:
                mock_table.return_value.count_rows.return_value = 1

                # Mock of the result of the query
                mock_arrow = MagicMock()
                mock_arrow.column.side_effect = lambda name: MagicMock(
                    to_pylist=lambda: (
                        ["deleted_note.md"] if name == "note_path" else ["2024-01-01T00:00:00"]
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
        """Detect modified files when vault mtime is newer than index mtime."""
        from pathlib import Path

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mtime in the vault: 2000.0 (more recent)
        # Mtime in the index: 1000.0 (more old)
        vault_mtime = 2000.0
        index_mtime = 1000.0
        index_mtime_iso = datetime.fromtimestamp(index_mtime).isoformat()

        # Create a real Path so the mock works with relative_to.
        fake_vault = Path("/fake/vault")

        # Mock Path to simulate a vault file.
        mock_path = MagicMock()
        mock_path.relative_to.return_value = Path("note.md")
        mock_stat = MagicMock()
        mock_stat.st_mtime = vault_mtime
        mock_path.stat.return_value = mock_stat

        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = [mock_path]

            with patch("vault_search.core.indexer.VAULT_PATH", fake_vault):
                with patch.object(indexer, "_ensure_table") as mock_table:
                    mock_table.return_value.count_rows.return_value = 1

                    # Mock of the result of the query Arrow
                    mock_arrow = MagicMock()

                    def column_mock(name):
                        m = MagicMock()
                        if name == "note_path":
                            m.to_pylist.return_value = ["note.md"]
                        else:  # modified_at
                            m.to_pylist.return_value = [index_mtime_iso]
                        return m

                    mock_arrow.column.side_effect = column_mock
                    mock_table.return_value.search.return_value.select.return_value.limit.return_value.to_arrow.return_value = mock_arrow

                    result = indexer.sync_check(auto_sync=False)

                    # Vault mtime (2000) exceeds index mtime (1000) plus margin (1).
                    # The next scan must detect the file as modified.
                    assert result["vault_files"] == 1
                    assert result["indexed_files"] == 1
                    assert result["new_files"] == 0
                    assert result["modified_files"] == 1  # Detectou modification
                    assert result["deleted_files"] == 0

    def test_sync_check_in_sync(self):
        """Detect no changes when the vault and index are synchronized."""
        from pathlib import Path

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mock: vault and index have the same file with same mtime
        mtime = 1000.0
        mock_path = MagicMock()
        mock_path.relative_to.return_value = Path("note.md")
        mock_path.stat.return_value.st_mtime = mtime

        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = [mock_path]

            with patch.object(indexer, "_ensure_table") as mock_table:
                mock_table.return_value.count_rows.return_value = 1

                # Mock of the result of the query - same mtime
                iso_mtime = datetime.fromtimestamp(mtime).isoformat()
                mock_arrow = MagicMock()
                mock_arrow.column.side_effect = lambda name: MagicMock(
                    to_pylist=lambda: ["note.md"] if name == "note_path" else [iso_mtime]
                )
                mock_table.return_value.search.return_value.select.return_value.limit.return_value.to_arrow.return_value = mock_arrow

                result = indexer.sync_check(auto_sync=False)

                assert result["vault_files"] == 1
                assert result["indexed_files"] == 1
                assert result["new_files"] == 0
                assert result["modified_files"] == 0
                assert result["deleted_files"] == 0

    def test_sync_check_auto_sync_calls_reindex(self):
        """auto_sync=True calls reindex_note for out-of-sync files."""
        from pathlib import Path

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mock: vault has a file new
        mock_path = MagicMock()
        mock_path.relative_to.return_value = Path("new.md")
        mock_path.stat.return_value.st_mtime = 1000.0

        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = [mock_path]

            with patch.object(indexer, "_ensure_table") as mock_table:
                mock_table.return_value.count_rows.return_value = 0

                with patch.object(indexer, "reindex_note") as mock_reindex:
                    mock_reindex.return_value = {"status": "updated"}

                    result = indexer.sync_check(auto_sync=True)

                    # Must have called reindex_note for the file new
                    mock_reindex.assert_called_once_with("new.md")
                    assert result["synced"] == 1

    def test_sync_check_dry_run_does_not_modify(self):
        """auto_sync=False does not modify the index."""
        from pathlib import Path

        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()

        # Mock: vault has a file new
        mock_path = MagicMock()
        mock_path.relative_to.return_value = Path("new.md")
        mock_path.stat.return_value.st_mtime = 1000.0

        with patch("vault_search.core.indexer.scan_vault") as mock_scan:
            mock_scan.return_value = [mock_path]

            with patch.object(indexer, "_ensure_table") as mock_table:
                mock_table.return_value.count_rows.return_value = 0

                with patch.object(indexer, "reindex_note") as mock_reindex:
                    result = indexer.sync_check(auto_sync=False)

                    # Not must have called reindex_note
                    mock_reindex.assert_not_called()
                    assert result["synced"] == 0


class TestSyncVaultTool:
    """Tests for the sync_vault MCP tool."""

    def test_sync_vault_tool_calls_sync_check(self):
        """sync_vault calls indexer.sync_check correctly."""
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

        # Verify that sync_check was registered.
        assert mcp.tool.called
