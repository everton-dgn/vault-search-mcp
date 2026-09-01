"""
Obsidian vault note indexer for LanceDB.

Responsibilities:
- Generate embeddings with BGE-M3
- Store chunks and embeddings in LanceDB
- Complete and incremental indexing with parallel reads
- Periodic index compaction
- Index statistics
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NotRequired, TypedDict

import lancedb
import pyarrow as pa
import pyarrow.compute as pc
from lancedb.db import DBConnection
from lancedb.table import Table

from vault_search.config.embedding import EMBEDDING_DIMENSION
from vault_search.config.paths import (
    ALIASES_TABLE,
    DATA_DIR,
    LANCEDB_TABLE,
    LINKS_TABLE,
    VAULT_PATH,
)
from vault_search.config.search import (
    INDEXABLE_EXTENSIONS,
    MAX_CHUNKS_PER_NOTE,
    REINDEX_BATCH_SIZE,
    REINDEX_WORKERS,
    VectorIndexRuntimeConfig,
    get_optimal_batch_size,
    get_vector_index_config,
    get_vector_index_settings,
)
from vault_search.core.fts_builder import (
    CompactionStats,
    compact_table,
    create_fts_index,
    try_optimize,
)
from vault_search.core.models import ModelManager
from vault_search.core.scanner import scan_vault
from vault_search.crud.write import ensure_note_id
from vault_search.parsers import parse_file_result
from vault_search.type_defs import (
    AliasRecord,
    ChunkRecord,
    ChunkWithVector,
    FullReindexPreview,
    FullReindexStats,
    FullReindexStatus,
    IndexStats,
    LinkRecord,
    ParseResult,
    ParseStatus,
    ReindexResult,
    ReindexStatus,
)
from vault_search.utils.logging import get_logger
from vault_search.utils.security import escape_sql_string, validate_relative_path
from vault_search.utils.shutdown import protected_section, shutdown_requested

logger = get_logger(__name__)


class VectorIndexCreationResult(TypedDict):
    """Stable result for optional ANN index creation."""

    created: bool
    reason: str
    total_chunks: NotRequired[int]
    config: NotRequired[VectorIndexRuntimeConfig]
    duration_ms: NotRequired[float]


class VectorIndexStatus(TypedDict):
    """Public ANN index state."""

    exists: bool
    auto_create_enabled: bool
    threshold: int
    total_chunks: int
    would_create: bool


class SyncStats(TypedDict):
    """Counts produced by incremental synchronization."""

    vault_files: int
    indexed_files: int
    new_files: int
    modified_files: int
    deleted_files: int
    synced: int


# Compact automatically after a fixed number of incremental operations.
AUTO_COMPACT_THRESHOLD = 100
_STAGING_SUFFIX = "__reindex_staging"

_CHUNKS_SCHEMA = pa.schema(
    [
        pa.field("note_path", pa.utf8()),
        pa.field("note_title", pa.utf8()),
        pa.field("folder", pa.utf8()),
        pa.field("headers", pa.utf8()),
        pa.field("tags", pa.utf8()),
        pa.field("modified_at", pa.utf8()),
        pa.field("text", pa.utf8()),
        pa.field("vector", pa.list_(pa.float32(), EMBEDDING_DIMENSION)),
        pa.field("id", pa.utf8()),
        pa.field("created_at", pa.utf8()),
        pa.field("updated_at", pa.utf8()),
        pa.field("description", pa.utf8()),
        pa.field("status", pa.utf8()),
        pa.field("note_type", pa.utf8()),
        pa.field("category", pa.utf8()),
        pa.field("project", pa.utf8()),
        pa.field("source", pa.utf8()),
    ]
)

_LINKS_SCHEMA = pa.schema(
    [
        pa.field("from_note_path", pa.utf8()),
        pa.field("from_note_title", pa.utf8()),
        pa.field("link_type", pa.utf8()),
        pa.field("link_target", pa.utf8()),
        pa.field("link_target_normalized", pa.utf8()),
        pa.field("to_note_path", pa.utf8()),
        pa.field("is_resolved", pa.bool_()),
        pa.field("alias", pa.utf8()),
        pa.field("heading", pa.utf8()),
        pa.field("block_ref", pa.utf8()),
        pa.field("context", pa.utf8()),
        pa.field("modified_at", pa.utf8()),
    ]
)

_ALIASES_SCHEMA = pa.schema(
    [
        pa.field("note_path", pa.utf8()),
        pa.field("alias", pa.utf8()),
        pa.field("alias_normalized", pa.utf8()),
    ]
)


class VaultIndexer:
    """
    Manage Obsidian vault indexing in LanceDB.

    ``_write_lock`` serializes ``full_reindex`` and ``reindex_note`` writes
    to prevent races between the watcher and manual reindexing.

    FTS rebuilds run in the background after automatic compaction.

    A circuit breaker limits repeated reindexing of one note to prevent
    write-triggered watcher loops.

    Usage:
        indexer = VaultIndexer()
        indexer.full_reindex()       # Index everything
        indexer.reindex_note(path)   # Reindex one note
    """

    _write_lock = threading.Lock()
    _fts_rebuild_lock = threading.Lock()  # Prevent concurrent FTS rebuilds
    _circuit_breaker_lock = threading.Lock()  # Protect _reindex_attempts
    _fts_rebuild_in_progress = False

    # Circuit breaker: (path -> (attempt_count, first_attempt_time))
    _CIRCUIT_BREAKER_MAX_ATTEMPTS = 3
    _CIRCUIT_BREAKER_WINDOW_SECONDS = 60.0

    def __init__(self):
        self._models = ModelManager()
        self._db: DBConnection | None = None
        self._table: Table | None = None
        self._links_table: Table | None = None
        self._aliases_table: Table | None = None
        self._operations_since_compact = 0
        self._reindex_attempts: dict[str, tuple[int, float]] = {}

    def _check_circuit_breaker(self, path: str) -> bool:
        """
        Check whether the circuit breaker should block this note.

        Return ``True`` after too many reindexes in a short period, which may
        indicate an infinite loop between writes and the watcher.

        Clear expired entries to avoid a memory leak. Thread-safe through
        ``_circuit_breaker_lock``.
        """
        with self._circuit_breaker_lock:
            now = time.time()

            # Remove expired entries.
            expired = [
                p
                for p, (_, first_time) in self._reindex_attempts.items()
                if now - first_time > self._CIRCUIT_BREAKER_WINDOW_SECONDS
            ]
            for p in expired:
                del self._reindex_attempts[p]

            # Check the current attempt window.
            if path in self._reindex_attempts:
                count, first_time = self._reindex_attempts[path]
                if now - first_time <= self._CIRCUIT_BREAKER_WINDOW_SECONDS:
                    if count >= self._CIRCUIT_BREAKER_MAX_ATTEMPTS:
                        return True  # Block repeated reindexing.
                    self._reindex_attempts[path] = (count + 1, first_time)
                else:
                    # Reset an expired window.
                    self._reindex_attempts[path] = (1, now)
            else:
                self._reindex_attempts[path] = (1, now)

            return False

    def reset_circuit_breaker(self, path: str | None = None) -> None:
        """
        Reset the circuit breaker for one note or all notes.

        Useful for tests that perform repeated reindexing.

        Args:
            path: Note path to reset; ``None`` resets every note.
        """
        with self._circuit_breaker_lock:
            if path is None:
                self._reindex_attempts.clear()
            elif path in self._reindex_attempts:
                del self._reindex_attempts[path]

    def _connect_db(self) -> DBConnection:
        """Return the LanceDB connection, creating it when necessary."""
        if self._db is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(DATA_DIR))
        return self._db

    def _ensure_table(self, data: list[ChunkWithVector] | None = None) -> Table:
        """
        Return the LanceDB table, creating it when necessary.

        Reuse an existing handle because reopening the table may not see
        uncommitted LanceDB data.

        Parameters:
            data: initial rows when the table must be created
        """
        if self._table is not None:
            return self._table

        db = self._connect_db()

        if LANCEDB_TABLE in db.list_tables().tables:
            self._table = db.open_table(LANCEDB_TABLE)
        elif data:
            self._table = db.create_table(LANCEDB_TABLE, data=data)
        else:
            self._table = db.create_table(LANCEDB_TABLE, schema=_CHUNKS_SCHEMA)

        return self._table

    def _ensure_links_table(self):
        """
        Return the links table, creating it when necessary.

        Store extracted note links for fast backlink queries.
        """
        if self._links_table is not None:
            return self._links_table

        db = self._connect_db()

        if LINKS_TABLE in db.list_tables().tables:
            self._links_table = db.open_table(LINKS_TABLE)
        else:
            self._links_table = db.create_table(LINKS_TABLE, schema=_LINKS_SCHEMA)

        return self._links_table

    def _ensure_aliases_table(self):
        """
        Return the aliases table, creating it when necessary.

        Store frontmatter aliases for link resolution.
        """
        if self._aliases_table is not None:
            return self._aliases_table

        db = self._connect_db()

        if ALIASES_TABLE in db.list_tables().tables:
            self._aliases_table = db.open_table(ALIASES_TABLE)
        else:
            self._aliases_table = db.create_table(ALIASES_TABLE, schema=_ALIASES_SCHEMA)

        return self._aliases_table

    def _index_links(self, links: list[LinkRecord]) -> int:
        """
        Index links in the links_index table.

        Thread-safe when called under _write_lock.

        Parameters:
            links: ``LinkRecord`` entries to index.

        Returns:
            Number of indexed links.
        """
        if not links:
            return 0

        links_table = self._ensure_links_table()
        links_table.add(links)
        return len(links)

    def _index_aliases(self, note_path: str, aliases: list[str]) -> int:
        """
        Index note aliases in the ``note_aliases`` table.

        Thread-safe when called under _write_lock.

        Parameters:
            note_path: Relative note path.
            aliases: Frontmatter aliases.

        Returns:
            Number of indexed aliases.
        """
        if not aliases:
            return 0

        from vault_search.utils.links import normalize_link_target

        aliases_table = self._ensure_aliases_table()

        records: list[AliasRecord] = []
        for alias in aliases:
            if alias and alias.strip():
                records.append(
                    {
                        "note_path": note_path,
                        "alias": alias.strip(),
                        "alias_normalized": normalize_link_target(alias),
                    }
                )

        if records:
            aliases_table.add(records)
        return len(records)

    def _delete_note_links(self, note_path: str) -> None:
        """
        Remove every link for one note.

        Thread-safe when called under _write_lock.
        """
        escaped = escape_sql_string(note_path)
        try:
            links_table = self._ensure_links_table()
            links_table.delete(f"from_note_path = '{escaped}'")
        except Exception as e:
            logger.warning(
                "note_links_delete_failed",
                error_type=type(e).__name__,
            )

    def _delete_note_aliases(self, note_path: str) -> None:
        """
        Remove every alias for one note.

        Thread-safe when called under _write_lock.
        """
        escaped = escape_sql_string(note_path)
        try:
            aliases_table = self._ensure_aliases_table()
            aliases_table.delete(f"note_path = '{escaped}'")
        except Exception as e:
            logger.warning(
                "note_aliases_delete_failed",
                error_type=type(e).__name__,
            )

    def _resolve_link_targets(self) -> int:
        """
        Resolve links to identify ``to_note_path``.

        Run after complete indexing to map ``link_target_normalized`` values
        to existing vault note paths.

        Thread-safe when called under _write_lock.

        Returns:
            Number of resolved links.
        """
        from vault_search.utils.links import normalize_link_target

        links_table = self._ensure_links_table()
        aliases_table = self._ensure_aliases_table()
        chunks_table = self._ensure_table()

        # 1. Read every unique note_path from the index.
        try:
            total = chunks_table.count_rows()
            if total == 0:
                return 0

            arrow = chunks_table.search().select(["note_path"]).limit(total).to_arrow()
            all_paths = set(arrow.column("note_path").to_pylist())
        except Exception as e:
            logger.warning(
                "link_resolution_paths_failed",
                error_type=type(e).__name__,
            )
            return 0

        # 2. Build a normalized-target-to-path map.
        path_map: dict[str, str] = {}
        for path in all_paths:
            # Normalized stem, for example "note.md" -> "note".
            stem = normalize_link_target(Path(path).stem)
            if stem not in path_map:
                path_map[stem] = path

            # Normalized full path, for example "folder/note.md" -> "folder/note".
            full = normalize_link_target(path)
            if full not in path_map:
                path_map[full] = path

        # 3. Add aliases to the map.
        try:
            total_aliases = aliases_table.count_rows()
            if total_aliases > 0:
                alias_arrow = (
                    aliases_table.search()
                    .select(["alias_normalized", "note_path"])
                    .limit(total_aliases)
                    .to_arrow()
                )

                for alias_norm, note_path in zip(
                    alias_arrow.column("alias_normalized").to_pylist(),
                    alias_arrow.column("note_path").to_pylist(),
                    strict=True,
                ):
                    if alias_norm not in path_map:
                        path_map[alias_norm] = note_path
        except Exception as e:
            logger.warning(
                "link_resolution_aliases_failed",
                error_type=type(e).__name__,
            )

        # 4. Read unresolved links, including link_type and link_target for uniqueness.
        try:
            links_arrow = (
                links_table.search()
                .where("is_resolved = false AND link_type != 'external'")
                .select(["from_note_path", "link_target_normalized", "link_type", "link_target"])
                .limit(100000)
                .to_arrow()
            )

            unresolved = list(
                zip(
                    links_arrow.column("from_note_path").to_pylist(),
                    links_arrow.column("link_target_normalized").to_pylist(),
                    links_arrow.column("link_type").to_pylist(),
                    links_arrow.column("link_target").to_pylist(),
                    strict=True,
                )
            )
        except Exception as e:
            logger.warning(
                "link_resolution_query_failed",
                error_type=type(e).__name__,
            )
            return 0

        if not unresolved:
            return 0

        # 5. Resolve and update.
        resolved_count = 0
        updates = []

        for from_path, target_norm, link_type, link_target in unresolved:
            to_path = path_map.get(target_norm)
            if to_path:
                updates.append(
                    {
                        "from_note_path": from_path,
                        "link_target_normalized": target_norm,
                        "link_type": link_type,
                        "link_target": link_target,
                        "to_note_path": to_path,
                        "is_resolved": True,
                    }
                )
                resolved_count += 1

        # 6. Apply updates as delete-and-add batches. Include link_type and
        # link_target in the key so distinct links do not collapse.
        if updates:
            for update in updates:
                try:
                    from_escaped = escape_sql_string(update["from_note_path"])
                    link_type_escaped = escape_sql_string(update["link_type"])
                    link_target_escaped = escape_sql_string(update["link_target"])
                    # Read the original record to preserve other fields.
                    orig = (
                        links_table.search()
                        .where(
                            f"from_note_path = '{from_escaped}' AND "
                            f"link_type = '{link_type_escaped}' AND "
                            f"link_target = '{link_target_escaped}'"
                        )
                        .limit(1)
                        .to_list()
                    )

                    if orig:
                        record = orig[0]
                        record["to_note_path"] = update["to_note_path"]
                        record["is_resolved"] = True
                        # Delete with a key that includes link_type and link_target.
                        links_table.delete(
                            f"from_note_path = '{from_escaped}' AND "
                            f"link_type = '{link_type_escaped}' AND "
                            f"link_target = '{link_target_escaped}'"
                        )
                        links_table.add([record])
                except Exception as e:
                    logger.warning(
                        "link_resolution_update_failed",
                        error_type=type(e).__name__,
                    )

        logger.info(
            "links_resolved",
            resolved=resolved_count,
            total=len(unresolved),
        )
        return resolved_count

    def _reset_staging_tables(self, db: DBConnection) -> tuple[Table, Table, Table]:
        """Create empty generations without modifying canonical tables."""
        existing = set(db.list_tables().tables)

        def reset(name: str, schema: pa.Schema) -> Table:
            # Lance prints the physical path to stderr when ``overwrite`` creates
            # a missing dataset. ``create`` avoids that disclosure.
            mode = "overwrite" if name in existing else "create"
            return db.create_table(name, schema=schema, mode=mode)

        chunks = reset(f"{LANCEDB_TABLE}{_STAGING_SUFFIX}", _CHUNKS_SCHEMA)
        links = reset(f"{LINKS_TABLE}{_STAGING_SUFFIX}", _LINKS_SCHEMA)
        aliases = reset(f"{ALIASES_TABLE}{_STAGING_SUFFIX}", _ALIASES_SCHEMA)
        return chunks, links, aliases

    def _store_staging_batch(self, table: Table, batch: list[ChunkRecord]) -> int:
        """Generate vectors and persist a batch only in the staging table."""
        vectors = self._models.embed_corpus([chunk["text"] for chunk in batch])
        if len(vectors) != len(batch):
            raise ValueError("embedding count differs from batch size")

        records: list[ChunkWithVector] = [
            {**chunk, "vector": vector} for chunk, vector in zip(batch, vectors, strict=True)
        ]
        table.add(records)
        return len(records)

    @staticmethod
    def _table_reader(table: Table) -> pa.RecordBatchReader:
        """Return a batch stream for memory-bounded copying."""
        total = table.count_rows()
        return table.search().limit(total).to_batches(batch_size=REINDEX_BATCH_SIZE)

    def _replace_table_from_staging(
        self,
        db: DBConnection,
        canonical_name: str,
        staging_table: Table,
    ) -> tuple[Table, int | None]:
        """Publish staging in one Lance commit while retaining the previous version."""
        existing = canonical_name in db.list_tables().tables
        reader = self._table_reader(staging_table)

        if existing:
            canonical = db.open_table(canonical_name)
            previous_version = canonical.version
            canonical.add(reader, mode="overwrite")
            return canonical, previous_version

        canonical = db.create_table(canonical_name, data=reader)
        return canonical, None

    @staticmethod
    def _rollback_table(table: Table, previous_version: int | None) -> None:
        """Restore the previous version or empty a newly created table."""
        if previous_version is not None:
            table.restore(previous_version)
            return

        empty = pa.RecordBatchReader.from_batches(table.schema, [])
        table.add(empty, mode="overwrite")

    def _restore_canonical_handles(self, db: DBConnection) -> None:
        """Drop staging references and reopen only public tables."""
        tables = set(db.list_tables().tables)
        self._table = db.open_table(LANCEDB_TABLE) if LANCEDB_TABLE in tables else None
        self._links_table = db.open_table(LINKS_TABLE) if LINKS_TABLE in tables else None
        self._aliases_table = db.open_table(ALIASES_TABLE) if ALIASES_TABLE in tables else None

    def _parse_note(self, note: Path) -> ParseResult:
        """
        Parse a note and return its chunks, links, and aliases.

        Run in parallel through ``ThreadPoolExecutor`` without modifying shared state.

        Returns:
            ``ParseResult`` with explicit state and extracted data.
        """
        try:
            result = parse_file_result(note, VAULT_PATH)
            if result.chunks and len(result.chunks) > MAX_CHUNKS_PER_NOTE:
                logger.warning(
                    "note_chunks_truncated",
                    chunks=len(result.chunks),
                    limit=MAX_CHUNKS_PER_NOTE,
                )
                result.chunks = result.chunks[:MAX_CHUNKS_PER_NOTE]
            return result
        except Exception as e:
            logger.warning(
                "note_parse_unexpected_error",
                error_type=type(e).__name__,
            )
            return ParseResult(
                status=ParseStatus.ERROR,
                error_type=type(e).__name__,
            )

    def full_reindex(
        self,
        dry_run: bool = False,
    ) -> FullReindexStats | FullReindexPreview:
        """
        Rebuild the complete vault index.

        Optimizations:
        - Parallel file reads through ``ThreadPoolExecutor``
        - Dynamic batch size based on available RAM
        - ``write_lock`` serialization with watcher ``reindex_note()`` calls

        Parameters:
            dry_run: Return a preview without modifying the index.

        Returns:
            Statistics including ``total_notes``, ``total_chunks``, and
            ``duration_seconds``. A dry run returns observed counts only.
        """
        # A dry run reports what would be processed.
        notes = scan_vault(VAULT_PATH)

        if dry_run:
            # Count files by extension.
            by_extension: dict[str, int] = {}
            for note in notes:
                ext = note.suffix.lower()
                by_extension[ext] = by_extension.get(ext, 0) + 1

            return {
                "dry_run": True,
                "would_index": len(notes),
                "notes_by_extension": by_extension,
                "batch_size": get_optimal_batch_size(),
            }

        with self._write_lock:
            start = time.time()
            logger.info("full_reindex_started", notes=len(notes))
            db = self._connect_db()
            total_chunks = 0
            total_links = 0
            total_aliases = 0
            notes_indexed = 0
            parse_errors = 0
            batch: list[ChunkRecord] = []
            all_links: list[LinkRecord] = []
            all_aliases: list[tuple[str, list[str]]] = []
            interrupted = False

            def failure_stats(
                status: FullReindexStatus = FullReindexStatus.FAILED,
            ) -> FullReindexStats:
                self._restore_canonical_handles(db)
                stats: FullReindexStats = {
                    "status": status,
                    "total_notes": notes_indexed,
                    "total_chunks": total_chunks,
                    "duration_seconds": round(time.time() - start, 1),
                    "previous_index_preserved": True,
                }
                if parse_errors:
                    stats["parse_errors"] = parse_errors
                if status is FullReindexStatus.INTERRUPTED:
                    stats["timed_out"] = True
                    stats["indices_skipped"] = True
                return stats

            try:
                staging_chunks, staging_links, staging_aliases = self._reset_staging_tables(db)
                self._table = staging_chunks
                self._links_table = staging_links
                self._aliases_table = staging_aliases

                workers = REINDEX_WORKERS or min(32, (os.cpu_count() or 4) + 4)
                batch_size = get_optimal_batch_size()
                logger.info(
                    "full_reindex_workers_ready",
                    workers=workers,
                    batch_size=batch_size,
                )

                total_notes = len(notes)
                last_progress_log = 0
                progress_interval = max(1, min(100, total_notes // 10))

                with ThreadPoolExecutor(max_workers=workers) as executor:
                    future_to_note = {
                        executor.submit(self._parse_note, note): note for note in notes
                    }

                    for future in as_completed(future_to_note):
                        if shutdown_requested():
                            logger.warning(
                                "full_reindex_interrupted",
                                indexed=notes_indexed,
                                total=total_notes,
                            )
                            interrupted = True
                            break

                        note = future_to_note[future]
                        result = future.result()
                        if result.status is ParseStatus.ERROR:
                            parse_errors += 1
                            continue
                        if result.status is not ParseStatus.SUCCESS:
                            continue

                        notes_indexed += 1
                        batch.extend(result.chunks)
                        all_links.extend(result.links)
                        if result.aliases:
                            relative_path = str(note.relative_to(VAULT_PATH))
                            all_aliases.append((relative_path, result.aliases))

                        if notes_indexed - last_progress_log >= progress_interval:
                            logger.info(
                                "full_reindex_progress",
                                indexed=notes_indexed,
                                total=total_notes,
                                chunks=total_chunks + len(batch),
                            )
                            last_progress_log = notes_indexed

                        if len(batch) >= batch_size:
                            total_chunks += self._store_staging_batch(staging_chunks, batch)
                            batch = []

                if interrupted:
                    return failure_stats(FullReindexStatus.INTERRUPTED)

                if batch:
                    with protected_section("saving the final LanceDB batch"):
                        total_chunks += self._store_staging_batch(staging_chunks, batch)

                if parse_errors:
                    logger.warning(
                        "full_reindex_aborted_on_parse_errors",
                        errors=parse_errors,
                    )
                    return failure_stats()

                with protected_section("indexing links and aliases in staging"):
                    for relative_path, aliases in all_aliases:
                        total_aliases += self._index_aliases(relative_path, aliases)
                    if all_links:
                        total_links = self._index_links(all_links)
                    resolved = self._resolve_link_targets()
                    logger.info(
                        "full_reindex_graph_ready",
                        links=total_links,
                        aliases=total_aliases,
                        resolved=resolved,
                    )

            except Exception as e:
                logger.error(
                    "full_reindex_staging_failed",
                    error_type=type(e).__name__,
                )
                return failure_stats()

            committed: list[tuple[Table, int | None]] = []
            try:
                canonical_links, previous = self._replace_table_from_staging(
                    db, LINKS_TABLE, staging_links
                )
                committed.append((canonical_links, previous))

                canonical_aliases, previous = self._replace_table_from_staging(
                    db, ALIASES_TABLE, staging_aliases
                )
                committed.append((canonical_aliases, previous))

                canonical_chunks, previous = self._replace_table_from_staging(
                    db, LANCEDB_TABLE, staging_chunks
                )
                committed.append((canonical_chunks, previous))
            except Exception as e:
                logger.error(
                    "full_reindex_commit_failed",
                    error_type=type(e).__name__,
                )
                for table, previous_version in reversed(committed):
                    try:
                        self._rollback_table(table, previous_version)
                    except Exception as rollback_error:
                        logger.error(
                            "full_reindex_rollback_failed",
                            error_type=type(rollback_error).__name__,
                        )
                return failure_stats()

            self._table = canonical_chunks
            self._links_table = canonical_links
            self._aliases_table = canonical_aliases

            vector_index_result: VectorIndexCreationResult = {
                "created": False,
                "reason": "not_attempted",
            }
            with protected_section("creating FTS and vector indexes"):
                create_fts_index(canonical_chunks)
                try_optimize(canonical_chunks)
                vector_index_result = self._maybe_create_vector_index(replace_existing=True)

            self._operations_since_compact = 0
            stats: FullReindexStats = {
                "status": FullReindexStatus.COMPLETED,
                "total_notes": notes_indexed,
                "total_chunks": total_chunks,
                "duration_seconds": round(time.time() - start, 1),
            }
            if total_links:
                stats["total_links"] = total_links
            if total_aliases:
                stats["total_aliases"] = total_aliases
            if vector_index_result.get("created"):
                stats["vector_index_created"] = True
            logger.info(
                "full_reindex_completed",
                notes=notes_indexed,
                chunks=total_chunks,
                duration_seconds=stats["duration_seconds"],
            )
            return stats

    def _apply_note_records(
        self,
        note_relative_path: str,
        chunks: list[ChunkWithVector],
        links: list[LinkRecord],
        aliases: list[str],
    ) -> tuple[Table, int, int]:
        """Replace note records and restore versions after any failure."""
        escaped_path = escape_sql_string(note_relative_path)
        snapshots: list[tuple[Table, int]] = []
        table = self._ensure_table()

        try:
            snapshots.append((table, table.version))
            table.delete(f"note_path = '{escaped_path}'")
            if chunks:
                table.add(chunks)

            links_table = self._ensure_links_table()
            snapshots.append((links_table, links_table.version))
            links_table.delete(f"from_note_path = '{escaped_path}'")
            if links:
                links_table.add(links)

            aliases_table = self._ensure_aliases_table()
            snapshots.append((aliases_table, aliases_table.version))
            aliases_table.delete(f"note_path = '{escaped_path}'")

            aliases_count = self._index_aliases(note_relative_path, aliases)
            return table, len(links), aliases_count
        except Exception:
            for changed_table, previous_version in reversed(snapshots):
                try:
                    changed_table.restore(previous_version)
                except Exception as rollback_error:
                    logger.error(
                        "reindex_note_rollback_failed",
                        error_type=type(rollback_error).__name__,
                    )
            raise

    def _record_incremental_operation(self, table: Table) -> bool:
        """Compact after several mutations instead of optimizing every note."""
        self._operations_since_compact += 1
        if self._operations_since_compact < AUTO_COMPACT_THRESHOLD:
            return False

        with protected_section("compacting LanceDB index"):
            stats = compact_table(table)
        if not stats.get("compacted"):
            return False

        self._operations_since_compact = 0
        logger.info(
            "index_auto_compacted",
            operations=AUTO_COMPACT_THRESHOLD,
        )
        return True

    def reindex_note(
        self,
        note_relative_path: str,
        auto_generate_id: bool = True,
    ) -> ReindexResult:
        """
        Reindex one note incrementally.

        Parsing and embedding finish before the first mutation. The replacement
        uses LanceDB versions to restore chunks, links, and aliases after a failure.

        This does not rebuild FTS because O(N) work per note is impractical.
        Vector search updates immediately, and FTS updates on the next full reindex.

        Parameters:
            note_relative_path: Vault-relative path, for example ``folder/note.md``.

        Returns:
            A dictionary with ``chunks_indexed`` and ``status``.
        """
        if not validate_relative_path(note_relative_path):
            return {
                "chunks_indexed": 0,
                "status": ReindexStatus.REJECTED_PATH_TRAVERSAL,
            }

        # Compare the extension case-insensitively.
        if Path(note_relative_path).suffix.lower() not in INDEXABLE_EXTENSIONS:
            return {
                "chunks_indexed": 0,
                "status": ReindexStatus.REJECTED_EXTENSION,
            }

        # Circuit breaker for repeated reindex loops.
        if self._check_circuit_breaker(note_relative_path):
            logger.warning(
                "circuit_breaker_triggered",
            )
            return {
                "chunks_indexed": 0,
                "status": ReindexStatus.CIRCUIT_BREAKER_OPEN,
            }

        with self._write_lock:
            note_path = VAULT_PATH / note_relative_path

            if not note_path.exists():
                try:
                    table, _, _ = self._apply_note_records(note_relative_path, [], [], [])
                except Exception as e:
                    logger.error(
                        "reindex_note_delete_failed",
                        error_type=type(e).__name__,
                    )
                    return {
                        "chunks_indexed": 0,
                        "status": ReindexStatus.ERROR_ADD_FAILED,
                    }
                self._record_incremental_operation(table)
                return {
                    "chunks_indexed": 0,
                    "status": ReindexStatus.DELETED,
                }

            # Ensure Markdown notes have unique UUID v7 IDs. This modifies the
            # file and triggers the watcher once more; the second pass is a no-op.
            id_added = False
            if auto_generate_id and note_path.suffix.lower() == ".md":
                try:
                    id_result = ensure_note_id(note_relative_path)
                    if id_result.get("id_added"):
                        id_added = True
                        logger.info("auto_id_generated")
                except FileNotFoundError:
                    try:
                        table, _, _ = self._apply_note_records(note_relative_path, [], [], [])
                    except Exception as e:
                        logger.error(
                            "reindex_note_delete_failed",
                            error_type=type(e).__name__,
                        )
                        return {
                            "chunks_indexed": 0,
                            "status": ReindexStatus.ERROR_ADD_FAILED,
                        }
                    self._record_incremental_operation(table)
                    return {
                        "chunks_indexed": 0,
                        "status": ReindexStatus.DELETED,
                    }
                except PermissionError as e:
                    logger.warning(
                        "auto_id_permission_denied",
                        error_type=type(e).__name__,
                    )
                except (ValueError, OSError) as e:
                    logger.warning(
                        "auto_id_failed",
                        error_type=type(e).__name__,
                    )
                except Exception as e:
                    logger.error(
                        "auto_id_unexpected_error",
                        error_type=type(e).__name__,
                    )

            parsed = parse_file_result(note_path, VAULT_PATH)
            if parsed.status is ParseStatus.ERROR:
                if not note_path.exists():
                    try:
                        table, _, _ = self._apply_note_records(note_relative_path, [], [], [])
                    except Exception as e:
                        logger.error(
                            "reindex_note_delete_failed",
                            error_type=type(e).__name__,
                        )
                        return {
                            "chunks_indexed": 0,
                            "status": ReindexStatus.ERROR_ADD_FAILED,
                        }
                    self._record_incremental_operation(table)
                    return {
                        "chunks_indexed": 0,
                        "status": ReindexStatus.DELETED,
                    }
                logger.warning(
                    "reindex_note_parse_failed",
                    error_type=parsed.error_type or "UnknownError",
                )
                return {
                    "chunks_indexed": 0,
                    "status": ReindexStatus.PARSE_ERROR,
                }

            chunks = parsed.chunks
            links = parsed.links
            aliases = parsed.aliases

            # Limit chunks per note to prevent resource exhaustion.
            if chunks and len(chunks) > MAX_CHUNKS_PER_NOTE:
                logger.warning(
                    "note_chunks_truncated",
                    chunks=len(chunks),
                    limit=MAX_CHUNKS_PER_NOTE,
                )
                chunks = chunks[:MAX_CHUNKS_PER_NOTE]

            if not chunks:
                try:
                    table, _, _ = self._apply_note_records(note_relative_path, [], [], [])
                except Exception as e:
                    logger.error(
                        "reindex_note_empty_update_failed",
                        error_type=type(e).__name__,
                    )
                    return {
                        "chunks_indexed": 0,
                        "status": ReindexStatus.ERROR_ADD_FAILED,
                    }
                auto_compacted = self._record_incremental_operation(table)
                empty_result: ReindexResult = {
                    "chunks_indexed": 0,
                    "status": ReindexStatus.EMPTY,
                }
                if auto_compacted:
                    empty_result["auto_compacted"] = True
                    self._schedule_fts_rebuild_async()
                return empty_result

            try:
                vectors = self._models.embed_corpus([chunk["text"] for chunk in chunks])
                if len(vectors) != len(chunks):
                    raise ValueError("embedding count differs from chunk count")
            except Exception as e:
                logger.error(
                    "reindex_note_embedding_failed",
                    error_type=type(e).__name__,
                )
                return {
                    "chunks_indexed": 0,
                    "status": ReindexStatus.ERROR_ADD_FAILED,
                }

            chunks_with_vectors: list[ChunkWithVector] = [
                {**chunk, "vector": vector} for chunk, vector in zip(chunks, vectors, strict=True)
            ]

            try:
                with protected_section("updating note records"):
                    table, links_count, aliases_count = self._apply_note_records(
                        note_relative_path,
                        chunks_with_vectors,
                        links,
                        aliases,
                    )
            except Exception as e:
                logger.error(
                    "reindex_note_commit_failed",
                    error_type=type(e).__name__,
                )
                return {
                    "chunks_indexed": 0,
                    "status": ReindexStatus.ERROR_ADD_FAILED,
                }

            auto_compacted = self._record_incremental_operation(table)
            result: ReindexResult = {
                "chunks_indexed": len(chunks_with_vectors),
                "status": ReindexStatus.UPDATED,
            }
            if links_count > 0:
                result["links_indexed"] = links_count
            if aliases_count > 0:
                result["aliases_indexed"] = aliases_count
            if id_added:
                result["id_added"] = True
            if auto_compacted:
                result["auto_compacted"] = True
                # Schedule a non-blocking FTS rebuild after compaction.
                self._schedule_fts_rebuild_async()
            return result

    def _schedule_fts_rebuild_async(self) -> None:
        """
        Schedule an FTS rebuild in a background thread.

        Keep vector search available and prevent concurrent rebuilds with a dedicated lock.
        """

        def _rebuild_fts_background():
            """Worker thread for FTS rebuilds."""
            try:
                start = time.time()
                logger.info("fts_rebuild_started", background=True)

                # Read the table reference without the write lock.
                table = self._table
                if table is None:
                    logger.warning("fts_rebuild_skipped", reason="table_not_initialized")
                    return

                # Rebuild FTS through LanceDB's thread-safe operation.
                create_fts_index(table)

                duration_ms = (time.time() - start) * 1000
                logger.info(
                    "fts_rebuild_completed",
                    duration_ms=round(duration_ms, 1),
                    background=True,
                )
            except Exception as e:
                logger.warning(
                    "fts_rebuild_failed",
                    error_type=type(e).__name__,
                    background=True,
                )
            finally:
                with self._fts_rebuild_lock:
                    self._fts_rebuild_in_progress = False

        # Check and mark state inside the lock to avoid a race.
        with self._fts_rebuild_lock:
            if self._fts_rebuild_in_progress:
                logger.debug("FTS rebuild already in progress; skipping")
                return
            # Mark the rebuild in progress before spawning.
            self._fts_rebuild_in_progress = True

        # Spawn outside the lock because start() may block briefly.
        try:
            thread = threading.Thread(target=_rebuild_fts_background, daemon=True)
            thread.start()
            logger.debug("fts_rebuild_scheduled")
        except Exception as e:
            # Roll back the flag if thread creation fails.
            with self._fts_rebuild_lock:
                self._fts_rebuild_in_progress = False
            logger.error(
                "fts_rebuild_thread_start_failed",
                error_type=type(e).__name__,
            )

    def _has_vector_index(self) -> bool:
        """
        Check whether a vector index already exists on the table.

        Thread-safe when called under _write_lock.

        Returns:
            ``True`` when the ``vector`` column has an index.
        """
        try:
            if self._table is None:
                return False
            indices = self._table.list_indices()
            # LanceDB 0.29.2 returns IndexConfig; older versions and mocks use dicts.
            for index in indices:
                if isinstance(index, dict):
                    columns = index.get("columns", [])
                    name = index.get("name", "")
                else:
                    columns = getattr(index, "columns", [])
                    name = getattr(index, "name", "")
                if columns == ["vector"] or str(name).startswith("vector"):
                    return True
            return False
        except Exception as e:
            logger.warning(
                "vector_index_check_failed",
                error_type=type(e).__name__,
            )
            return False

    def _maybe_create_vector_index(
        self,
        replace_existing: bool = False,
    ) -> VectorIndexCreationResult:
        """
        Create a vector index when the dataset is large enough.

        Check whether automatic creation is enabled, an index is absent,
        and the chunk count meets the threshold.

        Must be called while holding ``_write_lock``.

        Returns:
            Operation status:
            - created: bool
            - ``reason``: outcome
            - ``config``: configuration when created
        """
        try:
            table = self._table
            if table is None:
                return {"created": False, "reason": "table_not_initialized"}

            total_chunks = table.count_rows()

            # ``None`` means the configuration does not require an index.
            settings = get_vector_index_settings()
            config = get_vector_index_config(total_chunks)

            if config is None:
                if total_chunks < settings.min_chunks:
                    return {
                        "created": False,
                        "reason": f"below_threshold ({total_chunks} < {settings.min_chunks})",
                        "total_chunks": total_chunks,
                    }
                return {"created": False, "reason": "auto_create_disabled"}

            # Check whether an index already exists.
            if self._has_vector_index() and not replace_existing:
                return {"created": False, "reason": "already_exists"}

            # Create the index.
            logger.info(
                "vector_index_creating",
                total_chunks=total_chunks,
                index_type=config["index_type"],
                num_partitions=config["num_partitions"],
            )

            start = time.time()

            if config["index_type"] == "IVF_PQ":
                if EMBEDDING_DIMENSION % config["num_sub_vectors"] != 0:
                    return {
                        "created": False,
                        "reason": "invalid_num_sub_vectors",
                    }
                table.create_index(
                    metric=config["distance_type"],
                    num_partitions=config["num_partitions"],
                    num_sub_vectors=config["num_sub_vectors"],
                    vector_column_name="vector",
                    replace=True,
                    index_type="IVF_PQ",
                )
            elif config["index_type"] == "IVF_HNSW_SQ":
                table.create_index(
                    metric=config["distance_type"],
                    num_partitions=config["num_partitions"],
                    vector_column_name="vector",
                    replace=True,
                    index_type="IVF_HNSW_SQ",
                )
            else:
                return {"created": False, "reason": f"unknown_index_type: {config['index_type']}"}

            duration_ms = (time.time() - start) * 1000
            logger.info(
                "vector_index_created",
                duration_ms=round(duration_ms, 1),
                total_chunks=total_chunks,
                config=config,
            )

            return {
                "created": True,
                "reason": "success",
                "total_chunks": total_chunks,
                "config": config,
                "duration_ms": round(duration_ms, 1),
            }

        except Exception as e:
            logger.error(
                "vector_index_creation_failed",
                error_type=type(e).__name__,
            )
            return {
                "created": False,
                "reason": f"error: {type(e).__name__}",
            }

    def get_vector_index_status(self) -> VectorIndexStatus:
        """
        Return vector-index status.

        Acquire ``_write_lock`` for a consistent read.

        Returns:
            A dictionary containing:
            - exists: bool
            - auto_create_enabled: bool
            - threshold: int
            - total_chunks: int
            - would_create: bool (whether it would be created now)
        """
        with self._write_lock:
            settings = get_vector_index_settings()
            table = self._table
            if table is None:
                try:
                    table = self._ensure_table()
                except Exception:
                    return {
                        "exists": False,
                        "auto_create_enabled": settings.auto_create,
                        "threshold": settings.min_chunks,
                        "total_chunks": 0,
                        "would_create": False,
                    }

            total_chunks = table.count_rows() if table else 0
            config = get_vector_index_config(total_chunks)

            return {
                "exists": self._has_vector_index(),
                "auto_create_enabled": settings.auto_create,
                "threshold": settings.min_chunks,
                "total_chunks": total_chunks,
                "would_create": config is not None and not self._has_vector_index(),
            }

    def compact(self) -> CompactionStats:
        """
        Compact the LanceDB index to reduce fragmentation.

        Useful after many incremental ``reindex_note`` operations. Group small
        files, retain recovery versions, and reset the automatic-compaction counter.

        Returns:
            Compaction statistics.
        """
        with self._write_lock:
            try:
                table = self._ensure_table()
                stats = compact_table(table)
                self._operations_since_compact = 0  # Reset the operation counter.
                logger.info(
                    "index_compaction_completed",
                    compacted=stats.get("compacted", False),
                    cleaned=stats.get("cleaned", False),
                )
                return stats
            except Exception as e:
                logger.error(
                    "index_compaction_failed",
                    error_type=type(e).__name__,
                )
                return {
                    "compacted": False,
                    "cleaned": False,
                    "error": type(e).__name__,
                }

    def sync_check(self, auto_sync: bool = True) -> SyncStats:
        """
        Compare vault files with the index and synchronize differences.

        Find new files, modified files with a newer vault timestamp, and files
        removed from the vault but still present in the index.

        Parameters:
            auto_sync: Reindex out-of-sync files automatically.

        Returns:
            Counts and paths for new, modified, deleted, and synchronized files.
        """
        from datetime import datetime

        logger.info("sync_check_started")

        # 1. Scan the vault.
        vault_files = scan_vault(VAULT_PATH)
        vault_map: dict[str, float] = {}  # relative_path -> mtime
        for vault_file in vault_files:
            try:
                relative = str(vault_file.relative_to(VAULT_PATH))
                mtime = vault_file.stat().st_mtime
                vault_map[relative] = mtime
            except OSError, ValueError:
                continue

        # 2. Read indexed files.
        indexed_map: dict[str, str] = {}  # relative_path -> modified_at (ISO)
        try:
            with self._write_lock:
                table = self._ensure_table()

            total = table.count_rows()
            if total > 0:
                # Read every unique note_path and modified_at value.
                arrow_table = (
                    table.search().select(["note_path", "modified_at"]).limit(total).to_arrow()
                )

                paths = arrow_table.column("note_path").to_pylist()
                mtimes = arrow_table.column("modified_at").to_pylist()

                # Use one chunk per note because modified_at is identical across chunks.
                for indexed_path, indexed_mtime in zip(paths, mtimes, strict=True):
                    if indexed_path not in indexed_map:
                        indexed_map[indexed_path] = indexed_mtime

        except Exception as e:
            logger.warning(
                "sync_check_read_failed",
                error_type=type(e).__name__,
            )
            # Treat an unreadable index as empty.
            indexed_map = {}

        # 3. Compare both sets to find differences.
        new_files: list[str] = []
        modified_files: list[str] = []
        deleted_files: list[str] = []

        vault_paths = set(vault_map.keys())
        indexed_paths = set(indexed_map.keys())

        # Files present only in the vault are new.
        for relative_path in vault_paths - indexed_paths:
            new_files.append(relative_path)

        # Files present only in the index were deleted.
        for relative_path in indexed_paths - vault_paths:
            deleted_files.append(relative_path)

        # Compare timestamps for files present in both sets.
        for relative_path in vault_paths & indexed_paths:
            vault_mtime = vault_map[relative_path]
            try:
                indexed_mtime_str = indexed_map[relative_path]
                indexed_mtime = datetime.fromisoformat(indexed_mtime_str).timestamp()

                # Allow one second for timestamp rounding.
                if vault_mtime > indexed_mtime + 1:
                    modified_files.append(relative_path)
            except ValueError, TypeError:
                # Treat unparseable timestamps as modified.
                modified_files.append(relative_path)

        stats: SyncStats = {
            "vault_files": len(vault_map),
            "indexed_files": len(indexed_map),
            "new_files": len(new_files),
            "modified_files": len(modified_files),
            "deleted_files": len(deleted_files),
            "synced": 0,
        }

        # 4. Synchronize when requested.
        if auto_sync and (new_files or modified_files or deleted_files):
            synced = 0
            total_to_sync = len(new_files) + len(modified_files) + len(deleted_files)

            logger.info(
                "sync_check_syncing",
                new=len(new_files),
                modified=len(modified_files),
                deleted=len(deleted_files),
            )

            # Process deleted files first.
            for relative_path in deleted_files:
                try:
                    self.reindex_note(relative_path)  # Missing files are removed from the index.
                    synced += 1
                except Exception as e:
                    logger.warning(
                        "sync_check_remove_failed",
                        error_type=type(e).__name__,
                    )

            # Process new and modified files.
            for relative_path in new_files + modified_files:
                try:
                    self.reindex_note(relative_path)
                    synced += 1
                except Exception as e:
                    logger.warning(
                        "sync_check_reindex_failed",
                        error_type=type(e).__name__,
                    )

            stats["synced"] = synced
            logger.info("sync_check_completed", synced=synced, total=total_to_sync)
        else:
            logger.info(
                "sync_check_completed",
                new=len(new_files),
                modified=len(modified_files),
                deleted=len(deleted_files),
                auto_sync=auto_sync,
            )

        return stats

    def get_stats(self) -> IndexStats:
        """
        Return current index statistics.

        Use native PyArrow column projection to avoid loading vectors into memory.

        Briefly acquire ``_write_lock`` to avoid racing with ``full_reindex``.

        Returns:
            ``total_chunks``, ``unique_notes``, and the last modification time.
        """
        try:
            with self._write_lock:
                table = self._ensure_table()
            total = table.count_rows()

            if total == 0:
                return {
                    "total_chunks": 0,
                    "unique_notes": 0,
                    "last_modified": None,
                }

            # Native PyArrow column projection without pandas or vectors.
            arrow_table = (
                table.search().select(["note_path", "modified_at"]).limit(total).to_arrow()
            )

            unique_notes = pc.count_distinct(arrow_table.column("note_path")).as_py()
            last_modified = pc.max(arrow_table.column("modified_at")).as_py()

            return {
                "total_chunks": total,
                "unique_notes": unique_notes,
                "last_modified": last_modified,
            }
        except Exception as e:
            logger.warning(
                "index_stats_failed",
                error_type=type(e).__name__,
            )
            return {
                "total_chunks": 0,
                "unique_notes": 0,
                "last_modified": None,
            }


if __name__ == "__main__":
    import argparse
    import logging
    import os
    import sys

    parser = argparse.ArgumentParser(description="Index a vault for semantic search")
    parser.add_argument(
        "--require-daemon",
        action="store_true",
        help="Fail when the daemon is unavailable instead of using local models. "
        "Also available through VAULT_SEARCH_REQUIRE_DAEMON=1.",
    )
    parser.add_argument(
        "--wait-daemon",
        type=float,
        metavar="SECONDS",
        help="Wait for the daemon; 0 waits indefinitely. "
        "Also available through VAULT_SEARCH_WAIT_DAEMON=<seconds>.",
    )
    args = parser.parse_args()

    # Environment-variable fallbacks.
    require_daemon = args.require_daemon or os.environ.get("VAULT_SEARCH_REQUIRE_DAEMON") == "1"
    wait_daemon = args.wait_daemon
    if wait_daemon is None and "VAULT_SEARCH_WAIT_DAEMON" in os.environ:
        wait_daemon = float(os.environ["VAULT_SEARCH_WAIT_DAEMON"])

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    # Check the daemon when requested.
    if require_daemon or wait_daemon is not None:
        from vault_search.core.models import ModelManager

        mm = ModelManager()
        max_wait = None if wait_daemon == 0 else wait_daemon

        if wait_daemon is not None:
            print(
                f"Waiting for daemon... (max: {'unbounded' if max_wait is None else f'{max_wait}s'})"
            )

        try:
            mm.require_daemon(max_wait=max_wait or 30.0 if require_daemon else max_wait)
        except RuntimeError as e:
            print(f"\nERROR: {type(e).__name__}", file=sys.stderr)
            sys.exit(1)

    indexer = VaultIndexer()
    stats = indexer.full_reindex()
    print(f"\nIndexing complete: {stats}")
