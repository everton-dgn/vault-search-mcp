"""
SQLite note catalog for bounded list_notes queries.

Avoids a full filesystem scan on each request. It stays synchronized through
an initial scan, incremental watcher updates, and periodic reconciliation.
"""

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from vault_search.config.paths import DB_DIR, VAULT_PATH
from vault_search.config.search import IGNORED_FOLDERS, INDEXABLE_EXTENSIONS
from vault_search.crud.types import NoteListItem
from vault_search.utils.security import escape_like_pattern

logger = logging.getLogger(__name__)

# Catalog settings.
CATALOG_DB_PATH = DB_DIR / "notes_catalog.db"
RECONCILE_INTERVAL_SECONDS = 120

CatalogRow = tuple[str, str, str, str, int, int]


class CatalogStats(TypedDict):
    """Public catalog statistics aggregated by extension."""

    total_notes: int
    by_extension: dict[str, int]


class NotesCatalog:
    """
    SQLite catalog of vault notes.

    Serves list_notes without scanning the filesystem for every request.

    Example:
        catalog = NotesCatalog()
        catalog.initialize()  # Initial scan.
        notes = catalog.list_notes(folder="projects", limit=20)

        # When the watcher observes a file change:
        catalog.upsert("folder/note.md")
        catalog.delete("folder/deleted.md")
    """

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or CATALOG_DB_PATH
        self._lock = threading.Lock()
        self._initialized = False
        self._reconcile_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._reconcile_lifecycle_lock = threading.Lock()

    @contextmanager
    def _connection(self):
        """Open a configured SQLite connection."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        # Performance pragmas
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _create_schema(self):
        """Create the table and indexes when absent."""
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notes_catalog (
                    path TEXT PRIMARY KEY,
                    folder TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    title TEXT NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    size INTEGER NOT NULL
                )
            """)
            # Index common query predicates.
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_folder
                ON notes_catalog(folder)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mtime
                ON notes_catalog(mtime_ns DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_extension
                ON notes_catalog(extension)
            """)

    def initialize(self, force_rebuild: bool = False):
        """
        Initialize the catalog with a complete vault scan.

        Parameters:
            force_rebuild: rebuild the catalog from scratch when true
        """
        with self._lock:
            if self._initialized and not force_rebuild:
                return

            logger.info("Initializing notes_catalog...")
            start = time.perf_counter()

            self._create_schema()

            # Scan the complete vault.
            notes_data = self._scan_vault()

            # Insert in one batch.
            with self._connection() as conn:
                if force_rebuild:
                    conn.execute("DELETE FROM notes_catalog")

                conn.executemany(
                    """
                    INSERT OR REPLACE INTO notes_catalog
                    (path, folder, extension, title, mtime_ns, size)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    notes_data,
                )

            elapsed = time.perf_counter() - start
            logger.info(
                "notes_catalog initialized notes=%d elapsed_seconds=%.2f",
                len(notes_data),
                elapsed,
            )

            self._initialized = True

    def _scan_vault(self) -> list[CatalogRow]:
        """Scan the vault and return rows ready for insertion."""
        notes_data: list[CatalogRow] = []
        stack: list[Path] = [VAULT_PATH]

        while stack:
            current_dir = stack.pop()

            try:
                with os.scandir(current_dir) as entries:
                    for entry in entries:
                        if entry.name in IGNORED_FOLDERS or entry.name.startswith("."):
                            continue

                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                            continue

                        if not entry.is_file(follow_symlinks=False):
                            continue

                        name = entry.name
                        dot_idx = name.rfind(".")
                        if dot_idx == -1:
                            continue

                        ext = name[dot_idx:].lower()
                        if ext not in INDEXABLE_EXTENSIONS:
                            continue

                        try:
                            stat = entry.stat()
                        except OSError:
                            continue

                        path = Path(entry.path)
                        relative_path = str(path.relative_to(VAULT_PATH))
                        folder = str(path.parent.relative_to(VAULT_PATH))
                        if folder == ".":
                            folder = ""

                        notes_data.append(
                            (
                                relative_path,
                                folder,
                                ext,
                                name[:dot_idx],  # Title defaults to the filename stem.
                                stat.st_mtime_ns,
                                stat.st_size,
                            )
                        )

            except PermissionError:
                logger.warning("catalog_scan_permission_denied")

        return notes_data

    def upsert(self, relative_path: str):
        """
        Insert or update a note in the catalog.

        The watcher calls this for created and modified files.
        """
        file_path = VAULT_PATH / relative_path

        if not file_path.exists():
            self.delete(relative_path)
            return

        try:
            stat = file_path.stat()
        except OSError:
            return

        ext = file_path.suffix.lower()
        if ext not in INDEXABLE_EXTENSIONS:
            return

        folder = str(file_path.parent.relative_to(VAULT_PATH))
        if folder == ".":
            folder = ""

        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO notes_catalog
                (path, folder, extension, title, mtime_ns, size)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    relative_path,
                    folder,
                    ext,
                    file_path.stem,
                    stat.st_mtime_ns,
                    stat.st_size,
                ),
            )

        logger.debug("catalog.upsert completed")

    def _upsert_batch(self, notes_data: list[CatalogRow]) -> None:
        """
        Insert or update multiple notes in one batch.

        Reconciliation uses this to avoid duplicate stat calls. Each tuple is
        (path, folder, extension, title, mtime_ns, size).
        """
        if not notes_data:
            return

        with self._connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO notes_catalog
                (path, folder, extension, title, mtime_ns, size)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                notes_data,
            )

        logger.debug("catalog_upsert_batch notes=%d", len(notes_data))

    def delete(self, relative_path: str):
        """
        Remove a note from the catalog.

        The watcher calls this when a file is deleted.
        """
        with self._connection() as conn:
            conn.execute("DELETE FROM notes_catalog WHERE path = ?", (relative_path,))
        logger.debug("catalog.delete completed")

    def list_notes(
        self,
        folder: str | None = None,
        extension: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[NoteListItem], int]:
        """
        List catalog notes with filters and pagination.

        Returns:
            Tuple of page results and total matches before pagination.
        """
        conditions: list[str] = []
        params: list[str | int] = []

        if folder:
            # Match an exact folder and its descendants.
            conditions.append("(folder = ? OR folder LIKE ? ESCAPE '\\')")
            escaped_folder = escape_like_pattern(folder)
            params.extend([folder, f"{escaped_folder}/%"])

        if extension:
            ext = extension.lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            conditions.append("extension = ?")
            params.append(ext)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        with self._connection() as conn:
            # Total count
            count_query = f"SELECT COUNT(*) FROM notes_catalog {where_clause}"
            total = conn.execute(count_query, params).fetchone()[0]

            # Paginated results
            query = f"""
                SELECT path, folder, extension, title, mtime_ns, size
                FROM notes_catalog
                {where_clause}
                ORDER BY mtime_ns DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()

        notes = [self._row_to_note(row) for row in rows]

        return notes, total

    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> NoteListItem:
        """Convert a SQLite row to the public listing contract."""
        return NoteListItem(
            path=row["path"],
            folder=row["folder"],
            extension=row["extension"],
            title=row["title"],
            modified_at=datetime.fromtimestamp(row["mtime_ns"] / 1_000_000_000).isoformat(),
            size_bytes=row["size"],
        )

    def get_all_folders(self) -> list[str]:
        """List distinct folders without exposing the absolute vault path."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT folder
                FROM notes_catalog
                WHERE folder != ''
                ORDER BY folder
                """
            ).fetchall()
        return [str(row["folder"]) for row in rows]

    def get_recent_notes(self, days: int = 7, limit: int = 50) -> list[NoteListItem]:
        """Return the most recent notes inside a bounded time window."""
        safe_days = max(1, min(days, 3650))
        safe_limit = max(1, min(limit, 5000))
        cutoff_ns = time.time_ns() - safe_days * 86_400 * 1_000_000_000
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT path, folder, extension, title, mtime_ns, size
                FROM notes_catalog
                WHERE mtime_ns >= ?
                ORDER BY mtime_ns DESC
                LIMIT ?
                """,
                (cutoff_ns, safe_limit),
            ).fetchall()
        return [self._row_to_note(row) for row in rows]

    def start_reconciliation(self, interval: int = RECONCILE_INTERVAL_SECONDS) -> bool:
        """Start reconciliation if no previous generation is alive."""
        with self._reconcile_lifecycle_lock:
            if self._reconcile_thread is not None and self._reconcile_thread.is_alive():
                logger.warning("catalog_reconcile_start_rejected reason=previous_generation_alive")
                return False

            self._reconcile_thread = None
            self._stop_event.clear()
            thread = threading.Thread(
                target=self._reconcile_loop,
                args=(interval,),
                daemon=True,
            )
            thread.start()
            self._reconcile_thread = thread
        logger.info("catalog_reconciliation started interval_seconds=%d", interval)
        return True

    def stop_reconciliation(self) -> bool:
        """Request shutdown and retain the thread if it misses the deadline."""
        with self._reconcile_lifecycle_lock:
            self._stop_event.set()
            thread = self._reconcile_thread
            if thread is None:
                return True

            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.error("catalog_reconcile_stop_timeout")
                return False

            self._reconcile_thread = None
        logger.info("catalog_reconciliation stopped")
        return True

    def _reconcile_loop(self, interval: int):
        """Run periodic reconciliation until shutdown."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=interval)
            if self._stop_event.is_set():
                break

            try:
                self._reconcile()
            except Exception as e:
                logger.error(
                    "catalog_reconcile_failed error_type=%s",
                    type(e).__name__,
                )

    def _reconcile(self):
        """
        Reconcile the catalog with the filesystem.

        Detect new, deleted, and modified files.
        """
        logger.debug("catalog_reconciliation started")
        start = time.perf_counter()

        # Scan the filesystem once and retain all data needed for the batch.
        scan_data = self._scan_vault()
        current_files = {}
        scan_by_path = {}
        for data in scan_data:
            path, folder, ext, title, mtime_ns, size = data
            current_files[path] = (mtime_ns, size)
            scan_by_path[path] = data

        # Read the current catalog state.
        with self._connection() as conn:
            rows = conn.execute("SELECT path, mtime_ns, size FROM notes_catalog").fetchall()
            catalog_files = {row["path"]: (row["mtime_ns"], row["size"]) for row in rows}

        # Compute the reconciliation delta.
        to_upsert_data = []
        to_delete = []

        # Use retained scan data for new and modified files.
        for path, (mtime_ns, size) in current_files.items():
            if path not in catalog_files:
                to_upsert_data.append(scan_by_path[path])
            elif catalog_files[path] != (mtime_ns, size):
                to_upsert_data.append(scan_by_path[path])

        # Deleted files.
        for path in catalog_files:
            if path not in current_files:
                to_delete.append(path)

        # Apply changes in batches without duplicate stat calls.
        if to_upsert_data:
            self._upsert_batch(to_upsert_data)

        for path in to_delete:
            self.delete(path)

        elapsed = time.perf_counter() - start
        if to_upsert_data or to_delete:
            logger.info(
                "catalog_reconciliation upserted=%d deleted=%d elapsed_seconds=%.2f",
                len(to_upsert_data),
                len(to_delete),
                elapsed,
            )

    def stats(self) -> CatalogStats:
        """Return catalog statistics."""
        with self._connection() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM notes_catalog").fetchone()[0])

            by_ext = conn.execute("""
                SELECT extension, COUNT(*) as count
                FROM notes_catalog
                GROUP BY extension
            """).fetchall()

        return {
            "total_notes": total,
            "by_extension": {str(row["extension"]): int(row["count"]) for row in by_ext},
        }

    def is_available(self) -> bool:
        """
        Check whether the catalog is initialized and queryable.

        Returns:
            True when the catalog is initialized and queryable.
        """
        if not self._initialized:
            return False

        try:
            with self._connection() as conn:
                conn.execute("SELECT 1 FROM notes_catalog LIMIT 1")
            return True
        except Exception:
            return False


# Singleton
_catalog: NotesCatalog | None = None
_catalog_lock = threading.Lock()


def get_catalog() -> NotesCatalog:
    """Return the process-wide catalog instance."""
    global _catalog
    with _catalog_lock:
        if _catalog is None:
            _catalog = NotesCatalog()
        return _catalog
