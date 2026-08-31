"""
Semantic search engine for Obsidian vaults.

Responsibilities:
- Semantic vector search in LanceDB
- Hybrid semantic and FTS keyword search
- Cross-encoder reranking with MiniLM-L-6-v2
- Folder filtering
- Query-embedding cache
- Index prewarming for lower latency
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

# Maximum query-embedding cache size.
QUERY_EMBEDDING_CACHE_SIZE = 1000


class EmbeddingCacheStats(TypedDict):
    """Public counters for the embedding cache."""

    size: int
    max_size: int
    hits: int
    misses: int
    hit_rate: float


class PrewarmStatus(TypedDict, total=False):
    """Detailed index-prewarm state."""

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
    Calculate the vector-search candidate count.

    Ensure the reranker receives enough candidates even when ``top_k``
    exceeds ``SEARCH_CANDIDATES``.

    Parameters:
        top_k: Requested final result count.

    Returns:
        Candidate count between ``SEARCH_CANDIDATES`` and ``SEARCH_CANDIDATES_MAX``.
    """
    return min(
        max(SEARCH_CANDIDATES, top_k * SEARCH_CANDIDATES_MULTIPLIER),
        SEARCH_CANDIDATES_MAX,
    )


def _compute_rerank_pool_size(top_k: int, candidate_count: int) -> int:
    """
    Calculate how many candidates to send to the cross-encoder.

    Strategy:
    - Never fewer than ``top_k``
    - Typical window of ``top_k * RERANK_CANDIDATES_MULTIPLIER``
    - Hard cap through ``RERANK_CANDIDATES_MAX``

    Parameters:
        top_k: Requested final result count.
        candidate_count: Available candidate count.

    Returns:
        Number of candidates to rerank.
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
    """Combine vector and lexical rankings with Reciprocal Rank Fusion."""
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
    Search the vault semantically with reranking.

    Share models with the indexer through the ``ModelManager`` singleton and
    cache query embeddings to avoid repeated computation.

    Usage:
        searcher = VaultSearcher()
        results = searcher.search("how does X work?")
        results = searcher.search_hybrid("X keyword", top_k=5)
        results = searcher.search_by_folder("query", "folder/subfolder")
    """

    def __init__(self):
        self._models = ModelManager()
        self._db: DBConnection | None = None
        self._table: Table | None = None
        # LRU cache of query embeddings.
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
        """Return the LanceDB connection."""
        if self._db is None:
            self._db = lancedb.connect(str(DATA_DIR))
        return self._db

    def _open_table(self) -> Table:
        """Return the LanceDB table."""
        if self._table is None:
            db = self._connect_db()
            if LANCEDB_TABLE not in db.list_tables().tables:
                raise RuntimeError(INDEX_NOT_FOUND_ERROR)
            self._table = db.open_table(LANCEDB_TABLE)
        return self._table

    def invalidate_cache(self):
        """Invalidate the table cache so reindexing changes are reloaded."""
        self._table = None

    def _query_cache_key(self, query: str) -> str:
        """Generate an MD5 cache key for a query."""
        return hashlib.md5(query.encode("utf-8")).hexdigest()

    def _embed_query(self, query: str) -> list[float]:
        """
        Generate a search-text embedding through ``encode_queries``.

        Use an LRU cache to avoid recomputing repeated queries.

        Parameters:
            query: search text

        Returns:
            Embedding vector.
        """
        cache_key = self._query_cache_key(query)

        with self._embedding_cache_lock:
            if cache_key in self._embedding_cache:
                # Move a cache hit to the end of the LRU.
                self._embedding_cache.move_to_end(cache_key)
                self._cache_hits += 1
                return self._embedding_cache[cache_key]

        # Compute a cache miss outside the lock.
        vecs = self._models.embed_queries([query])
        embedding = vecs[0]

        with self._embedding_cache_lock:
            # Another thread may have inserted the same value while this thread
            # computed it. Count the work as a miss; per-query locks add more
            # complexity than this rare duplicate computation warrants.
            if cache_key in self._embedding_cache:
                self._embedding_cache.move_to_end(cache_key)
                self._cache_misses += 1  # Computed work counts as a miss.
                return self._embedding_cache[cache_key]

            # Evict entries when necessary.
            while len(self._embedding_cache) >= QUERY_EMBEDDING_CACHE_SIZE:
                self._embedding_cache.popitem(last=False)

            self._embedding_cache[cache_key] = embedding
            self._cache_misses += 1

        return embedding

    def get_embedding_cache_stats(self) -> EmbeddingCacheStats:
        """Return embedding-cache statistics."""
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
        """Return index-prewarm status."""
        return self._prewarm_status.copy()

    def _check_memory_for_prewarm(self, estimated_size_bytes: int) -> tuple[bool, str]:
        """
        Check whether enough memory is available for prewarming.

        Rules:
        1. psutil must be available.
        2. Available RAM must meet ``PREWARM_MIN_AVAILABLE_RAM``.
        3. Estimated index size must remain below the configured RAM percentage.

        Parameters:
            estimated_size_bytes: Estimated index size in bytes.

        Returns:
            A ``(can_prewarm, reason_code)`` tuple.
        """
        if not PSUTIL_AVAILABLE:
            return False, "dependency_unavailable"

        try:
            mem = psutil.virtual_memory()
            available = mem.available

            # Check the minimum available RAM.
            if available < PREWARM_MIN_AVAILABLE_RAM:
                return False, "insufficient_memory"

            # Check whether the index fits within the allowed percentage.
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
        Attempt to prewarm LanceDB indexes.

        Load indexes into RAM to reduce query latency after checking capacity.

        Parameters:
            force: Skip the memory check when true.

        Returns:
            Prewarm status with enabled state, outcome code, loaded and failed
            index counts, a stable skip reason, and a timestamp.
            - duration_ms: float | None - prewarm duration
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

        # Read the index list.
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

        # Estimate index size.
        row_count = None
        try:
            row_count = table.count_rows()
            estimated_size = row_count * PREWARM_BYTES_PER_CHUNK
        except Exception:
            estimated_size = 0

        # Check memory unless force is true.
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

        # Prewarm each index.
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
        Run vector search in LanceDB with an optional filter.

        Parameters:
            query_vec: Query embedding vector.
            candidates: Candidate count.
            where: Optional SQL WHERE clause.

        Returns:
            Rows containing ``SEARCH_COLUMNS`` fields.
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
        Rerank results with a cross-encoder.

        Copy input dictionaries instead of mutating them.

        Parameters:
            query: Original query text.
            results: Rows containing a ``text`` field.
            top_k: Final result count.

        Returns:
            A reranked, truncated list with updated scores.
        """
        if not results:
            return []

        rerank_pool_size = _compute_rerank_pool_size(top_k, len(results))
        rerank_candidates = results[:rerank_pool_size]

        texts = [r["text"] for r in rerank_candidates]
        scores = self._models.rerank(query, texts)

        scored: list[SearchRow] = []
        for result, score in zip(rerank_candidates, scores, strict=True):
            entry: SearchRow = result.copy()  # Copy to avoid mutating input
            entry["rerank_score"] = round(score, SCORE_PRECISION)
            scored.append(entry)

        ranked = sorted(scored, key=lambda x: x["rerank_score"], reverse=True)
        return ranked[:top_k]

    def _format_results(self, rows: list[SearchRow]) -> list[SearchResult]:
        """
        Format LanceDB results for the public response contract.

        Delegate to ``format_search_results``, which normalizes and returns
        only public fields.
        """
        return format_search_results(rows)

    def _filter_excluded(self, results: list[SearchRow], exclude: list[str]) -> list[SearchRow]:
        """
        Remove results containing excluded terms.

        Search the ``text`` field case-insensitively.

        Parameters:
            results: Search results.
            exclude: Terms to exclude.

        Returns:
            Results that do not contain excluded terms.
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
        Run the primary semantic search with reranking.

        Parameters:
            query: Search text in any language.
            top_k: Final result count.

        Returns:
            Results ordered by relevance.
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
        Combine semantic vector search with keyword search.

        Parameters:
            query: Search text.
            top_k: Final result count.

        Returns:
            Results ordered by relevance.
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
        Run semantic search within a vault folder.

        Use exact boundaries to avoid prefix collisions: ``proj`` matches
        ``proj`` and ``proj/sub``, but not ``project``.

        Parameters:
            query: Search text.
            folder: Folder filter, for example ``projects/web``.
            top_k: Final result count.

        Returns:
            Results from the specified folder.
        """
        query_vec = self._embed_query(query)
        candidates = _compute_candidates(top_k)

        # Escape for both SQL equality and LIKE pattern contexts.
        escaped_sql = escape_sql_string(folder)
        escaped_like = escape_like_pattern(folder)
        # Match ``proj`` and ``proj/...`` without matching ``project``.
        where_clause = f"(folder = '{escaped_sql}' OR folder LIKE '{escaped_like}/%')"
        raw = self._vector_search(query_vec, candidates, where=where_clause)

        if not raw:
            return []

        reranked = self._rerank(query, raw, top_k)
        return self._format_results(reranked)

    def _validate_iso_date(self, date_str: str) -> str | None:
        """
        Validate ISO date format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS).

        Parameters:
            date_str: Date string to validate.

        Returns:
            Validated date, or ``None`` when invalid.
        """
        from datetime import datetime

        if not date_str or not isinstance(date_str, str):
            return None

        date_str = date_str.strip()[:19]  # Truncate to the maximum ISO datetime length

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
        Build a date filter for advanced search.

        Parameters:
            date_range: "today", "week", "month", "year", or
                       {"from": "2026-01-01", "to": "2026-02-01"}

        Returns:
            A date-filter WHERE clause, or ``None``.
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
        Run semantic search with advanced facets.

        Combine vector search with structured filters for large vaults.

        Parameters:
            query: Search text in any language.
            top_k: Final result count.
            tags: Tag filters combined with OR.
            folder: Folder filter including subfolders.
            extension: File extension such as .md, .pdf, or .canvas.
            date_range: ``today``, ``week``, ``month``, ``year``, or
                       {"from": "2026-01-01", "to": "2026-02-01"}
            status: Note status.
            note_type: Note type.
            category: Category.
            project: Associated project name.
            exclude: Terms to exclude from results.
            highlight: Whether to highlight query terms.
            highlight_start: Opening highlight marker.
            highlight_end: Closing highlight marker.

        Returns:
            Filtered results ordered by relevance.

        Examples:
            search_advanced("python", tags=["tutorial"])
            search_advanced("API", date_range="week", extension=".md")
            search_advanced("project", folder="work", tags=["urgent"])
            search_advanced("meeting", note_type="meeting", status="published")
            search_advanced("feature", project="vault-search")
            search_advanced("python", exclude=["django", "flask"])
            search_advanced("API", highlight=True)
        """
        query_vec = self._embed_query(query)
        candidates = _compute_candidates(top_k)

        # Build WHERE clauses.
        conditions = []

        # Combine tag filters with OR.
        if tags:
            tag_conditions = []
            for tag in tags:
                escaped_tag = escape_like_pattern(tag)
                tag_conditions.append(f"tags LIKE '%{escaped_tag}%'")
            if tag_conditions:
                conditions.append(f"({' OR '.join(tag_conditions)})")

        # Match an exact folder or its subfolders.
        if folder:
            escaped_sql = escape_sql_string(folder)
            escaped_like = escape_like_pattern(folder)
            conditions.append(f"(folder = '{escaped_sql}' OR folder LIKE '{escaped_like}/%')")

        # Filter by extension.
        if extension:
            ext = extension.lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            escaped_ext = escape_like_pattern(ext)
            conditions.append(f"note_path LIKE '%{escaped_ext}'")

        # Filter by date.
        date_filter = self._build_date_filter(date_range) if date_range else None
        if date_filter:
            conditions.append(date_filter)

        # Frontmatter field filters.
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

        # Combine conditions.
        where_clause = " AND ".join(conditions) if conditions else None

        # Request extra candidates when exclusions may remove results.
        search_candidates = candidates
        if exclude:
            search_candidates = min(candidates * 2, SEARCH_CANDIDATES_MAX)

        raw = self._vector_search(query_vec, search_candidates, where=where_clause)

        if not raw:
            return []

        # Apply exclusions before reranking.
        if exclude:
            raw = self._filter_excluded(raw, exclude)
            if not raw:
                return []

        reranked = self._rerank(query, raw, top_k)

        # Apply highlighting only to final reranked results.
        if highlight:
            reranked = apply_highlight(reranked, query, True, highlight_start, highlight_end)

        return self._format_results(reranked)

    def find_similar_notes(self, path: str, top_k: int = 5) -> list[SimilarNoteResult]:
        """
        Find notes similar to one note.

        Average all note-chunk embeddings and search for semantically similar notes.

        Parameters:
            path: Vault-relative note path.
            top_k: Similar-note count.

        Returns:
            Similar notes with similarity scores.

        Raises:
            ValueError: When the note is absent from the index.
        """
        import numpy as np

        table = self._open_table()

        # Read chunks for the reference note.
        escaped_path = escape_sql_string(path)
        note_chunks = (
            table.search()
            .where(f"note_path = '{escaped_path}'")
            .select(["note_path", "vector"])
            .limit(100)
            .to_list()
        )

        if not note_chunks:
            raise ValueError(f"Note '{path}' was not found in the index")

        # Calculate the note's mean embedding.
        vectors = [c["vector"] for c in note_chunks]
        avg_vector = np.mean(vectors, axis=0).tolist()

        # Request extra candidates before excluding the reference note.
        candidates = _compute_candidates(top_k * 3)
        results = table.search(avg_vector).select(SEARCH_COLUMNS).limit(candidates).to_list()

        # Group by note and keep the best score.
        seen_notes: dict[str, _SimilarCandidate] = {}
        for r in results:
            note_path = r.get("note_path", "")
            if note_path == path:
                continue  # Exclude the reference note.

            if note_path not in seen_notes:
                seen_notes[note_path] = {
                    "note_path": note_path,
                    "note_title": r.get("note_title", ""),
                    "folder": r.get("folder", ""),
                    "tags": r.get("tags", ""),
                    "_distance": r.get("_distance", 1.0),
                }
            else:
                # Keep the smallest, most-similar distance.
                if r.get("_distance", 1.0) < seen_notes[note_path]["_distance"]:
                    seen_notes[note_path]["_distance"] = r.get("_distance", 1.0)

        # Sort by similarity, where a smaller distance is more similar.
        sorted_notes = sorted(seen_notes.values(), key=lambda x: x["_distance"])

        # Convert distances to scores and apply the limit.
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
        Find duplicate or highly similar note groups in the vault.

        Calculate note similarity and cluster notes above the threshold.

        Parameters:
            threshold: Minimum duplicate similarity from 0.0 to 1.0.
            max_notes: Maximum notes to process, capped at 2,000.
            folder: Optional folder restriction.

        Returns:
            Duplicate groups containing notes and average similarity.

        Note:
            This operation is computationally expensive. Restrict the folder
            or raise the threshold for large vaults.
        """
        import numpy as np

        # Cap max_notes to prevent excessive memory use.
        MAX_SAFE_NOTES = 2000
        if max_notes > MAX_SAFE_NOTES:
            logger.warning(
                f"search_duplicates: max_notes={max_notes} exceeds the safe limit; "
                f"using {MAX_SAFE_NOTES}"
            )
            max_notes = MAX_SAFE_NOTES

        table = self._open_table()

        # Read unique notes.
        query = table.search().select(["note_path", "note_title", "folder", "vector"])

        if folder:
            escaped = escape_sql_string(folder)
            # Match the exact folder or descendants.
            query = query.where(
                f"folder = '{escaped}' OR folder LIKE '{escape_like_pattern(folder)}/%'"
            )

        all_chunks = query.limit(max_notes * 100).to_list()  # Approximately 100 chunks per note

        if not all_chunks:
            return []

        # Group chunks by note and calculate mean embeddings.
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

        # Limit the number of notes.
        if len(note_embeddings) > max_notes:
            # Keep the first max_notes in discovery order.
            note_embeddings = dict(list(note_embeddings.items())[:max_notes])

        # Calculate each note's mean embedding.
        for note in note_embeddings.values():
            vectors = note["vectors"]
            if vectors:
                note["avg_vector"] = np.mean(vectors, axis=0).tolist()
            else:
                note["avg_vector"] = None

        # Compare note vectors in one NumPy operation instead of separate queries.
        notes_list = [n for n in note_embeddings.values() if n.get("avg_vector") is not None]

        if len(notes_list) < 2:
            return []

        # Build and normalize a vector matrix for cosine similarity.
        vector_matrix = np.array([n["avg_vector"] for n in notes_list])
        norms = np.linalg.norm(vector_matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1  # Avoid division by zero
        normalized = vector_matrix / norms

        # The dot product of normalized vectors is cosine similarity. Use only
        # the upper triangle to avoid duplicate pairs.
        similarity_matrix = np.dot(normalized, normalized.T)

        # Find pairs above the threshold.
        processed: set[int] = set()
        duplicate_groups: list[DuplicateGroup] = []

        for i in range(len(notes_list)):
            if i in processed:
                continue

            # Find every note similar to this one.
            similar_indices = []
            for j in range(i + 1, len(notes_list)):
                if j in processed:
                    continue
                score = similarity_matrix[i, j]
                if score >= threshold:
                    similar_indices.append((j, score))

            if similar_indices:
                # Create a duplicate group.
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

        # Sort groups by similarity, highest first.
        duplicate_groups.sort(key=lambda g: g["avg_similarity"], reverse=True)

        return duplicate_groups


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "test"
    searcher = VaultSearcher()

    print(f"\nSearching: '{query}'")
    results = searcher.search(query)

    if not results:
        print("No results found.")
    else:
        for i, r in enumerate(results, 1):
            print(f"\n--- Result {i} (score: {r.get('score', 'N/A')}) ---")
            print(f"Note: {r['note_path']}")
            if r["headers"]:
                print(f"Section: {r['headers']}")
            print(f"Text: {r['text'][:200]}...")
