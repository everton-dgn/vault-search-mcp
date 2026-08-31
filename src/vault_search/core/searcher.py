"""
Motor de busca semântica para o vault Obsidian.

Responsável por:
- Busca vetorial (semântica) no LanceDB
- Busca híbrida (semântica + keyword via FTS)
- Reranking com cross-encoder MiniLM-L-6-v2
- Filtragem por pasta
- Cache de embeddings de query (evita recomputar queries repetidas)
- Prewarm de índices (carrega na RAM para baixa latência)
"""

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import NotRequired, TypedDict

import lancedb
from lancedb.db import DBConnection
from lancedb.table import Table

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

from vault_search.config.paths import DATA_DIR, LANCEDB_TABLE
from vault_search.config.search import (
    FTS_SEARCH_COLUMNS,
    HYBRID_RRF_K,
    PREWARM_BYTES_PER_CHUNK,
    PREWARM_ENABLED,
    PREWARM_MAX_RAM_PERCENT,
    PREWARM_MIN_AVAILABLE_RAM,
    RERANK_CANDIDATES_MAX,
    RERANK_CANDIDATES_MULTIPLIER,
    SCORE_PRECISION,
    SEARCH_CANDIDATES,
    SEARCH_CANDIDATES_MAX,
    SEARCH_CANDIDATES_MULTIPLIER,
    SEARCH_COLUMNS,
    SEARCH_TOP_K,
    get_vector_index_distance_type,
)
from vault_search.config.security import INDEX_NOT_FOUND_ERROR
from vault_search.core.highlight import apply_highlight
from vault_search.core.models import ModelManager
from vault_search.core.result_formatter import format_search_results
from vault_search.type_defs import (
    DuplicateGroup,
    DuplicateNoteResult,
    SearchResult,
    SearchRow,
    SimilarNoteResult,
)
from vault_search.utils.security import escape_like_pattern, escape_sql_string

logger = logging.getLogger(__name__)

# Tamanho máximo do cache de embeddings de query
QUERY_EMBEDDING_CACHE_SIZE = 1000


class EmbeddingCacheStats(TypedDict):
    """Contadores públicos do cache de embeddings."""

    size: int
    max_size: int
    hits: int
    misses: int
    hit_rate: float


class PrewarmStatus(TypedDict, total=False):
    """Estado detalhado do prewarm dos índices."""

    enabled: bool
    status: str
    indices_prewarmed: int
    failed_indices: int
    skipped_reason: str | None
    prewarmed_at: str | None
    duration_ms: float
    row_count: int | None


class _FusedEntry(TypedDict):
    row: SearchRow
    score: float
    best_rank: int
    order: int


class _SimilarCandidate(TypedDict):
    note_path: str
    note_title: str
    folder: str
    tags: str
    _distance: float


class _NoteEmbedding(TypedDict):
    note_path: str
    note_title: str
    folder: str
    vectors: list[list[float]]
    avg_vector: NotRequired[list[float] | None]


def _compute_candidates(top_k: int) -> int:
    """
    Calcula número de candidatos para busca vetorial.

    Garante que há sempre candidatos suficientes para o reranker,
    mesmo quando top_k > SEARCH_CANDIDATES.

    Parâmetros:
        top_k: número de resultados finais desejados

    Retorna:
        Número de candidatos (mínimo SEARCH_CANDIDATES, até SEARCH_CANDIDATES_MAX).
    """
    return min(
        max(SEARCH_CANDIDATES, top_k * SEARCH_CANDIDATES_MULTIPLIER),
        SEARCH_CANDIDATES_MAX,
    )


def _compute_rerank_pool_size(top_k: int, candidate_count: int) -> int:
    """
    Calcula quantos candidatos enviar para o cross-encoder.

    Estratégia:
    - Nunca abaixo de top_k (garante resultados suficientes)
    - Janela típica: top_k * RERANK_CANDIDATES_MULTIPLIER
    - Cap rígido via RERANK_CANDIDATES_MAX para evitar latência excessiva

    Parâmetros:
        top_k: número de resultados finais desejados
        candidate_count: quantidade de candidatos disponíveis

    Retorna:
        Quantidade de candidatos para reranking.
    """
    if candidate_count <= 0:
        return 0

    pool_size = max(
        top_k,
        min(RERANK_CANDIDATES_MAX, top_k * RERANK_CANDIDATES_MULTIPLIER),
    )
    return min(candidate_count, pool_size)


