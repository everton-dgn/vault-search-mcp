"""
Catálogo SQLite de notas para list_notes() rápido.

Substitui scan O(N) do filesystem por query SQL instantânea.
Mantido sincronizado via:
- Scan inicial no startup
- Updates incrementais via watcher
- Reconciliação periódica (default: 2min)
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

# Configurações
CATALOG_DB_PATH = DB_DIR / "notes_catalog.db"
RECONCILE_INTERVAL_SECONDS = 120  # 2 minutos

CatalogRow = tuple[str, str, str, str, int, int]


class CatalogStats(TypedDict):
    """Estatísticas públicas agregadas por extensão."""

    total_notes: int
    by_extension: dict[str, int]


class NotesCatalog:
    """
    Catálogo SQLite de notas do vault.

    Permite list_notes() em O(1) ao invés de O(N) filesystem scan.

    Uso:
        catalog = NotesCatalog()
        catalog.initialize()  # Scan inicial
        notes = catalog.list_notes(folder="projetos", limit=20)

        # Quando arquivo muda (via watcher):
        catalog.upsert("pasta/nota.md")
        catalog.delete("pasta/deletada.md")
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
        """Context manager para conexão SQLite."""
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
        """Cria tabela e índices se não existirem."""
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
            # Índices para queries comuns
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
        Inicializa catálogo com scan completo do vault.

        Parâmetros:
            force_rebuild: se True, apaga e reconstrói do zero
        """
        with self._lock:
            if self._initialized and not force_rebuild:
                return

            logger.info("Inicializando notes_catalog...")
            start = time.perf_counter()

            self._create_schema()

            # Scan completo do vault
            notes_data = self._scan_vault()

            # Inserir em batch
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
            logger.info(f"notes_catalog inicializado: {len(notes_data)} notas em {elapsed:.2f}s")

            self._initialized = True

    def _scan_vault(self) -> list[CatalogRow]:
        """Scan do vault retornando dados para inserção."""
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
                                name[:dot_idx],  # title = filename sem extensão
                                stat.st_mtime_ns,
                                stat.st_size,
                            )
                        )

            except PermissionError:
                logger.warning("catalog_scan_permission_denied")

        return notes_data

    def upsert(self, relative_path: str):
        """
        Insere ou atualiza uma nota no catálogo.

        Chamado pelo watcher quando arquivo é criado/modificado.
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
        Insere ou atualiza múltiplas notas de uma vez (batch).

        Usado por _reconcile para evitar stat() duplicado.
        notes_data é lista de tuplas (path, folder, ext, title, mtime_ns, size).
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

        logger.debug(f"catalog._upsert_batch: {len(notes_data)} notas")

    def delete(self, relative_path: str):
        """
        Remove uma nota do catálogo.

        Chamado pelo watcher quando arquivo é deletado.
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
        Lista notas do catálogo com filtros e paginação.

        Retorna:
            Tupla (lista de notas, total sem paginação)
        """
        conditions: list[str] = []
        params: list[str | int] = []

        if folder:
            # Pasta exata ou subpastas
            # Mantém consistência para filtros LIKE com subpastas.
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
        """Converte uma linha SQLite no contrato público de listagem."""
        return NoteListItem(
            path=row["path"],
            folder=row["folder"],
            extension=row["extension"],
            title=row["title"],
            modified_at=datetime.fromtimestamp(row["mtime_ns"] / 1_000_000_000).isoformat(),
            size_bytes=row["size"],
        )

    def get_all_folders(self) -> list[str]:
        """Lista pastas distintas sem expor o path absoluto do vault."""
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
        """Retorna as notas mais recentes dentro de uma janela fechada."""
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
        """Inicia reconciliação se nenhuma geração anterior estiver viva."""
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
        logger.info(f"Reconciliação iniciada (intervalo: {interval}s)")
        return True

    def stop_reconciliation(self) -> bool:
        """Solicita parada e preserva referência se a thread exceder o prazo."""
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
        logger.info("Reconciliação parada")
        return True

    def _reconcile_loop(self, interval: int):
        """Loop de reconciliação periódica."""
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
        Reconcilia catálogo com filesystem.

        Detecta:
        - Arquivos novos não no catálogo
        - Arquivos deletados ainda no catálogo
        - Arquivos modificados (mtime diferente)
        """
        logger.debug("Iniciando reconciliação...")
        start = time.perf_counter()

        # Scan atual do filesystem - já inclui todos os dados necessários
        scan_data = self._scan_vault()
        current_files = {}
        scan_by_path = {}
        for data in scan_data:
            path, folder, ext, title, mtime_ns, size = data
            current_files[path] = (mtime_ns, size)
            scan_by_path[path] = data  # Guardar dados completos para batch upsert

        # Obter estado do catálogo
        with self._connection() as conn:
            rows = conn.execute("SELECT path, mtime_ns, size FROM notes_catalog").fetchall()
            catalog_files = {row["path"]: (row["mtime_ns"], row["size"]) for row in rows}

        # Detectar diferenças
        to_upsert_data = []  # Agora é lista de dados completos, não só paths
        to_delete = []

        # Novos ou modificados - usar dados do scan (evita stat() duplicado)
        for path, (mtime_ns, size) in current_files.items():
            if path not in catalog_files:
                to_upsert_data.append(scan_by_path[path])
            elif catalog_files[path] != (mtime_ns, size):
                to_upsert_data.append(scan_by_path[path])

        # Deletados
        for path in catalog_files:
            if path not in current_files:
                to_delete.append(path)

        # Aplicar mudanças em batch (evita stat() duplicado)
        if to_upsert_data:
            self._upsert_batch(to_upsert_data)

        for path in to_delete:
            self.delete(path)

        elapsed = time.perf_counter() - start
        if to_upsert_data or to_delete:
            logger.info(
                f"Reconciliação: +{len(to_upsert_data)} -{len(to_delete)} em {elapsed:.2f}s"
            )

    def stats(self) -> CatalogStats:
        """Retorna estatísticas do catálogo."""
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
        Verifica se o catálogo está disponível e funcionando.

        Retorna:
            True se o catálogo está inicializado e pode ser consultado.
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
    """Obtém instância singleton do catálogo."""
    global _catalog
    with _catalog_lock:
        if _catalog is None:
            _catalog = NotesCatalog()
        return _catalog
