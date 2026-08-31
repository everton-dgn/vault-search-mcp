"""
Testes para o catálogo SQLite de notas.
"""

import time
from pathlib import Path
from unittest.mock import MagicMock

from vault_search.crud.catalog import NotesCatalog


class TestNotesCatalog:
    """Testes para NotesCatalog."""

    def test_create_schema(self, tmp_path: Path):
        """Deve criar tabela e índices."""
        db_path = tmp_path / "catalog-schema.db"
        catalog = NotesCatalog(db_path=db_path)
        catalog._create_schema()

        # Verificar que tabela existe
        with catalog._connection() as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = [table["name"] for table in tables]

        assert "notes_catalog" in table_names

    def test_upsert_and_list(self, tmp_path: Path):
        """Deve inserir e listar notas."""
        catalog = NotesCatalog(db_path=tmp_path / "catalog-upsert.db")
        catalog._create_schema()

        # Inserir diretamente no banco
        with catalog._connection() as conn:
            conn.execute(
                """
                INSERT INTO notes_catalog
                (path, folder, extension, title, mtime_ns, size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("pasta/nota.md", "pasta", ".md", "nota", 1000000000, 100),
            )

        notes, total = catalog.list_notes()

        assert total == 1
        assert len(notes) == 1
        assert notes[0]["path"] == "pasta/nota.md"
        assert notes[0]["folder"] == "pasta"
        assert notes[0]["title"] == "nota"

    def test_list_with_folder_filter(self, tmp_path: Path):
        """Deve filtrar por pasta."""
        catalog = NotesCatalog(db_path=tmp_path / "catalog-folder.db")
        catalog._create_schema()

        with catalog._connection() as conn:
            conn.executemany(
                """
                INSERT INTO notes_catalog
                (path, folder, extension, title, mtime_ns, size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("projetos/a.md", "projetos", ".md", "a", 1000, 100),
                    ("projetos/sub/b.md", "projetos/sub", ".md", "b", 2000, 100),
                    ("estudos/c.md", "estudos", ".md", "c", 3000, 100),
                ],
            )

        # Filtrar por projetos (inclui subpastas)
        notes, total = catalog.list_notes(folder="projetos")

        assert total == 2
        paths = [note["path"] for note in notes]
        assert "projetos/a.md" in paths
        assert "projetos/sub/b.md" in paths
        assert "estudos/c.md" not in paths

    def test_list_with_extension_filter(self, tmp_path: Path):
        """Deve filtrar por extensão."""
        catalog = NotesCatalog(db_path=tmp_path / "catalog-extension.db")
        catalog._create_schema()

        with catalog._connection() as conn:
            conn.executemany(
                """
                INSERT INTO notes_catalog
                (path, folder, extension, title, mtime_ns, size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("nota.md", "", ".md", "nota", 1000, 100),
                    ("doc.pdf", "", ".pdf", "doc", 2000, 100),
                    ("diagrama.canvas", "", ".canvas", "diagrama", 3000, 100),
                ],
            )

        notes, total = catalog.list_notes(extension=".md")

        assert total == 1
        assert notes[0]["path"] == "nota.md"

    def test_list_with_pagination(self, tmp_path: Path):
        """Deve paginar resultados."""
        catalog = NotesCatalog(db_path=tmp_path / "catalog-pagination.db")
        catalog._create_schema()

        # Inserir 10 notas
        with catalog._connection() as conn:
            for i in range(10):
                conn.execute(
                    """
                        INSERT INTO notes_catalog
                        (path, folder, extension, title, mtime_ns, size)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (f"nota{i}.md", "", ".md", f"nota{i}", i * 1000, 100),
                )

        # Página 1
        notes1, total = catalog.list_notes(limit=3, offset=0)
        assert total == 10
        assert len(notes1) == 3

        # Página 2
        notes2, total = catalog.list_notes(limit=3, offset=3)
        assert total == 10
        assert len(notes2) == 3

        # Páginas não devem sobrepor
        paths1 = {note["path"] for note in notes1}
        paths2 = {note["path"] for note in notes2}
        assert paths1.isdisjoint(paths2)

    def test_list_ordered_by_mtime_desc(self, tmp_path: Path):
        """Deve ordenar por mtime decrescente."""
        catalog = NotesCatalog(db_path=tmp_path / "catalog-ordering.db")
        catalog._create_schema()

        with catalog._connection() as conn:
            conn.executemany(
                """
                INSERT INTO notes_catalog
                (path, folder, extension, title, mtime_ns, size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("old.md", "", ".md", "old", 1000, 100),
                    ("new.md", "", ".md", "new", 3000, 100),
                    ("mid.md", "", ".md", "mid", 2000, 100),
                ],
            )

        notes, _ = catalog.list_notes()

        # Mais recente primeiro
        assert notes[0]["path"] == "new.md"
        assert notes[1]["path"] == "mid.md"
        assert notes[2]["path"] == "old.md"

    def test_delete(self, tmp_path: Path):
        """Deve deletar nota do catálogo."""
        catalog = NotesCatalog(db_path=tmp_path / "catalog-delete.db")
        catalog._create_schema()

        with catalog._connection() as conn:
            conn.execute(
                """
                INSERT INTO notes_catalog
                (path, folder, extension, title, mtime_ns, size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("nota.md", "", ".md", "nota", 1000, 100),
            )

        catalog.delete("nota.md")

        notes, total = catalog.list_notes()
        assert total == 0

    def test_stats(self, tmp_path: Path):
        """Deve retornar estatísticas corretas."""
        catalog = NotesCatalog(db_path=tmp_path / "catalog-stats.db")
        catalog._create_schema()

        with catalog._connection() as conn:
            conn.executemany(
                """
                INSERT INTO notes_catalog
                (path, folder, extension, title, mtime_ns, size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("a.md", "", ".md", "a", 1000, 100),
                    ("b.md", "", ".md", "b", 2000, 100),
                    ("c.pdf", "", ".pdf", "c", 3000, 100),
                ],
            )

        stats = catalog.stats()

        assert stats["total_notes"] == 3
        assert stats["by_extension"][".md"] == 2
        assert stats["by_extension"][".pdf"] == 1
        assert "db_path" not in stats

    def test_get_all_folders_returns_distinct_non_root_paths(self, tmp_path: Path):
        catalog = NotesCatalog(db_path=tmp_path / "catalog-folders.db")
        catalog._create_schema()
        with catalog._connection() as conn:
            conn.executemany(
                """
                INSERT INTO notes_catalog
                (path, folder, extension, title, mtime_ns, size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("root.md", "", ".md", "root", 1, 1),
                    ("a/one.md", "a", ".md", "one", 2, 1),
                    ("a/two.md", "a", ".md", "two", 3, 1),
                    ("a/b/three.md", "a/b", ".md", "three", 4, 1),
                ],
            )

        assert catalog.get_all_folders() == ["a", "a/b"]

    def test_get_recent_notes_filters_and_orders_at_database_level(self, tmp_path: Path):
        catalog = NotesCatalog(db_path=tmp_path / "catalog-recent.db")
        catalog._create_schema()
        now_ns = time.time_ns()
        with catalog._connection() as conn:
            conn.executemany(
                """
                INSERT INTO notes_catalog
                (path, folder, extension, title, mtime_ns, size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("recent.md", "", ".md", "recent", now_ns, 1),
                    (
                        "old.md",
                        "",
                        ".md",
                        "old",
                        now_ns - 8 * 86_400 * 1_000_000_000,
                        1,
                    ),
                ],
            )

        notes = catalog.get_recent_notes(days=7, limit=50)

        assert [note["path"] for note in notes] == ["recent.md"]


class TestCatalogReconciliation:
    """Testes para reconciliação periódica."""

    def test_start_stop_reconciliation(self, tmp_path: Path):
        """Deve iniciar e parar thread de reconciliação."""
        catalog = NotesCatalog(db_path=tmp_path / "catalog-reconciliation.db")
        catalog._create_schema()

        catalog.start_reconciliation(interval=60)
        assert catalog._reconcile_thread is not None
        assert catalog._reconcile_thread.is_alive()

        catalog.stop_reconciliation()
        assert catalog._reconcile_thread is None

    def test_reconciliation_idempotent_start(self, tmp_path: Path):
        """Múltiplas chamadas a start_reconciliation não criam threads extras."""
        catalog = NotesCatalog(db_path=tmp_path / "catalog-idempotent.db")
        catalog._create_schema()

        catalog.start_reconciliation(interval=60)
        thread1 = catalog._reconcile_thread

        catalog.start_reconciliation(interval=60)
        thread2 = catalog._reconcile_thread

        assert thread1 is thread2

        catalog.stop_reconciliation()

    def test_stop_timeout_preserva_thread_viva_e_impede_restart(self, tmp_path: Path):
        """Timeout mantém a referência para bloquear uma segunda worker."""
        catalog = NotesCatalog(db_path=tmp_path / "catalog-timeout.db")
        thread = MagicMock()
        thread.is_alive.return_value = True
        catalog._reconcile_thread = thread

        assert catalog.stop_reconciliation() is False
        assert catalog._reconcile_thread is thread
        assert catalog.start_reconciliation(interval=60) is False
        assert catalog._reconcile_thread is thread


class TestCatalogWALMode:
    """Testes para SQLite WAL mode."""

    def test_wal_mode_enabled(self, tmp_path: Path):
        """Deve usar WAL mode para melhor concorrência."""
        catalog = NotesCatalog(db_path=tmp_path / "catalog-wal.db")
        catalog._create_schema()

        with catalog._connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

        # WAL ou wal (case insensitive)
        assert mode.lower() == "wal"