def _fuse_hybrid_results(
    vector_results: list[SearchRow],
    fts_results: list[SearchRow],
    limit: int,
) -> list[SearchRow]:
    """Combina rankings vetorial e lexical com Reciprocal Rank Fusion."""
    if limit <= 0:
        return []

    fused: dict[tuple[str, str], _FusedEntry] = {}
    insertion_order = 0

    for results in (vector_results, fts_results):
        for rank, result in enumerate(results, start=1):
            key = (result.get("note_path", ""), result.get("text", ""))
            entry = fused.get(key)
            if entry is None:
                entry = {
                    "row": result.copy(),
                    "score": 0.0,
                    "best_rank": rank,
                    "order": insertion_order,
                }
                fused[key] = entry
                insertion_order += 1

            entry["score"] += 1.0 / (HYBRID_RRF_K + rank)
            entry["best_rank"] = min(entry["best_rank"], rank)

    ranked = sorted(
        fused.values(),
        key=lambda entry: (
            -entry["score"],
            entry["best_rank"],
            entry["order"],
        ),
    )

    output: list[SearchRow] = []
    for entry in ranked[:limit]:
        row = entry["row"]
        row["_hybrid_score"] = round(entry["score"], SCORE_PRECISION)
        output.append(row)
    return output


class VaultSearcher:
    """
    Busca semântica no vault com reranking.

    Usa ModelManager (singleton) para compartilhar modelos com o indexer.
    Cache de embeddings de query evita recomputar queries repetidas.

    Uso:
        searcher = VaultSearcher()
        results = searcher.search("como funciona X?")
        results = searcher.search_hybrid("X keyword", top_k=5)
        results = searcher.search_by_folder("query", "pasta/sub")
    """

    def __init__(self):
        self._models = ModelManager()
        self._db: DBConnection | None = None
        self._table: Table | None = None
        # Cache LRU de embeddings de query
        self._embedding_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._embedding_cache_lock = threading.Lock()
        self._cache_hits = 0
        self._cache_misses = 0
        # Prewarm status
        self._prewarm_status: PrewarmStatus = {
            "enabled": False,
            "status": "not_started",
            "indices_prewarmed": 0,
            "failed_indices": 0,
            "skipped_reason": None,
            "prewarmed_at": None,
        }

    def _connect_db(self) -> DBConnection:
        """Retorna conexão LanceDB."""
        if self._db is None:
            self._db = lancedb.connect(str(DATA_DIR))
        return self._db

    def _open_table(self) -> Table:
        """Retorna tabela LanceDB."""
        if self._table is None:
            db = self._connect_db()
            if LANCEDB_TABLE not in db.list_tables().tables:
                raise RuntimeError(INDEX_NOT_FOUND_ERROR)
            self._table = db.open_table(LANCEDB_TABLE)
        return self._table

    def invalidate_cache(self):
        """Invalida cache da tabela para forçar releitura após reindexação."""
        self._table = None

    def _query_cache_key(self, query: str) -> str:
        """Gera chave de cache para uma query (hash MD5)."""
        return hashlib.md5(query.encode("utf-8")).hexdigest()

    def _embed_query(self, query: str) -> list[float]:
        """
        Gera embedding do texto de busca usando encode_queries.

        Usa cache LRU para evitar recomputar queries repetidas.

        Parâmetros:
            query: texto da consulta

        Retorna:
            Vetor de embedding (1024 dims).
        """
        cache_key = self._query_cache_key(query)

        with self._embedding_cache_lock:
            if cache_key in self._embedding_cache:
                # Cache hit - mover para o fim (LRU)
                self._embedding_cache.move_to_end(cache_key)
                self._cache_hits += 1
                return self._embedding_cache[cache_key]

        # Cache miss - computar embedding (fora do lock para não bloquear)
        vecs = self._models.embed_queries([query])
        embedding = vecs[0]

        with self._embedding_cache_lock:
            # Double-check: outro thread pode ter inserido enquanto computávamos
            # Mesmo assim, contamos como miss pois fizemos o trabalho de computar
            # Nota: há desperdício de CPU em queries concorrentes idênticas,
            # mas isso é raro e a complexidade de um lock por query não compensa
            if cache_key in self._embedding_cache:
                self._embedding_cache.move_to_end(cache_key)
                self._cache_misses += 1  # Trabalho foi feito, conta como miss
                return self._embedding_cache[cache_key]

            # Eviction se necessário
            while len(self._embedding_cache) >= QUERY_EMBEDDING_CACHE_SIZE:
                self._embedding_cache.popitem(last=False)

            self._embedding_cache[cache_key] = embedding
            self._cache_misses += 1

        return embedding

    def get_embedding_cache_stats(self) -> EmbeddingCacheStats:
        """Retorna estatísticas do cache de embeddings."""
        with self._embedding_cache_lock:
            total = self._cache_hits + self._cache_misses
            hit_rate = self._cache_hits / total if total > 0 else 0.0
            return {
                "size": len(self._embedding_cache),
                "max_size": QUERY_EMBEDDING_CACHE_SIZE,
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "hit_rate": round(hit_rate, 4),
            }

    def get_prewarm_status(self) -> PrewarmStatus:
        """Retorna status do prewarm de índices."""
        return self._prewarm_status.copy()

    def _check_memory_for_prewarm(self, estimated_size_bytes: int) -> tuple[bool, str]:
        """
        Verifica se há memória suficiente para prewarm.

        Regras:
        1. psutil deve estar disponível
        2. RAM disponível >= PREWARM_MIN_AVAILABLE_RAM (default: 2GB)
        3. Tamanho estimado do índice < PREWARM_MAX_RAM_PERCENT da RAM disponível

        Parâmetros:
            estimated_size_bytes: tamanho estimado do índice em bytes

        Retorna:
            (can_prewarm, reason_code) - True se pode, False com código estável
        """
        if not PSUTIL_AVAILABLE:
            return False, "dependency_unavailable"

        try:
            mem = psutil.virtual_memory()
            available = mem.available

            # Verificar RAM mínima disponível
            if available < PREWARM_MIN_AVAILABLE_RAM:
                return False, "insufficient_memory"

            # Verificar se índice cabe no percentual permitido
            max_allowed = int(available * PREWARM_MAX_RAM_PERCENT)
            if estimated_size_bytes > max_allowed:
                return False, "estimated_index_too_large"

            return True, "ready"

        except Exception as e:
            logger.warning(
                "prewarm_memory_check_failed",
                extra={"error_type": type(e).__name__},
            )
            return False, "memory_check_failed"

    def try_prewarm(self, force: bool = False) -> PrewarmStatus:
        """
        Tenta fazer prewarm dos índices do LanceDB.

        Prewarm carrega os índices na RAM, reduzindo latência de queries.
        Verifica automaticamente se há memória suficiente antes de carregar.

        Parâmetros:
            force: se True, ignora verificação de memória (use com cuidado)

        Retorna:
            Dict com status do prewarm:
            - enabled: bool - se prewarm foi ativado
            - status: str - código do estado final
            - indices_prewarmed: int - quantidade carregada
            - failed_indices: int - quantidade que falhou
            - skipped_reason: str | None - código estável do motivo
            - prewarmed_at: str | None - timestamp do prewarm
            - duration_ms: float | None - tempo de prewarm
        """
        if not PREWARM_ENABLED and not force:
            self._prewarm_status = {
                "enabled": False,
                "status": "skipped",
                "indices_prewarmed": 0,
                "failed_indices": 0,
                "skipped_reason": "disabled",
                "prewarmed_at": None,
            }
            logger.info(
                "prewarm_skipped",
                extra={"reason_code": "disabled"},
            )
            return self._prewarm_status

        try:
            table = self._open_table()
        except RuntimeError as e:
            self._prewarm_status = {
                "enabled": False,
                "status": "skipped",
                "indices_prewarmed": 0,
                "failed_indices": 0,
                "skipped_reason": "index_unavailable",
                "prewarmed_at": None,
            }
            logger.warning(
                "prewarm_skipped",
                extra={"error_type": type(e).__name__},
            )
            return self._prewarm_status

        # Obter lista de índices
        indices = table.list_indices()
        if not indices:
            self._prewarm_status = {
                "enabled": False,
                "status": "skipped",
                "indices_prewarmed": 0,
                "failed_indices": 0,
                "skipped_reason": "no_indices",
                "prewarmed_at": None,
            }
            logger.info(
                "prewarm_skipped",
                extra={"reason_code": "no_indices"},
            )
            return self._prewarm_status

        # Estimar tamanho do índice
        row_count = None
        try:
            row_count = table.count_rows()
            estimated_size = row_count * PREWARM_BYTES_PER_CHUNK
        except Exception:
            estimated_size = 0

        # Verificar memória (skip se force=True)
        if not force:
            can_prewarm, reason = self._check_memory_for_prewarm(estimated_size)
            if not can_prewarm:
                self._prewarm_status = {
                    "enabled": False,
                    "status": "skipped",
                    "indices_prewarmed": 0,
                    "failed_indices": 0,
                    "skipped_reason": reason,
                    "prewarmed_at": None,
                }
                logger.info(
                    "prewarm_skipped",
                    extra={"reason_code": reason},
                )
                return self._prewarm_status

        # Fazer prewarm de cada índice
        start_time = time.time()
        prewarmed_count = 0
        failed_count = 0

        for idx in indices:
            idx_name = idx.name if hasattr(idx, "name") else str(idx)
            try:
                table.prewarm_index(idx_name)
                prewarmed_count += 1
                logger.debug("prewarm_index_completed")
            except Exception as e:
                failed_count += 1
                logger.warning(
                    "prewarm_index_failed",
                    extra={"error_type": type(e).__name__},
                )

        duration_ms = (time.time() - start_time) * 1000
        if prewarmed_count == 0:
            status = "failed"
            skipped_reason = "all_indices_failed"
        elif failed_count:
            status = "partial"
            skipped_reason = None
        else:
            status = "completed"
            skipped_reason = None

        self._prewarm_status = {
            "enabled": prewarmed_count > 0,
            "status": status,
            "indices_prewarmed": prewarmed_count,
            "failed_indices": failed_count,
            "skipped_reason": skipped_reason,
            "prewarmed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "duration_ms": round(duration_ms, 1),
            "row_count": row_count,
        }

        if prewarmed_count:
            logger.info(
                "prewarm_completed",
                extra={
                    "indices_prewarmed": prewarmed_count,
                    "failed_indices": failed_count,
                    "duration_ms": round(duration_ms, 1),
                },
            )
        else:
            logger.warning(
                "prewarm_failed",
                extra={"failed_indices": failed_count},
            )

        return self._prewarm_status

    def _vector_search(
        self, query_vec: list[float], candidates: int, where: str | None = None
    ) -> list[SearchRow]:
        """
        Executa busca vetorial no LanceDB com filtro opcional.

        Parâmetros:
            query_vec: vetor de embedding da query
            candidates: número de candidatos a retornar
            where: cláusula WHERE SQL opcional

        Retorna:
            Lista de dicts com campos de SEARCH_COLUMNS.
        """
        table = self._open_table()
        builder = (
            table.search(query_vec)
            .distance_type(get_vector_index_distance_type())
            .select(SEARCH_COLUMNS)
            .limit(candidates)
        )
        if where:
            builder = builder.where(where)
        return builder.to_list()

    def _rerank(self, query: str, results: list[SearchRow], top_k: int) -> list[SearchRow]:
        """
        Reordena resultados usando cross-encoder para maior precisão.

        Não muta os dicts de entrada — cria cópias com score adicionado.

        Parâmetros:
            query: texto da consulta original
            results: lista de dicts com campo 'text'
            top_k: quantos resultados finais retornar

        Retorna:
            Nova lista reordenada e truncada com score atualizado.
        """
        if not results:
            return []

        rerank_pool_size = _compute_rerank_pool_size(top_k, len(results))
        rerank_candidates = results[:rerank_pool_size]

        texts = [r["text"] for r in rerank_candidates]
        scores = self._models.rerank(query, texts)

        scored: list[SearchRow] = []
        for result, score in zip(rerank_candidates, scores, strict=True):
            entry: SearchRow = result.copy()  # cópia para não mutar input
            entry["rerank_score"] = round(score, SCORE_PRECISION)
            scored.append(entry)

        ranked = sorted(scored, key=lambda x: x["rerank_score"], reverse=True)
        return ranked[:top_k]

    def _format_results(self, rows: list[SearchRow]) -> list[SearchResult]:
        """
        Formata resultados do LanceDB para retorno padronizado.

        Delega para format_search_results, que normaliza e retorna
        apenas os campos públicos do contrato de busca.
        """
        return format_search_results(rows)

    def _filter_excluded(self, results: list[SearchRow], exclude: list[str]) -> list[SearchRow]:
        """
        Remove resultados que contêm termos excluídos.

        Busca case-insensitive no campo 'text'.

        Parâmetros:
            results: lista de resultados
            exclude: termos para excluir

        Retorna:
            Lista filtrada sem resultados que contêm termos excluídos.
        """
        if not exclude:
            return results

        exclude_lower = [term.lower() for term in exclude]

        filtered: list[SearchRow] = []
        for result in results:
            text_lower = result.get("text", "").lower()
            if not any(term in text_lower for term in exclude_lower):
                filtered.append(result)

        return filtered

    def search(self, query: str, top_k: int = SEARCH_TOP_K) -> list[SearchResult]:
        """
        Busca semântica com reranking (busca principal).

        Parâmetros:
            query: texto de busca (qualquer idioma)
            top_k: quantidade de resultados finais

        Retorna:
            Lista de resultados ordenados por relevância.
        """
        query_vec = self._embed_query(query)
        candidates = _compute_candidates(top_k)

        raw = self._vector_search(query_vec, candidates)

        if not raw:
            return []

        reranked = self._rerank(query, raw, top_k)
        return self._format_results(reranked)

    def search_hybrid(self, query: str, top_k: int = SEARCH_TOP_K) -> list[SearchResult]:
        """
        Busca híbrida: combina semântica vetorial com busca por keyword.

        Parâmetros:
            query: texto de busca
            top_k: quantidade de resultados finais

        Retorna:
            Lista de resultados ordenados por relevância.
        """
        table = self._open_table()
        query_vec = self._embed_query(query)
        candidates = _compute_candidates(top_k)

        vector_results = self._vector_search(query_vec, candidates)

        fts_results: list[SearchRow] = []
        try:
            fts_results = (
                table.search(query, query_type="fts")
                .select(FTS_SEARCH_COLUMNS)
                .limit(candidates)
                .to_list()
            )
        except Exception as e:
            logger.warning(
                "hybrid_fts_unavailable",
                extra={"error_type": type(e).__name__},
            )

        merged = _fuse_hybrid_results(vector_results, fts_results, candidates)

        if not merged:
            return []

        reranked = self._rerank(query, merged, top_k)
        return self._format_results(reranked)

    def search_by_folder(
        self, query: str, folder: str, top_k: int = SEARCH_TOP_K
    ) -> list[SearchResult]:
        """
        Busca semântica filtrada por pasta do vault.

        Usa filtro com boundary exata para evitar prefix collision:
        folder='proj' casa 'proj' e 'proj/sub', mas NÃO 'project'.

        Parâmetros:
            query: texto de busca
            folder: pasta para filtrar (ex: 'projetos/web')
            top_k: quantidade de resultados finais

        Retorna:
            Lista de resultados apenas da pasta especificada.
        """
        query_vec = self._embed_query(query)
        candidates = _compute_candidates(top_k)

        # Escapar para ambos os contextos: SQL string (=) e LIKE pattern
        escaped_sql = escape_sql_string(folder)
        escaped_like = escape_like_pattern(folder)
        # Boundary exata: match 'proj' ou 'proj/...' mas não 'project'
        where_clause = f"(folder = '{escaped_sql}' OR folder LIKE '{escaped_like}/%')"
        raw = self._vector_search(query_vec, candidates, where=where_clause)

        if not raw:
            return []

        reranked = self._rerank(query, raw, top_k)
        return self._format_results(reranked)

    def _validate_iso_date(self, date_str: str) -> str | None:
        """
        Valida formato de data ISO (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS).

        Parâmetros:
            date_str: string de data para validar

        Retorna:
            Data validada ou None se inválida.
        """
        from datetime import datetime

        if not date_str or not isinstance(date_str, str):
            return None

        date_str = date_str.strip()[:19]  # Truncar para max ISO datetime

        try:
            if "T" in date_str:
                datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
            else:
                datetime.strptime(date_str, "%Y-%m-%d")
            return date_str
        except ValueError:
            logger.warning("invalid_iso_date_ignored")
            return None

    def _build_date_filter(self, date_range: str | Mapping[str, str]) -> str | None:
        """
        Constrói filtro de data para busca avançada.

        Parâmetros:
            date_range: "today", "week", "month", "year" ou
                       {"from": "2026-01-01", "to": "2026-02-01"}

        Retorna:
            Cláusula WHERE para filtro de data ou None.
        """
        from datetime import datetime, timedelta

        now = datetime.now()

        if isinstance(date_range, str):
            if date_range == "today":
                cutoff = now - timedelta(days=1)
            elif date_range == "week":
                cutoff = now - timedelta(days=7)
            elif date_range == "month":
                cutoff = now - timedelta(days=30)
            elif date_range == "year":
                cutoff = now - timedelta(days=365)
            else:
                return None

            cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
            return f"modified_at >= '{cutoff_str}'"

        elif isinstance(date_range, Mapping):
            conditions = []
            if "from" in date_range:
                date_from = self._validate_iso_date(date_range["from"])
                if date_from:
                    date_from = escape_sql_string(date_from)
                    conditions.append(f"modified_at >= '{date_from}'")
            if "to" in date_range:
                date_to = self._validate_iso_date(date_range["to"])
                if date_to:
                    date_to = escape_sql_string(date_to)
                    conditions.append(f"modified_at <= '{date_to}'")
            if conditions:
                return " AND ".join(conditions)

        return None

    def search_advanced(
        self,
        query: str,
        top_k: int = SEARCH_TOP_K,
        tags: list[str] | None = None,
        folder: str | None = None,
        extension: str | None = None,
        date_range: str | Mapping[str, str] | None = None,
        status: str | None = None,
        note_type: str | None = None,
        category: str | None = None,
        project: str | None = None,
        exclude: list[str] | None = None,
        highlight: bool = False,
        highlight_start: str = "**",
        highlight_end: str = "**",
    ) -> list[SearchResult]:
        """
        Busca semântica com filtros avançados (faceted search).

        Combina busca vetorial com filtros estruturados para
        resultados mais precisos em vaults grandes.

        Parâmetros:
            query: texto de busca (qualquer idioma)
            top_k: quantidade de resultados finais
            tags: lista de tags para filtrar (OR entre elas)
            folder: pasta para filtrar (inclui subpastas)
            extension: extensão do arquivo (".md", ".pdf", ".canvas")
            date_range: período - "today", "week", "month", "year" ou
                       {"from": "2026-01-01", "to": "2026-02-01"}
            status: status da nota (draft, review, published, archived)
            note_type: tipo da nota (daily, weekly, monthly, yearly, meeting, idea, task)
            category: categoria (work, personal, reference, project)
            project: nome do projeto associado
            exclude: lista de termos para EXCLUIR dos resultados
            highlight: se True, destaca termos da query no texto
            highlight_start: marcador de início (default: **)
            highlight_end: marcador de fim (default: **)

        Retorna:
            Lista de resultados filtrados ordenados por relevância.

        Exemplos:
            search_advanced("python", tags=["tutorial"])
            search_advanced("API", date_range="week", extension=".md")
            search_advanced("projeto", folder="trabalho", tags=["urgente"])
            search_advanced("reunião", note_type="meeting", status="published")
            search_advanced("feature", project="vault-search")
            search_advanced("python", exclude=["django", "flask"])
            search_advanced("API", highlight=True)
        """
        query_vec = self._embed_query(query)
        candidates = _compute_candidates(top_k)

        # Construir cláusulas WHERE
        conditions = []

        # Filtro por tags (OR entre elas)
        if tags:
            tag_conditions = []
            for tag in tags:
                escaped_tag = escape_like_pattern(tag)
                tag_conditions.append(f"tags LIKE '%{escaped_tag}%'")
            if tag_conditions:
                conditions.append(f"({' OR '.join(tag_conditions)})")

        # Filtro por pasta (exato ou subpastas)
        if folder:
            escaped_sql = escape_sql_string(folder)
            escaped_like = escape_like_pattern(folder)
            conditions.append(f"(folder = '{escaped_sql}' OR folder LIKE '{escaped_like}/%')")

        # Filtro por extensão
        if extension:
            ext = extension.lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            escaped_ext = escape_like_pattern(ext)
            conditions.append(f"note_path LIKE '%{escaped_ext}'")

        # Filtro por data
        date_filter = self._build_date_filter(date_range) if date_range else None
        if date_filter:
            conditions.append(date_filter)

        # Filtros de campos do frontmatter
        if status:
            escaped = escape_sql_string(status.lower().strip())
            conditions.append(f"status = '{escaped}'")

        if note_type:
            escaped = escape_sql_string(note_type.lower().strip())
            conditions.append(f"note_type = '{escaped}'")

        if category:
            escaped = escape_like_pattern(category.lower().strip())
            conditions.append(f"category LIKE '%{escaped}%'")

        if project:
            escaped = escape_sql_string(project.strip())
            conditions.append(f"project = '{escaped}'")

        # Combinar condições
        where_clause = " AND ".join(conditions) if conditions else None

        # Buscar mais candidatos se temos exclusão (alguns serão removidos)
        search_candidates = candidates
        if exclude:
            search_candidates = min(candidates * 2, SEARCH_CANDIDATES_MAX)

        raw = self._vector_search(query_vec, search_candidates, where=where_clause)

        if not raw:
            return []

        # Aplicar exclusão ANTES do reranking (evita reranquear itens que serão removidos)
        if exclude:
            raw = self._filter_excluded(raw, exclude)
            if not raw:
                return []

        reranked = self._rerank(query, raw, top_k)

        # Aplicar highlight DEPOIS do reranking (apenas nos resultados finais)
        if highlight:
            reranked = apply_highlight(reranked, query, True, highlight_start, highlight_end)

        return self._format_results(reranked)

    def find_similar_notes(self, path: str, top_k: int = 5) -> list[SimilarNoteResult]:
        """
        Encontra notas similares a uma nota específica.

        Calcula o embedding médio de todos os chunks da nota e
        busca outras notas semanticamente similares.

        Parâmetros:
            path: caminho relativo da nota no vault
            top_k: quantidade de notas similares a retornar

        Retorna:
            Lista de notas similares com score de similaridade.

        Raises:
            ValueError: se a nota não foi encontrada no índice.
        """
        import numpy as np

        table = self._open_table()

        # Buscar chunks da nota de referência
        escaped_path = escape_sql_string(path)
        note_chunks = (
            table.search()
            .where(f"note_path = '{escaped_path}'")
            .select(["note_path", "vector"])
            .limit(100)
            .to_list()
        )

        if not note_chunks:
            raise ValueError(f"Nota '{path}' não encontrada no índice")

        # Calcular embedding médio da nota
        vectors = [c["vector"] for c in note_chunks]
        avg_vector = np.mean(vectors, axis=0).tolist()

        # Buscar notas similares, excluindo a própria nota
        # Buscar mais candidatos para ter margem após filtro
        candidates = _compute_candidates(top_k * 3)
        results = table.search(avg_vector).select(SEARCH_COLUMNS).limit(candidates).to_list()

        # Agrupar por nota e pegar melhor score de cada
        seen_notes: dict[str, _SimilarCandidate] = {}
        for r in results:
            note_path = r.get("note_path", "")
            if note_path == path:
                continue  # Excluir a própria nota

            if note_path not in seen_notes:
                seen_notes[note_path] = {
                    "note_path": note_path,
                    "note_title": r.get("note_title", ""),
                    "folder": r.get("folder", ""),
                    "tags": r.get("tags", ""),
                    "_distance": r.get("_distance", 1.0),
                }
            else:
                # Manter o menor distance (mais similar)
                if r.get("_distance", 1.0) < seen_notes[note_path]["_distance"]:
                    seen_notes[note_path]["_distance"] = r.get("_distance", 1.0)

        # Ordenar por similaridade (menor distance = mais similar)
        sorted_notes = sorted(seen_notes.values(), key=lambda x: x["_distance"])

        # Converter distance para score e limitar
        result: list[SimilarNoteResult] = []
        for note in sorted_notes[:top_k]:
            score = round(1 / (1 + note["_distance"]), SCORE_PRECISION)
            result.append(
                {
                    "note_path": note["note_path"],
                    "note_title": note["note_title"],
                    "folder": note["folder"],
                    "tags": note["tags"],
                    "similarity_score": score,
                }
            )

        return result

    def search_duplicates(
        self,
        threshold: float = 0.90,
        max_notes: int = 500,
        folder: str | None = None,
    ) -> list[DuplicateGroup]:
        """
        Encontra grupos de notas duplicadas ou muito similares no vault.

        Varre o vault calculando similaridade entre notas e agrupa
        aquelas que excedem o threshold em clusters de duplicatas.

        Parâmetros:
            threshold: similaridade mínima para considerar duplicata (0.0-1.0)
                      0.90 = 90% similar (default, captura duplicatas óbvias)
                      0.80 = 80% similar (mais permissivo)
                      0.95 = 95% similar (apenas duplicatas quase idênticas)
            max_notes: máximo de notas a processar (proteção de performance)
                      Limitado a 2000 para evitar uso excessivo de RAM
            folder: opcional, restringir busca a uma pasta específica

        Retorna:
            Lista de grupos de duplicatas, cada grupo com:
            - notes: lista de notas no grupo
            - similarity: score médio de similaridade do grupo

        Observação:
            Operação computacionalmente cara. Para vaults grandes,
            considere restringir por folder ou aumentar threshold.
        """
        import numpy as np

        # Validar max_notes para evitar OOM (50k chunks = ~200MB RAM)
        MAX_SAFE_NOTES = 2000
        if max_notes > MAX_SAFE_NOTES:
            logger.warning(
                f"search_duplicates: max_notes={max_notes} muito alto, "
                f"limitando a {MAX_SAFE_NOTES} para evitar uso excessivo de RAM"
            )
            max_notes = MAX_SAFE_NOTES

        table = self._open_table()

        # Buscar todas as notas únicas
        query = table.search().select(["note_path", "note_title", "folder", "vector"])

        if folder:
            escaped = escape_sql_string(folder)
            # Buscar pasta exata ou subpastas
            query = query.where(
                f"folder = '{escaped}' OR folder LIKE '{escape_like_pattern(folder)}/%'"
            )

        all_chunks = query.limit(max_notes * 100).to_list()  # ~100 chunks por nota

        if not all_chunks:
            return []

        # Agrupar chunks por nota e calcular embedding médio
        note_embeddings: dict[str, _NoteEmbedding] = {}
        for chunk in all_chunks:
            path = chunk.get("note_path", "")
            if not path:
                continue

            if path not in note_embeddings:
                note_embeddings[path] = {
                    "note_path": path,
                    "note_title": chunk.get("note_title", ""),
                    "folder": chunk.get("folder", ""),
                    "vectors": [],
                }
            note_embeddings[path]["vectors"].append(chunk.get("vector", []))

        # Limitar número de notas
        if len(note_embeddings) > max_notes:
            # Pegar as primeiras max_notes (por ordem de descoberta)
            note_embeddings = dict(list(note_embeddings.items())[:max_notes])

        # Calcular embedding médio de cada nota
        for note in note_embeddings.values():
            vectors = note["vectors"]
            if vectors:
                note["avg_vector"] = np.mean(vectors, axis=0).tolist()
            else:
                note["avg_vector"] = None

        # Encontrar duplicatas usando numpy (O(n*d) em vez de n queries LanceDB)
        notes_list = [n for n in note_embeddings.values() if n.get("avg_vector") is not None]

        if len(notes_list) < 2:
            return []

        # Construir matriz de vetores e normalizar para similaridade coseno
        vector_matrix = np.array([n["avg_vector"] for n in notes_list])
        norms = np.linalg.norm(vector_matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1  # Evitar divisão por zero
        normalized = vector_matrix / norms

        # Calcular matriz de similaridade (dot product de vetores normalizados = coseno)
        # Apenas triângulo superior para evitar duplicatas (i,j) e (j,i)
        similarity_matrix = np.dot(normalized, normalized.T)

        # Encontrar pares acima do threshold
        processed: set[int] = set()
        duplicate_groups: list[DuplicateGroup] = []

        for i in range(len(notes_list)):
            if i in processed:
                continue

            # Encontrar todas as notas similares a esta
            similar_indices = []
            for j in range(i + 1, len(notes_list)):
                if j in processed:
                    continue
                score = similarity_matrix[i, j]
                if score >= threshold:
                    similar_indices.append((j, score))

            if similar_indices:
                # Criar grupo de duplicatas
                note = notes_list[i]
                group_notes: list[DuplicateNoteResult] = [
                    {
                        "note_path": note["note_path"],
                        "note_title": note["note_title"],
                        "folder": note["folder"],
                    }
                ]
                scores = []

                for j, score in similar_indices:
                    dup = notes_list[j]
                    group_notes.append(
                        {
                            "note_path": dup["note_path"],
                            "note_title": dup["note_title"],
                            "folder": dup["folder"],
                        }
                    )
                    scores.append(score)
                    processed.add(j)

                avg_similarity = round(sum(scores) / len(scores), SCORE_PRECISION) if scores else 0
                duplicate_groups.append(
                    {
                        "notes": group_notes,
                        "count": len(group_notes),
                        "avg_similarity": avg_similarity,
                    }
                )

            processed.add(i)

        # Ordenar grupos por similaridade (mais similares primeiro)
        duplicate_groups.sort(key=lambda g: g["avg_similarity"], reverse=True)

        return duplicate_groups


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "teste"
    searcher = VaultSearcher()

    print(f"\nBuscando: '{query}'")
    results = searcher.search(query)

    if not results:
        print("Nenhum resultado encontrado.")
    else:
        for i, r in enumerate(results, 1):
            print(f"\n--- Resultado {i} (score: {r.get('score', 'N/A')}) ---")
            print(f"Nota: {r['note_path']}")
            if r["headers"]:
                print(f"Seção: {r['headers']}")
            print(f"Texto: {r['text'][:200]}...")
