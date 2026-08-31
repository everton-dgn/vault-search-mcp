"""
Indexador de notas do vault Obsidian no LanceDB.

Responsável por:
- Gerar embeddings com BGE-M3
- Armazenar chunks + embeddings no LanceDB
- Indexação completa e incremental (com leitura paralela)
- Compactação periódica do índice
- Estatísticas do índice
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
    """Resultado estável da criação opcional do índice ANN."""

    created: bool
    reason: str
    total_chunks: NotRequired[int]
    config: NotRequired[VectorIndexRuntimeConfig]
    duration_ms: NotRequired[float]


class VectorIndexStatus(TypedDict):
    """Estado público do índice ANN."""

    exists: bool
    auto_create_enabled: bool
    threshold: int
    total_chunks: int
    would_create: bool


class SyncStats(TypedDict):
    """Contagens produzidas pela sincronização incremental."""

    vault_files: int
    indexed_files: int
    new_files: int
    modified_files: int
    deleted_files: int
    synced: int


# Compactação automática após N operações incrementais.
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
    Gerencia a indexação do vault Obsidian no LanceDB.

    Thread-safe: _write_lock serializa operações de escrita
    (full_reindex e reindex_note) para evitar race conditions
    entre o watcher e reindexações manuais.

    FTS Async: rebuild do índice FTS roda em background após
    compactação automática, sem bloquear operações.

    Circuit Breaker: limita reindex da mesma nota para evitar loops
    infinitos causados por escritas que disparam o watcher.

    Uso:
        indexer = VaultIndexer()
        indexer.full_reindex()       # indexa tudo
        indexer.reindex_note(path)   # reindexar uma nota
    """

    _write_lock = threading.Lock()
    _fts_rebuild_lock = threading.Lock()  # Evita múltiplos rebuilds simultâneos
    _circuit_breaker_lock = threading.Lock()  # Protege _reindex_attempts
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
        Verifica se o circuit breaker deve bloquear reindex desta nota.

        Retorna True se a nota foi reindexada muitas vezes em curto período
        (indica possível loop infinito entre escritas e watcher).

        Limpa entradas expiradas para evitar memory leak.
        Thread-safe via _circuit_breaker_lock.
        """
        with self._circuit_breaker_lock:
            now = time.time()

            # Limpar entradas antigas (garbage collection)
            expired = [
                p
                for p, (_, first_time) in self._reindex_attempts.items()
                if now - first_time > self._CIRCUIT_BREAKER_WINDOW_SECONDS
            ]
            for p in expired:
                del self._reindex_attempts[p]

            # Verificar tentativas atuais
            if path in self._reindex_attempts:
                count, first_time = self._reindex_attempts[path]
                if now - first_time <= self._CIRCUIT_BREAKER_WINDOW_SECONDS:
                    if count >= self._CIRCUIT_BREAKER_MAX_ATTEMPTS:
                        return True  # Bloquear
                    self._reindex_attempts[path] = (count + 1, first_time)
                else:
                    # Janela expirou, resetar
                    self._reindex_attempts[path] = (1, now)
            else:
                self._reindex_attempts[path] = (1, now)

            return False

    def reset_circuit_breaker(self, path: str | None = None) -> None:
        """
        Reseta o circuit breaker para uma nota ou todas.

        Útil para testes que fazem múltiplos reindex consecutivos.

        Args:
            path: Caminho da nota para resetar. Se None, reseta todas.
        """
        with self._circuit_breaker_lock:
            if path is None:
                self._reindex_attempts.clear()
            elif path in self._reindex_attempts:
                del self._reindex_attempts[path]

    def _connect_db(self) -> DBConnection:
        """Retorna conexão LanceDB, criando se necessário."""
        if self._db is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(DATA_DIR))
        return self._db

    def _ensure_table(self, data: list[ChunkWithVector] | None = None) -> Table:
        """
        Retorna a tabela LanceDB, criando se necessário.

        Reutiliza handle existente para evitar reabrir a tabela
        (que pode não ver dados não-commitados no LanceDB).

        Parâmetros:
            data: dados iniciais se a tabela precisar ser criada.
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
        Retorna a tabela de links, criando se necessário.

        Armazena links extraídos das notas para queries rápidas de backlinks.
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
        Retorna a tabela de aliases, criando se necessário.

        Armazena aliases das notas (do frontmatter) para resolução de links.
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
        Indexa links na tabela links_index.

        Thread-safe: chamado dentro do _write_lock.

        Parâmetros:
            links: lista de LinkRecord para indexar

        Retorna:
            Número de links indexados.
        """
        if not links:
            return 0

        links_table = self._ensure_links_table()
        links_table.add(links)
        return len(links)

    def _index_aliases(self, note_path: str, aliases: list[str]) -> int:
        """
        Indexa aliases de uma nota na tabela note_aliases.

        Thread-safe: chamado dentro do _write_lock.

        Parâmetros:
            note_path: caminho relativo da nota
            aliases: lista de aliases do frontmatter

        Retorna:
            Número de aliases indexados.
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
        Remove todos os links de uma nota específica.

        Thread-safe: chamado dentro do _write_lock.
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
        Remove todos os aliases de uma nota específica.

        Thread-safe: chamado dentro do _write_lock.
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
        Resolve links para identificar to_note_path.

        Executado após indexação completa. Mapeia link_target_normalized
        para note_path existentes no vault.

        Thread-safe: chamado dentro do _write_lock.

        Retorna:
            Número de links resolvidos.
        """
        from vault_search.utils.links import normalize_link_target

        links_table = self._ensure_links_table()
        aliases_table = self._ensure_aliases_table()
        chunks_table = self._ensure_table()

        # 1. Obter todos os note_path únicos do índice
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

        # 2. Construir mapa de resolução: normalized -> path
        path_map: dict[str, str] = {}
        for path in all_paths:
            # Stem normalizado (ex: "nota.md" -> "nota")
            stem = normalize_link_target(Path(path).stem)
            if stem not in path_map:
                path_map[stem] = path

            # Path completo normalizado (ex: "pasta/nota.md" -> "pasta/nota")
            full = normalize_link_target(path)
            if full not in path_map:
                path_map[full] = path

        # 3. Adicionar aliases ao mapa
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

        # 4. Obter links não resolvidos
        # IMPORTANTE: Buscar link_type e link_target também para identificação única
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

        # 5. Resolver e atualizar
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

        # 6. Aplicar updates em batch (delete + add para simplificar)
        # IMPORTANTE: Usar link_type e link_target na chave para não colapsar links distintos
        if updates:
            for update in updates:
                try:
                    from_escaped = escape_sql_string(update["from_note_path"])
                    link_type_escaped = escape_sql_string(update["link_type"])
                    link_target_escaped = escape_sql_string(update["link_target"])
                    # Buscar registro original para preservar outros campos
                    # Usar link_type + link_target para identificação única
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
                        # Delete com chave única (inclui link_type e link_target)
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
        """Cria gerações vazias sem tocar nas tabelas canônicas."""
        existing = set(db.list_tables().tables)

        def reset(name: str, schema: pa.Schema) -> Table:
            # Lance emite o caminho físico no stderr quando `overwrite` é usado
            # para criar um dataset ausente. `create` evita esse vazamento.
            mode = "overwrite" if name in existing else "create"
            return db.create_table(name, schema=schema, mode=mode)

        chunks = reset(f"{LANCEDB_TABLE}{_STAGING_SUFFIX}", _CHUNKS_SCHEMA)
        links = reset(f"{LINKS_TABLE}{_STAGING_SUFFIX}", _LINKS_SCHEMA)
        aliases = reset(f"{ALIASES_TABLE}{_STAGING_SUFFIX}", _ALIASES_SCHEMA)
        return chunks, links, aliases

    def _store_staging_batch(self, table: Table, batch: list[ChunkRecord]) -> int:
        """Gera vetores e persiste um batch somente na tabela de staging."""
        vectors = self._models.embed_corpus([chunk["text"] for chunk in batch])
        if len(vectors) != len(batch):
            raise ValueError("quantidade de embeddings diferente do batch")

        records: list[ChunkWithVector] = [
            {**chunk, "vector": vector} for chunk, vector in zip(batch, vectors, strict=True)
        ]
        table.add(records)
        return len(records)

    @staticmethod
    def _table_reader(table: Table) -> pa.RecordBatchReader:
        """Retorna stream de batches para cópia com memória limitada."""
        total = table.count_rows()
        return table.search().limit(total).to_batches(batch_size=REINDEX_BATCH_SIZE)

    def _replace_table_from_staging(
        self,
        db: DBConnection,
        canonical_name: str,
        staging_table: Table,
    ) -> tuple[Table, int | None]:
        """Publica staging em um commit Lance, mantendo a versão anterior."""
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
        """Restaura a versão anterior ou esvazia uma tabela recém-criada."""
        if previous_version is not None:
            table.restore(previous_version)
            return

        empty = pa.RecordBatchReader.from_batches(table.schema, [])
        table.add(empty, mode="overwrite")

    def _restore_canonical_handles(self, db: DBConnection) -> None:
        """Remove referências de staging e reabre apenas tabelas públicas."""
        tables = set(db.list_tables().tables)
        self._table = db.open_table(LANCEDB_TABLE) if LANCEDB_TABLE in tables else None
        self._links_table = db.open_table(LINKS_TABLE) if LINKS_TABLE in tables else None
        self._aliases_table = db.open_table(ALIASES_TABLE) if ALIASES_TABLE in tables else None

    def _parse_note(self, note: Path) -> ParseResult:
        """
        Parseia uma nota e retorna seus chunks, links e aliases.

        Executado em paralelo pelo ThreadPoolExecutor.
        Thread-safe: não modifica estado compartilhado.

        Retorna:
            ParseResult com estado explícito e dados extraídos.
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
        Reindexar todo o vault do zero.

        Otimizações:
        - Leitura paralela de arquivos com ThreadPoolExecutor
        - Batch size dinâmico baseado na RAM disponível
        - write_lock serializa com reindex_note() do watcher

        Parâmetros:
            dry_run: se True, apenas retorna preview sem executar

        Retorna:
            Dict com estatísticas: total_notes, total_chunks, duration_seconds.
            Se dry_run=True, retorna contagens observadas sem modificar o índice.
        """
        # Dry-run: apenas estima o que seria feito
        notes = scan_vault(VAULT_PATH)

        if dry_run:
            # Contar por extensão
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
                    with protected_section("salvando batch final no LanceDB"):
                        total_chunks += self._store_staging_batch(staging_chunks, batch)

                if parse_errors:
                    logger.warning(
                        "full_reindex_aborted_on_parse_errors",
                        errors=parse_errors,
                    )
                    return failure_stats()

                with protected_section("indexando links e aliases em staging"):
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
            with protected_section("criando índices FTS e vetorial"):
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
        """Substitui registros de uma nota e restaura versões em qualquer falha."""
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
        """Compacta em lote após várias mutações, sem otimizar a cada nota."""
        self._operations_since_compact += 1
        if self._operations_since_compact < AUTO_COMPACT_THRESHOLD:
            return False

        with protected_section("compactando índice LanceDB"):
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
        Reindexar uma nota específica (atualização incremental).

        O parsing e os embeddings terminam antes da primeira mutação. A troca usa
        versões do LanceDB para restaurar chunks, links e aliases em qualquer falha.

        NÃO reconstrói FTS (O(N) por nota é inviável). Busca vetorial
        fica atualizada imediatamente; FTS atualiza no próximo full_reindex.

        Parâmetros:
            note_relative_path: caminho relativo ao vault (ex: 'pasta/nota.md')

        Retorna:
            Dict com 'chunks_indexed' e 'status'.
        """
        if not validate_relative_path(note_relative_path):
            return {
                "chunks_indexed": 0,
                "status": ReindexStatus.REJECTED_PATH_TRAVERSAL,
            }

        # Extensão case-insensitive
        if Path(note_relative_path).suffix.lower() not in INDEXABLE_EXTENSIONS:
            return {
                "chunks_indexed": 0,
                "status": ReindexStatus.REJECTED_EXTENSION,
            }

        # Circuit breaker: evitar loops infinitos de reindex
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

            # Garantir que notas .md tenham ID único (UUID v7)
            # TRADE-OFF: Modifica o arquivo, o que dispara o watcher novamente.
            # Porém, na segunda execução ensure_note_id não faz nada (já tem ID),
            # então o loop para após uma reindexação extra.
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

            # Limitar chunks por nota (proteção contra resource exhaustion)
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
                    raise ValueError("quantidade de embeddings diferente dos chunks")
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
                with protected_section("atualizando registros da nota"):
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
                # FTS rebuild assíncrono após compactação (não bloqueia)
                self._schedule_fts_rebuild_async()
            return result

    def _schedule_fts_rebuild_async(self) -> None:
        """
        Agenda rebuild do FTS em background thread.

        Não bloqueia operações - busca vetorial continua funcionando.
        Evita múltiplos rebuilds simultâneos com lock dedicado.
        """

        def _rebuild_fts_background():
            """Worker thread para rebuild FTS."""
            try:
                start = time.time()
                logger.info("fts_rebuild_started", background=True)

                # Obter referência à tabela (sem write_lock - read only)
                table = self._table
                if table is None:
                    logger.warning("fts_rebuild_skipped", reason="table_not_initialized")
                    return

                # Rebuild FTS (operação thread-safe no LanceDB)
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

        # FIX: Check E spawn DENTRO do lock para evitar race condition
        with self._fts_rebuild_lock:
            if self._fts_rebuild_in_progress:
                logger.debug("FTS rebuild já em andamento, ignorando")
                return
            # Marcar como em progresso ANTES de tentar spawnar
            self._fts_rebuild_in_progress = True

        # Spawnar thread FORA do lock (start() pode bloquear brevemente)
        try:
            thread = threading.Thread(target=_rebuild_fts_background, daemon=True)
            thread.start()
            logger.debug("fts_rebuild_scheduled")
        except Exception as e:
            # Rollback do flag se falhar ao spawnar thread
            with self._fts_rebuild_lock:
                self._fts_rebuild_in_progress = False
            logger.error(
                "fts_rebuild_thread_start_failed",
                error_type=type(e).__name__,
            )

    def _has_vector_index(self) -> bool:
        """
        Verifica se já existe índice vetorial na tabela.

        Thread-safe: chamado dentro do _write_lock.

        Retorna:
            True se existe índice vetorial na coluna 'vector'.
        """
        try:
            if self._table is None:
                return False
            indices = self._table.list_indices()
            # 0.29.2 retorna IndexConfig; versões antigas e mocks usam dict.
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
        Cria índice vetorial se o dataset for grande o suficiente.

        Verifica:
        1. Se auto-criação está habilitada
        2. Se já não existe índice
        3. Se o número de chunks atinge o threshold

        Thread-safe: deve ser chamado dentro do _write_lock.

        Retorna:
            Dict com status da operação:
            - created: bool
            - reason: str (motivo de não criar ou sucesso)
            - config: dict (se criado)
        """
        try:
            table = self._table
            if table is None:
                return {"created": False, "reason": "table_not_initialized"}

            total_chunks = table.count_rows()

            # Obter configuração (retorna None se não deve criar)
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

            # Verificar se já existe
            if self._has_vector_index() and not replace_existing:
                return {"created": False, "reason": "already_exists"}

            # Criar índice
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
        Retorna status do índice vetorial.

        Thread-safe: adquire _write_lock para leitura consistente.

        Retorna:
            Dict com:
            - exists: bool
            - auto_create_enabled: bool
            - threshold: int
            - total_chunks: int
            - would_create: bool (se recriado agora)
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
        Compacta o índice LanceDB para reduzir fragmentação.

        Útil após muitas operações incrementais (reindex_note).
        Agrupa arquivos pequenos e mantém versões para recuperação.
        Reseta o contador de auto-compactação.

        Retorna:
            Dict com estatísticas da compactação.
        """
        with self._write_lock:
            try:
                table = self._ensure_table()
                stats = compact_table(table)
                self._operations_since_compact = 0  # Reset contador
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
        Verifica e sincroniza arquivos do vault com o índice.

        Compara arquivos no vault com o índice para encontrar:
        - Arquivos novos (no vault mas não no índice)
        - Arquivos modificados (mtime do vault > mtime do índice)
        - Arquivos deletados (no índice mas não no vault)

        Parâmetros:
            auto_sync: se True, reindexar arquivos fora de sincronia automaticamente

        Retorna:
            Dict com new_files, modified_files, deleted_files e synced counts.
        """
        from datetime import datetime

        logger.info("sync_check_started")

        # 1. Scan do vault
        vault_files = scan_vault(VAULT_PATH)
        vault_map: dict[str, float] = {}  # relative_path -> mtime
        for vault_file in vault_files:
            try:
                relative = str(vault_file.relative_to(VAULT_PATH))
                mtime = vault_file.stat().st_mtime
                vault_map[relative] = mtime
            except OSError, ValueError:
                continue

        # 2. Obter arquivos indexados
        indexed_map: dict[str, str] = {}  # relative_path -> modified_at (ISO)
        try:
            with self._write_lock:
                table = self._ensure_table()

            total = table.count_rows()
            if total > 0:
                # Query para obter todos note_path e modified_at únicos
                arrow_table = (
                    table.search().select(["note_path", "modified_at"]).limit(total).to_arrow()
                )

                paths = arrow_table.column("note_path").to_pylist()
                mtimes = arrow_table.column("modified_at").to_pylist()

                # Usar o primeiro chunk de cada nota (todos têm o mesmo modified_at)
                for indexed_path, indexed_mtime in zip(paths, mtimes, strict=True):
                    if indexed_path not in indexed_map:
                        indexed_map[indexed_path] = indexed_mtime

        except Exception as e:
            logger.warning(
                "sync_check_read_failed",
                error_type=type(e).__name__,
            )
            # Se não conseguiu ler o índice, assume vazio
            indexed_map = {}

        # 3. Comparar para encontrar diferenças
        new_files: list[str] = []
        modified_files: list[str] = []
        deleted_files: list[str] = []

        vault_paths = set(vault_map.keys())
        indexed_paths = set(indexed_map.keys())

        # Arquivos novos: no vault mas não no índice
        for relative_path in vault_paths - indexed_paths:
            new_files.append(relative_path)

        # Arquivos deletados: no índice mas não no vault
        for relative_path in indexed_paths - vault_paths:
            deleted_files.append(relative_path)

        # Arquivos modificados: comparar mtimes
        for relative_path in vault_paths & indexed_paths:
            vault_mtime = vault_map[relative_path]
            try:
                indexed_mtime_str = indexed_map[relative_path]
                indexed_mtime = datetime.fromisoformat(indexed_mtime_str).timestamp()

                # Margem de 1 segundo para evitar falsos positivos por arredondamento
                if vault_mtime > indexed_mtime + 1:
                    modified_files.append(relative_path)
            except ValueError, TypeError:
                # Se não conseguir parsear, considerar modificado
                modified_files.append(relative_path)

        stats: SyncStats = {
            "vault_files": len(vault_map),
            "indexed_files": len(indexed_map),
            "new_files": len(new_files),
            "modified_files": len(modified_files),
            "deleted_files": len(deleted_files),
            "synced": 0,
        }

        # 4. Sincronizar se solicitado
        if auto_sync and (new_files or modified_files or deleted_files):
            synced = 0
            total_to_sync = len(new_files) + len(modified_files) + len(deleted_files)

            logger.info(
                "sync_check_syncing",
                new=len(new_files),
                modified=len(modified_files),
                deleted=len(deleted_files),
            )

            # Processar deletados primeiro
            for relative_path in deleted_files:
                try:
                    self.reindex_note(relative_path)  # Detecta ausência e remove do índice.
                    synced += 1
                except Exception as e:
                    logger.warning(
                        "sync_check_remove_failed",
                        error_type=type(e).__name__,
                    )

            # Processar novos e modificados
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
        Retorna estatísticas do índice atual.

        Usa PyArrow nativo (sem pandas) com column projection
        para evitar carregar vetores na memória.

        Thread-safe: adquire _write_lock brevemente para evitar
        race condition com full_reindex (que dropa a tabela).

        Retorna:
            Dict com total_chunks, unique_notes, última modificação.
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

            # PyArrow nativo com column projection (sem pandas, sem vetor)
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

    parser = argparse.ArgumentParser(description="Indexador de vault para busca semântica")
    parser.add_argument(
        "--require-daemon",
        action="store_true",
        help="Falha se daemon não disponível (não usa modelos locais). "
        "Também via env VAULT_SEARCH_REQUIRE_DAEMON=1",
    )
    parser.add_argument(
        "--wait-daemon",
        type=float,
        metavar="SECONDS",
        help="Aguarda daemon ficar disponível (0 = indefinido). "
        "Também via env VAULT_SEARCH_WAIT_DAEMON=<seconds>",
    )
    args = parser.parse_args()

    # Variáveis de ambiente como fallback
    require_daemon = args.require_daemon or os.environ.get("VAULT_SEARCH_REQUIRE_DAEMON") == "1"
    wait_daemon = args.wait_daemon
    if wait_daemon is None and "VAULT_SEARCH_WAIT_DAEMON" in os.environ:
        wait_daemon = float(os.environ["VAULT_SEARCH_WAIT_DAEMON"])

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    # Verificar daemon se requisitado
    if require_daemon or wait_daemon is not None:
        from vault_search.core.models import ModelManager

        mm = ModelManager()
        max_wait = None if wait_daemon == 0 else wait_daemon

        if wait_daemon is not None:
            print(
                f"Aguardando daemon... (max: {'indefinido' if max_wait is None else f'{max_wait}s'})"
            )

        try:
            mm.require_daemon(max_wait=max_wait or 30.0 if require_daemon else max_wait)
        except RuntimeError as e:
            print(f"\nERRO: {type(e).__name__}", file=sys.stderr)
            sys.exit(1)

    indexer = VaultIndexer()
    stats = indexer.full_reindex()
    print(f"\nIndexação finalizada: {stats}")
