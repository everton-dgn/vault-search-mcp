"""
MCP tools for search and indexing.
"""

import logging
import time
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from fastmcp import Context

from vault_search.config.search import (
    FOLDER_TREE_MAX_DEPTH,
    FOLDER_TREE_MAX_DEPTH_LIMIT,
    INDEXABLE_EXTENSIONS,
)
from vault_search.core.models import ModelManager
from vault_search.crud.cache import get_metadata_cache
from vault_search.crud.catalog import get_catalog
from vault_search.crud.validation import resolve_internal_path, resolve_path
from vault_search.server.errors import public_error, public_error_dict
from vault_search.server.helpers import (
    clamp_top_k,
    execute_search,
    log_query,
    truncate_query,
)
from vault_search.utils.links import normalize_link_target
from vault_search.utils.metrics import (
    check_cache_health,
    check_latency_health,
    get_metrics,
    reset_metrics,
)
from vault_search.utils.security import (
    escape_like_pattern,
    escape_sql_string,
    validate_relative_path,
)

logger = logging.getLogger("vault-search-mcp")

# Bound excluded terms to limit query construction work.
MAX_EXCLUDE_TERMS = 20

# Server start timestamp for uptime reporting.
_SERVER_START_TIME = time.time()


class BacklinkResult(TypedDict):
    """Deduplicated backlink returned to the client."""

    path: str
    title: str
    link_type: str
    link_target: str
    context: NotRequired[str]


class BrokenNoteCount(TypedDict):
    """Intermediate broken-link count for one note."""

    path: str
    title: str
    count: int


class BrokenLinkDetail(TypedDict):
    """Public details for one broken link."""

    target: str
    type: str
    context: str


class OrphanNote(TypedDict):
    """Orphan note retained during global pagination."""

    path: str
    title: str
    folder: str
    modified_at: str


class BacklinkRank(TypedDict):
    """One backlink ranking item."""

    path: str
    backlinks: int


class OutlinkRank(TypedDict):
    """One outlink ranking item."""

    path: str
    outlinks: int


class RecentNote(TypedDict):
    """Recent note with a calculated age."""

    path: str
    title: str
    modified_at: str
    folder: str
    days_ago: int


class TaggedNote(TypedDict):
    """Note grouped by tags."""

    path: str
    title: str
    folder: str
    tags: list[str]
    modified_at: str


type FolderTree = dict[str, int | FolderTree]


def _iter_query_rows(query: Any, batch_size: int = 1000) -> Iterator[dict[str, Any]]:
    """Iterate a LanceDB query in batches without truncating the result set."""
    for batch in query.limit(None).to_batches(batch_size=batch_size):
        yield from batch.to_pylist()


def register_search_tools(mcp, indexer, searcher):
    """
    Register search tools on the MCP server.

    Parameters:
        mcp: FastMCP instance
        indexer: VaultIndexer instance
        searcher: VaultSearcher instance
    """

    @mcp.tool()
    async def search_vault(
        query: str,
        top_k: int = 10,
        ctx: Context | None = None,
    ) -> list[dict[str, object]] | str:
        """
        Search vault notes semantically with cross-encoder reranking.

        Flow: query embedding, vector retrieval, then cross-encoder reranking.

        Parameters:
            query: search text
            top_k: number of results

        Returns:
            Results with note, section, text, and relevance score.
        """
        if ctx:
            await ctx.info(f"search_vault: '{log_query(query)}' top_k={top_k}")
        return execute_search("search_vault", query, top_k, searcher.search)

    @mcp.tool()
    async def search_vault_hybrid(
        query: str,
        top_k: int = 10,
        ctx: Context | None = None,
    ) -> list[dict[str, object]] | str:
        """
        Combine semantic and keyword search.

        This can recover exact technical terms, names, and acronyms alongside
        semantically related content.

        Parameters:
            query: search text
            top_k: number of results

        Returns:
            Results with note, section, text, and score.
        """
        if ctx:
            await ctx.info(f"search_vault_hybrid: '{log_query(query)}' top_k={top_k}")
        return execute_search("search_vault_hybrid", query, top_k, searcher.search_hybrid)

    @mcp.tool()
    def search_by_folder(
        query: str,
        folder: str,
        top_k: int = 10,
    ) -> list[dict[str, object]] | str:
        """
        Search semantically within one vault folder.

        Descendant folders are included by the search backend.

        Parameters:
            query: search text
            folder: folder filter, such as 'projects' or 'research/python'
            top_k: number of results

        Returns:
            Results from the selected folder.
        """
        if not folder or not folder.strip():
            return "Error: folder cannot be empty."
        return execute_search(
            "search_by_folder",
            query,
            top_k,
            searcher.search_by_folder,
            folder=folder.strip(),
        )

    @mcp.tool()
    async def vault_stats(ctx: Context | None = None) -> dict[str, object]:
        """
        Return search-index statistics.

        Returns:
            Totals for chunks and notes, plus the last modification time.
        """
        if ctx:
            await ctx.info("vault_stats")
        return indexer.get_stats()

    @mcp.tool()
    async def reindex_vault(
        dry_run: bool = False,
        require_daemon: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, object] | str:
        """
        Rebuild the complete vault index.

        Use this after broad reorganizations or when the rebuildable index must
        be recreated.

        Parameters:
            dry_run: return a preview without changing the index
            require_daemon: fail if the daemon is unavailable; the
                VAULT_SEARCH_REQUIRE_DAEMON environment variable also enables this

        Returns:
            Reindex statistics, or observed counts during a dry run.
        """
        import os

        from vault_search.core.models import ModelManager

        # Apply the explicit argument or environment policy.
        must_use_daemon = require_daemon or os.environ.get("VAULT_SEARCH_REQUIRE_DAEMON") == "1"

        if must_use_daemon and not dry_run:
            mm = ModelManager()
            try:
                mm.require_daemon(max_wait=30.0)
            except RuntimeError as e:
                return public_error_dict(
                    logger,
                    "reindex_vault_daemon",
                    e,
                    code="daemon_unavailable",
                    message="The required daemon is unavailable.",
                )

        msg = f"reindex_vault: {'dry_run' if dry_run else 'starting_full_reindex'}"
        if ctx:
            await ctx.info(msg)
        else:
            logger.info(msg)
        try:
            stats = indexer.full_reindex(dry_run=dry_run)
            if not dry_run:
                searcher.invalidate_cache()
            return stats
        except Exception as e:
            return public_error(logger, "reindex_vault", e)

    @mcp.tool()
    def reindex_note(path: str) -> dict[str, object] | str:
        """
        Reindex one note incrementally.

        Parameters:
            path: vault-relative note path, such as 'folder/my-note.md'

        Returns:
            Operation status and indexed chunk count.
        """
        if not path or not path.strip():
            return "Error: path cannot be empty."
        path = path.strip()
        logger.info("reindex_note requested")
        try:
            result = indexer.reindex_note(path)
            searcher.invalidate_cache()
            return result
        except Exception as e:
            return public_error(logger, "reindex_note", e)

    @mcp.tool()
    def system_stats(reset: bool = False) -> dict[str, object]:
        """
        Return measured operation and subsystem statistics.

        Includes observed p50/p95 latency, cache counters, catalog totals, and
        vector-index statistics.

        Parameters:
            reset: reset operation metrics after taking the snapshot

        Returns:
            Measured metrics and subsystem statistics.
        """
        logger.info(f"system_stats (reset={reset})")

        metrics = get_metrics()
        cache_stats = get_metadata_cache().stats()
        index_stats = indexer.get_stats()

        # The SQLite catalog may not be initialized yet.
        catalog_stats: object
        try:
            catalog_stats = get_catalog().stats()
        except Exception:
            catalog_stats = {"status": "not_initialized"}

        # Query-embedding cache.
        embedding_cache_stats = searcher.get_embedding_cache_stats()

        # Index prewarm status.
        prewarm_status = searcher.get_prewarm_status()

        result = {
            "performance": {
                "operations": metrics,
                "description": "Observed p50 and p95 latency in milliseconds",
            },
            "cache": {
                "metadata_cache": cache_stats,
                "embedding_cache": embedding_cache_stats,
                "description": "LRU caches for note metadata and query embeddings",
            },
            "catalog": {
                "notes_catalog": catalog_stats,
                "description": "SQLite catalog used by list_notes",
            },
            "index": index_stats,
            "prewarm": {
                "status": prewarm_status,
                "description": "Search-index prewarm state",
            },
        }

        if reset:
            reset_metrics()
            logger.info("system_metrics reset=true")

        return result

    @mcp.tool()
    def sync_vault(
        dry_run: bool = False,
        require_daemon: bool = False,
    ) -> dict[str, object]:
        """
        Synchronize vault files with the index.

        Detects new, modified, and deleted files.

        Use this after files changed while the server was stopped.

        Parameters:
            dry_run: report changes without updating the index
            require_daemon: fail if the daemon is unavailable; the
                VAULT_SEARCH_REQUIRE_DAEMON environment variable also enables this

        Returns:
            Counts for vault, indexed, new, modified, deleted, and synchronized files.
        """
        import os

        from vault_search.core.models import ModelManager

        # Apply the explicit argument or environment policy.
        must_use_daemon = require_daemon or os.environ.get("VAULT_SEARCH_REQUIRE_DAEMON") == "1"

        if must_use_daemon and not dry_run:
            mm = ModelManager()
            try:
                mm.require_daemon(max_wait=30.0)
            except RuntimeError as e:
                return public_error_dict(
                    logger,
                    "sync_vault_daemon",
                    e,
                    code="daemon_unavailable",
                    message="The required daemon is unavailable.",
                )

        logger.info(f"sync_vault (dry_run={dry_run})")

        try:
            stats = indexer.sync_check(auto_sync=not dry_run)
            return stats
        except Exception as e:
            return public_error_dict(logger, "sync_vault", e)

    @mcp.tool()
    def compact_index() -> dict[str, object] | str:
        """
        Compact the LanceDB index.

        This merges small fragments and removes obsolete versions after
        incremental mutations.

        Returns:
            Compaction statistics.
        """
        logger.info("compact_index started")
        try:
            stats = indexer.compact()
            return stats
        except Exception as e:
            return public_error(logger, "compact_index", e)

    @mcp.tool()
    def health_check() -> dict[str, object]:
        """
        Return a health snapshot for monitoring.

        Checks the index, catalog, model state, and measured latency alerts.

        Returns:
            Overall status and component details.
        """
        logger.info("health_check")

        # Component status.
        index_ready = False
        try:
            stats = indexer.get_stats()
            index_ready = stats.get("total_chunks", 0) > 0
        except Exception:
            pass

        catalog_ready = False
        try:
            catalog = get_catalog()
            catalog_ready = catalog.is_available()
        except Exception:
            pass

        models = ModelManager()
        models_status = models.is_loaded()
        daemon_required = models_status.get("daemon_required", False)

        # Latency and cache alerts.
        latency_alerts = check_latency_health()
        cache_alerts = check_cache_health()
        all_alerts = latency_alerts + cache_alerts

        if daemon_required and not models_status["using_daemon"]:
            all_alerts.append(
                {
                    "type": "daemon_required_unavailable",
                    "severity": "critical",
                    "message": "Required daemon is unavailable; local fallback is disabled.",
                }
            )

        # Determine overall status.
        status = "healthy"
        if not index_ready:
            status = "degraded"
        if all_alerts:
            status = "warning"
        if daemon_required and not models_status["using_daemon"]:
            status = "unhealthy"
        if not index_ready and not catalog_ready:
            status = "unhealthy"

        uptime_seconds = round(time.time() - _SERVER_START_TIME, 1)

        return {
            "status": status,
            "uptime_seconds": uptime_seconds,
            "components": {
                "index_ready": index_ready,
                "catalog_ready": catalog_ready,
                "embed_model_loaded": models_status["embed_model"],
                "reranker_loaded": models_status["reranker_model"],
                "daemon_required": daemon_required,
            },
            "alerts": all_alerts,
            "alerts_count": len(all_alerts),
        }

    @mcp.tool()
    def find_similar_notes(
        path: str,
        top_k: int = 5,
    ) -> list[dict[str, object]] | str:
        """
        Find notes similar to one specific note.

        Averages the note's chunk embeddings and searches for semantically
        similar content.

        Parameters:
            path: vault-relative path, such as 'projects/my-project.md'
            top_k: number of similar notes

        Returns:
            Similar notes with similarity scores.
        """
        # Bound a computationally expensive read.

        if not path or not path.strip():
            return "Error: path cannot be empty."

        path = path.strip()
        top_k = max(1, min(top_k, 20))

        logger.info("find_similar_notes top_k=%d", top_k)

        try:
            return searcher.find_similar_notes(path, top_k)
        except ValueError as e:
            return public_error(
                logger,
                "find_similar_notes",
                e,
                code="invalid_request",
                message="The note does not exist or the path is invalid.",
            )
        except Exception as e:
            return public_error(logger, "find_similar_notes", e)

    @mcp.tool()
    def search_duplicates(
        threshold: float = 0.90,
        max_notes: int = 500,
        folder: str | None = None,
    ) -> list[dict[str, object]] | str:
        """
        Find groups of duplicate or highly similar notes.

        Compares note embeddings and groups notes above the similarity threshold.

        Parameters:
            threshold: minimum similarity from 0.5 to 0.99
            max_notes: maximum notes to process
            folder: optional folder scope

        Returns:
            Duplicate groups with notes, count, and average similarity.

        Note:
            This operation is computationally expensive. Scope large vaults by
            folder or use a higher threshold.
        """
        # Apply bounded inputs.
        threshold = max(0.5, min(threshold, 0.99))
        max_notes = max(10, min(max_notes, 1000))

        if folder:
            folder = folder.strip()

        logger.info(
            "search_duplicates threshold=%s max_notes=%d folder_filter=%s",
            threshold,
            max_notes,
            bool(folder),
        )

        try:
            return searcher.search_duplicates(
                threshold=threshold,
                max_notes=max_notes,
                folder=folder,
            )
        except Exception as e:
            return public_error(logger, "search_duplicates", e)

    @mcp.tool()
    def search_advanced(
        query: str,
        top_k: int = 10,
        tags: list[str] | None = None,
        folder: str | None = None,
        extension: str | None = None,
        date_range: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        status: str | None = None,
        note_type: str | None = None,
        category: str | None = None,
        project: str | None = None,
        exclude: list[str] | None = None,
        highlight: bool = False,
    ) -> list[dict[str, object]] | str:
        """
        Run semantic search with structured filters.

        Every optional filter is combined with AND.

        Parameters:
            query: semantic search text
            top_k: number of results
            tags: tag filter, with OR between tags
            folder: folder filter including descendants
            extension: file extension such as md, canvas, or pdf
            date_range: today, week, month, or year
            date_from: ISO start date, ignored when date_range is set
            date_to: ISO end date, ignored when date_range is set
            status: note status
            note_type: note type
            category: category
            project: associated project name
            exclude: terms to exclude from results
            highlight: highlight query terms in result text

        Returns:
            Results with note, section, text, score, and metadata.

        Examples:
            search_advanced("python", exclude=["django", "flask"])
            search_advanced("API REST", highlight=True)
            search_advanced("meeting", note_type="meeting", highlight=True)
        """
        # Bounded read operation.

        # Validate the query.
        if not query or not query.strip():
            return "Error: query cannot be empty."

        # Truncate oversized queries.
        query = truncate_query(query.strip())
        top_k = clamp_top_k(top_k)

        # Build a custom date range when explicit dates are supplied.
        effective_date_range: str | dict[str, str] | None = None
        if date_range:
            effective_date_range = date_range.strip().lower()
        elif date_from or date_to:
            effective_date_range = {}
            if date_from:
                effective_date_range["from"] = date_from.strip()
            if date_to:
                effective_date_range["to"] = date_to.strip()

        # Normalize parameters.
        normalized_tags = None
        if tags:
            normalized_tags = [t.strip() for t in tags if t and t.strip()]
            if not normalized_tags:
                normalized_tags = None

        normalized_folder = folder.strip() if folder and folder.strip() else None

        # Validate the extension allowlist.
        normalized_extension = None
        if extension and extension.strip():
            ext = extension.strip().lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            if ext not in INDEXABLE_EXTENSIONS:
                valid_exts = ", ".join(sorted(INDEXABLE_EXTENSIONS))
                return f"Error: invalid extension '{ext}'. Valid values: {valid_exts}"
            normalized_extension = ext

        normalized_status = status.strip().lower() if status and status.strip() else None
        normalized_note_type = (
            note_type.strip().lower() if note_type and note_type.strip() else None
        )
        normalized_category = category.strip().lower() if category and category.strip() else None
        normalized_project = project.strip() if project and project.strip() else None

        # Normalize excluded terms under the operational limit.
        normalized_exclude = None
        if exclude:
            normalized_exclude = [t.strip() for t in exclude if t and t.strip()]
            if not normalized_exclude:
                normalized_exclude = None
            elif len(normalized_exclude) > MAX_EXCLUDE_TERMS:
                # Truncate the excluded-term list.
                logger.warning(
                    "exclude_terms_truncated original_count=%d maximum_count=%d",
                    len(normalized_exclude),
                    MAX_EXCLUDE_TERMS,
                )
                normalized_exclude = normalized_exclude[:MAX_EXCLUDE_TERMS]

        active_filter_count = sum(
            bool(value)
            for value in (
                normalized_tags,
                normalized_folder,
                normalized_extension,
                effective_date_range,
                normalized_status,
                normalized_note_type,
                normalized_category,
                normalized_project,
                normalized_exclude,
                highlight,
            )
        )
        logger.info(
            "search_advanced query_length=%d top_k=%d active_filters=%d",
            len(query),
            top_k,
            active_filter_count,
        )

        try:
            return searcher.search_advanced(
                query=query,
                top_k=top_k,
                tags=normalized_tags,
                folder=normalized_folder,
                extension=normalized_extension,
                date_range=effective_date_range,
                status=normalized_status,
                note_type=normalized_note_type,
                category=normalized_category,
                project=normalized_project,
                exclude=normalized_exclude,
                highlight=highlight,
            )
        except Exception as e:
            return public_error(logger, "search_advanced", e)

    @mcp.tool()
    def benchmark_search(
        query: str = "test",
        iterations: int = 10,
    ) -> dict[str, int | float | str] | str:
        """
        Measure local search latency.

        Results describe only this process, data set, configuration, and runtime.

        Parameters:
            query: benchmark search text
            iterations: bounded iteration count

        Returns:
            Observed mean, min, max, p50, and p95 latency in milliseconds.
        """
        iterations = max(1, min(iterations, 100))

        times_ms: list[float] = []
        error_types: list[str] = []
        for _ in range(iterations):
            start = time.perf_counter()
            try:
                searcher.search(query, top_k=10)
            except Exception as e:
                error_types.append(type(e).__name__)
            elapsed_ms = (time.perf_counter() - start) * 1000
            times_ms.append(elapsed_ms)

        # Do not report latency as successful when every search failed.
        if error_types and len(error_types) == iterations:
            return {
                "query_length": len(query),
                "iterations": iterations,
                "errors": len(error_types),
                "sample_error_type": error_types[0],
                "hint": "Check daemon readiness or allow the configured local fallback.",
            }

        times_ms.sort()
        n = len(times_ms)

        result: dict[str, int | float | str] = {
            "query_length": len(query),
            "iterations": iterations,
            "mean_ms": round(sum(times_ms) / n, 2),
            "min_ms": round(times_ms[0], 2),
            "max_ms": round(times_ms[-1], 2),
            "p50_ms": round(times_ms[n // 2], 2),
            "p95_ms": round(times_ms[int(n * 0.95)], 2),
        }
        if error_types:
            result["errors"] = len(error_types)
            result["sample_error_type"] = error_types[0]
        return result

    @mcp.tool()
    def vector_index_status() -> dict[str, object]:
        """
        Return ANN vector-index status.

        The index is created when the configured chunk threshold is reached.

        Returns:
            Current existence, threshold, chunk count, eligibility, and
            auto-creation setting.
        """
        logger.info("vector_index_status")
        return indexer.get_vector_index_status()

    @mcp.tool()
    def get_backlinks(
        path: str,
        include_context: bool = True,
    ) -> list[BacklinkResult] | str:
        """
        Find notes that link to one target note.

        Uses the rebuildable link index instead of rereading vault files.

        Parameters:
            path: vault-relative target path, such as 'projects/my-project.md'
            include_context: include text around the link

        Returns:
            Deduplicated backlinks with path, title, link type, original target,
            and optional context.
        """
        if not path or not path.strip():
            return "Error: path cannot be empty."

        path = path.strip()
        logger.info("get_backlinks include_context=%s", include_context)

        try:
            # Normalize target variants for matching.
            target_normalized = normalize_link_target(path)
            target_stem = normalize_link_target(Path(path).stem)

            # Query the link index.
            links_table = indexer._ensure_links_table()

            # Escape values before building the filter.
            path_escaped = escape_sql_string(path)
            target_norm_escaped = escape_sql_string(target_normalized)
            target_stem_escaped = escape_sql_string(target_stem)

            # Match the resolved path or a normalized target variant.
            results = (
                links_table.search()
                .where(
                    f"to_note_path = '{path_escaped}' OR "
                    f"link_target_normalized = '{target_norm_escaped}' OR "
                    f"link_target_normalized = '{target_stem_escaped}'"
                )
                .select(
                    [
                        "from_note_path",
                        "from_note_title",
                        "link_type",
                        "link_target",
                        "context",
                    ]
                )
                .limit(1000)
                .to_list()
            )

            # Deduplicate source notes that link to the target more than once.
            seen_notes = set()
            backlinks: list[BacklinkResult] = []

            for row in results:
                note_path = row["from_note_path"]
                # Exclude self-links and duplicates.
                if note_path in seen_notes or note_path == path:
                    continue
                seen_notes.add(note_path)

                backlink: BacklinkResult = {
                    "path": note_path,
                    "title": row["from_note_title"],
                    "link_type": row["link_type"],
                    "link_target": row["link_target"],
                }
                if include_context and row["context"]:
                    backlink["context"] = row["context"]

                backlinks.append(backlink)

            logger.info("get_backlinks result_count=%d", len(backlinks))
            return backlinks

        except Exception as e:
            return public_error(logger, "get_backlinks", e)

    def _extract_link_context(content: str, link_marker: str, context_chars: int = 100) -> str:
        """Extract bounded context around a link marker."""
        idx = content.find(link_marker)
        if idx == -1:
            return ""

        start = max(0, idx - context_chars)
        end = min(len(content), idx + len(link_marker) + context_chars)

        # Prefer word boundaries.
        if start > 0:
            space_idx = content.find(" ", start)
            if space_idx != -1 and space_idx < idx:
                start = space_idx + 1

        if end < len(content):
            space_idx = content.rfind(" ", idx, end)
            if space_idx != -1:
                end = space_idx

        context = content[start:end].strip()
        if start > 0:
            context = "..." + context
        if end < len(content):
            context = context + "..."

        # Flatten line breaks in the context snippet.
        return " ".join(context.split())

    @mcp.tool()
    def get_outlinks(path: str) -> dict[str, object] | str:
        """
        List every indexed link from one note.

        Uses the same rebuildable link index as get_backlinks.

        Parameters:
            path: vault-relative note path, such as 'projects/my-project.md'

        Returns:
            Links grouped as wikilinks, Markdown links, embeds, and external
            URLs, plus total and broken counts.
        """
        if not path or not path.strip():
            return "Error: path cannot be empty."

        path = path.strip()
        logger.info("get_outlinks requested")

        try:
            links_table = indexer._ensure_links_table()

            # Query every indexed link from this note.
            escaped_path = escape_sql_string(path)
            results = (
                links_table.search()
                .where(f"from_note_path = '{escaped_path}'")
                .select(
                    [
                        "link_type",
                        "link_target",
                        "to_note_path",
                        "is_resolved",
                        "alias",
                        "heading",
                        "block_ref",
                    ]
                )
                .limit(1000)
                .to_list()
            )

            # Group links by type.
            wikilinks = []
            markdown_links = []
            embeds = []
            external = []

            for row in results:
                link_info = {
                    "target": row["link_target"],
                    "resolved": row["is_resolved"],
                }
                if row["is_resolved"] and row["to_note_path"]:
                    link_info["resolved_path"] = row["to_note_path"]
                if row["alias"]:
                    link_info["alias"] = row["alias"]
                if row["heading"]:
                    link_info["heading"] = row["heading"]
                if row["block_ref"]:
                    link_info["block_ref"] = row["block_ref"]

                if row["link_type"] == "wikilink":
                    wikilinks.append(link_info)
                elif row["link_type"] == "markdown":
                    markdown_links.append(link_info)
                elif row["link_type"] == "embed":
                    embeds.append(link_info)
                elif row["link_type"] == "external":
                    external.append({"url": row["link_target"]})

            # Count unresolved wiki and Markdown links.
            broken_count = sum(1 for w in wikilinks if not w["resolved"])
            broken_count += sum(1 for m in markdown_links if not m["resolved"])

            return {
                "path": path,
                "wikilinks": wikilinks,
                "markdown_links": markdown_links,
                "embeds": embeds,
                "external": external,
                "total": len(wikilinks) + len(markdown_links) + len(embeds) + len(external),
                "broken_count": broken_count,
            }

        except Exception as e:
            return public_error(logger, "get_outlinks", e)

    @mcp.tool()
    def find_broken_links(
        folder: str | None = None,
        limit: int = 100,
    ) -> dict[str, object] | str:
        """
        Find links that point to missing notes.

        Broken links have is_resolved=false in the rebuildable link index.

        Parameters:
            folder: optional folder filter
            limit: maximum returned notes

        Returns:
            Broken-link total, affected-note count, and bounded note details.
        """
        limit = max(1, min(limit, 500))
        logger.info("find_broken_links folder_filter=%s limit=%d", bool(folder), limit)

        try:
            normalized_folder = folder.strip() if folder else None
            if normalized_folder and not validate_relative_path(normalized_folder):
                raise ValueError("Folder is invalid or outside the vault")
            links_table = indexer._ensure_links_table()

            # Find unresolved non-external links.
            where_clause = "is_resolved = false AND link_type != 'external'"
            if normalized_folder:
                escaped_folder = escape_sql_string(escape_like_pattern(normalized_folder))
                where_clause += f" AND from_note_path LIKE '{escaped_folder}/%' ESCAPE '\\'"

            counts: dict[str, BrokenNoteCount] = {}
            total_broken = 0
            count_query = (
                links_table.search()
                .where(where_clause)
                .select(["from_note_path", "from_note_title"])
            )
            for row in _iter_query_rows(count_query):
                note_path = row["from_note_path"]
                total_broken += 1
                if note_path not in counts:
                    counts[note_path] = {
                        "path": note_path,
                        "title": row["from_note_title"],
                        "count": 0,
                    }
                counts[note_path]["count"] += 1

            selected = sorted(
                counts.values(),
                key=lambda item: (-item["count"], item["path"]),
            )[:limit]
            selected_paths = {item["path"] for item in selected}
            broken_by_path: dict[str, list[BrokenLinkDetail]] = {
                path: [] for path in selected_paths
            }

            if selected_paths:
                details_query = (
                    links_table.search()
                    .where(where_clause)
                    .select(["from_note_path", "link_type", "link_target", "context"])
                )
                for row in _iter_query_rows(details_query):
                    note_path = row["from_note_path"]
                    if note_path not in selected_paths:
                        continue
                    broken_by_path[note_path].append(
                        {
                            "target": row["link_target"],
                            "type": row["link_type"],
                            "context": row["context"] if row["context"] else "",
                        }
                    )

            notes_list = [
                {
                    "path": item["path"],
                    "title": item["title"],
                    "broken_links": broken_by_path[item["path"]],
                }
                for item in selected
            ]

            return {
                "total_broken_links": total_broken,
                "notes_with_broken_links": len(counts),
                "returned_notes": len(notes_list),
                "has_more": len(counts) > len(notes_list),
                "notes": notes_list,
            }

        except Exception as e:
            return public_error(logger, "find_broken_links", e)

    @mcp.tool()
    def find_orphan_notes(
        folder: str | None = None,
        limit: int = 100,
    ) -> dict[str, object] | str:
        """
        Find notes with no backlinks.

        These notes are isolated from incoming graph edges.

        Parameters:
            folder: optional folder filter
            limit: maximum returned notes

        Returns:
            Total notes, orphan count and percentage, plus bounded note details.
        """
        limit = max(1, min(limit, 500))
        logger.info("find_orphan_notes folder_filter=%s limit=%d", bool(folder), limit)

        try:
            normalized_folder = folder.strip() if folder else None
            if normalized_folder and not validate_relative_path(normalized_folder):
                raise ValueError("Folder is invalid or outside the vault")
            catalog = get_catalog()
            if not catalog.is_available():
                return "Error: catalog is unavailable. Run reindex_vault first."

            # Read notes that have incoming links.
            links_table = indexer._ensure_links_table()

            # Collect linked paths and normalized targets.
            linked_paths: set[str] = set()
            linked_normalized: set[str] = set()
            linked_query = links_table.search().select(["to_note_path", "link_target_normalized"])
            for row in _iter_query_rows(linked_query):
                if row["to_note_path"]:
                    linked_paths.add(row["to_note_path"])
                if row["link_target_normalized"]:
                    linked_normalized.add(row["link_target_normalized"])

            # The catalog yields newest first. Retain only the oldest requested
            # orphan notes while still counting the complete result set.
            oldest_orphans: deque[OrphanNote] = deque(maxlen=limit)
            orphan_count = 0
            total = 0
            offset = 0
            page_size = 5000
            while offset == 0 or offset < total:
                notes, total = catalog.list_notes(
                    folder=normalized_folder,
                    extension=".md",
                    limit=page_size,
                    offset=offset,
                )
                if not notes:
                    break
                for note in notes:
                    note_path = note["path"]
                    path_normalized = normalize_link_target(note_path)
                    stem_normalized = normalize_link_target(Path(note_path).stem)
                    if (
                        note_path in linked_paths
                        or path_normalized in linked_normalized
                        or stem_normalized in linked_normalized
                    ):
                        continue
                    orphan_count += 1
                    oldest_orphans.append(
                        {
                            "path": note_path,
                            "title": note.get("title", note_path),
                            "folder": note.get("folder", ""),
                            "modified_at": note.get("modified_at", ""),
                        }
                    )
                offset += len(notes)

            orphans = list(reversed(oldest_orphans))
            orphan_pct = round(orphan_count / max(total, 1) * 100, 1)

            return {
                "total_notes": total,
                "total_orphans": orphan_count,
                "orphan_percentage": orphan_pct,
                "returned_notes": len(orphans),
                "has_more": orphan_count > len(orphans),
                "notes": orphans,
            }

        except Exception as e:
            return public_error(logger, "find_orphan_notes", e)

    @mcp.tool()
    def link_stats(limit: int = 50) -> dict[str, object] | str:
        """
        Return vault link statistics.

        Includes totals, most-referenced notes, and notes with most outlinks.

        Parameters:
            limit: maximum notes in each ranking

        Returns:
            Link totals, resolution rate, most-referenced notes, and notes with
            the most outlinks.
        """
        limit = max(1, min(limit, 200))
        logger.info(f"link_stats: limit={limit}")

        try:
            links_table = indexer._ensure_links_table()

            # Count indexed links.
            all_links = (
                links_table.search()
                .select(
                    [
                        "from_note_path",
                        "to_note_path",
                        "link_type",
                        "is_resolved",
                    ]
                )
                .limit(100000)
                .to_list()
            )

            total_links = len(all_links)
            total_resolved = sum(1 for link in all_links if link["is_resolved"])
            total_broken = sum(
                1
                for link in all_links
                if not link["is_resolved"] and link["link_type"] != "external"
            )
            total_external = sum(1 for link in all_links if link["link_type"] == "external")

            # Count backlinks by target note.
            backlink_count: dict[str, int] = {}
            for link in all_links:
                target = link["to_note_path"]
                if target:
                    backlink_count[target] = backlink_count.get(target, 0) + 1

            backlink_ranking: list[BacklinkRank] = [
                {"path": path, "backlinks": count} for path, count in backlink_count.items()
            ]
            most_referenced = sorted(
                backlink_ranking,
                key=lambda x: x["backlinks"],
                reverse=True,
            )[:limit]

            # Count outlinks by source note.
            outlink_count: dict[str, int] = {}
            for link in all_links:
                source = link["from_note_path"]
                outlink_count[source] = outlink_count.get(source, 0) + 1

            outlink_ranking: list[OutlinkRank] = [
                {"path": path, "outlinks": count} for path, count in outlink_count.items()
            ]
            most_outlinks = sorted(
                outlink_ranking,
                key=lambda x: x["outlinks"],
                reverse=True,
            )[:limit]

            # Calculate the non-external resolution rate.
            non_external = total_links - total_external
            resolution_rate = round(total_resolved / max(non_external, 1) * 100, 1)

            return {
                "total_links": total_links,
                "total_resolved": total_resolved,
                "total_broken": total_broken,
                "total_external": total_external,
                "resolution_rate": resolution_rate,
                "unique_sources": len(outlink_count),
                "unique_targets": len(backlink_count),
                "most_referenced": most_referenced,
                "most_outlinks": most_outlinks,
            }

        except Exception as e:
            return public_error(logger, "link_stats", e)

    @mcp.tool()
    def get_recent_notes(
        days: int = 7,
        limit: int = 20,
        folder: str | None = None,
    ) -> list[RecentNote] | str:
        """
        Return recently modified notes.

        Results are ordered by modification time, newest first.

        Parameters:
            days: bounded time window in days
            limit: maximum returned notes
            folder: optional folder filter

        Returns:
            Notes with path, title, modified_at, folder, and days_ago.
        """
        from datetime import datetime, timedelta

        # Bound a potentially expensive read.

        # Validate parameters.
        days = max(1, min(days, 365))
        limit = max(1, min(limit, 100))

        logger.info(
            "get_recent_notes days=%d limit=%d folder_filter=%s",
            days,
            limit,
            bool(folder),
        )

        try:
            catalog = get_catalog()
            if not catalog.is_available():
                return "Error: catalog is unavailable. Run reindex_vault first."

            # Read a wider catalog page before applying the date window.
            notes, _ = catalog.list_notes(
                folder=folder.strip() if folder else None,
                limit=limit * 2,
            )

            # Apply the date window.
            now = datetime.now()
            cutoff = now - timedelta(days=days)
            recent: list[RecentNote] = []

            for note in notes:
                try:
                    modified = datetime.fromisoformat(note["modified_at"])
                    if modified >= cutoff:
                        days_ago = (now - modified).days
                        recent.append(
                            {
                                "path": note["path"],
                                "title": note.get("title", note["path"]),
                                "modified_at": note["modified_at"],
                                "folder": note.get("folder", ""),
                                "days_ago": days_ago,
                            }
                        )
                except ValueError, KeyError:
                    continue

            # Sort newest first and apply the limit.
            recent.sort(key=lambda x: x["modified_at"], reverse=True)
            recent = recent[:limit]

            logger.info("get_recent_notes result_count=%d days=%d", len(recent), days)
            return recent

        except Exception as e:
            return public_error(logger, "get_recent_notes", e)

    @mcp.tool()
    def tag_stats(
        limit: int = 50,
        folder: str | None = None,
    ) -> dict[str, object] | str:
        """
        Return vault tag usage statistics.

        Each tag is counted once per note.

        Parameters:
            limit: maximum returned tags
            folder: optional folder filter

        Returns:
            Tag total, tagged-note count, and tags ordered by frequency.
        """
        from collections import Counter

        # Bound a potentially expensive read.

        limit = max(1, min(limit, 500))
        folder_filter = folder.strip() if folder else None

        logger.info("tag_stats limit=%d folder_filter=%s", limit, bool(folder_filter))

        try:
            # Read the LanceDB table.
            table = indexer._ensure_table()
            total_rows = table.count_rows()

            if total_rows == 0:
                return {
                    "total_tags": 0,
                    "total_notes_with_tags": 0,
                    "tags": [],
                }

            # Select the fields needed to deduplicate tags by note.
            query = table.search().select(["note_path", "tags", "folder"])

            if folder_filter:
                # Match the folder and descendants.
                query = query.where(
                    f"folder = '{folder_filter}' OR folder LIKE '{folder_filter}/%'"
                )

            arrow_table = query.limit(total_rows).to_arrow()

            # Deduplicate tags within each note.
            note_tags: dict[str, set[str]] = {}

            for i in range(arrow_table.num_rows):
                note_path = arrow_table.column("note_path")[i].as_py()
                tags_str = arrow_table.column("tags")[i].as_py()

                if note_path not in note_tags:
                    note_tags[note_path] = set()

                if tags_str:
                    # Tags are stored as comma-separated text.
                    for tag in tags_str.split(","):
                        tag = tag.strip()
                        if tag:
                            note_tags[note_path].add(tag)

            # Count each tag once per note.
            tag_counter: Counter[str] = Counter()
            notes_with_tags = 0

            for tags in note_tags.values():
                if tags:
                    notes_with_tags += 1
                    tag_counter.update(tags)

            # Order by frequency and apply the limit.
            top_tags = [
                {"tag": tag, "count": count} for tag, count in tag_counter.most_common(limit)
            ]

            result = {
                "total_tags": len(tag_counter),
                "total_notes_with_tags": notes_with_tags,
                "tags": top_tags,
            }

            logger.info(
                "tag_stats total_tags=%d notes_with_tags=%d",
                result["total_tags"],
                notes_with_tags,
            )
            return result

        except Exception as e:
            return public_error(logger, "tag_stats", e)

    @mcp.tool()
    def folder_tree(
        include_counts: bool = True,
        max_depth: int = FOLDER_TREE_MAX_DEPTH,
    ) -> dict[str, object] | str:
        """
        Return the vault folder structure as a hierarchy.

        Uses the SQLite catalog without scanning the filesystem.

        Parameters:
            include_counts: include note counts by folder
            max_depth: hierarchy depth, bounded by configuration

        Returns:
            Folder total, note total, and hierarchical tree.
        """
        from pathlib import PurePosixPath

        # Bound a potentially expensive read.

        max_depth = max(1, min(max_depth, FOLDER_TREE_MAX_DEPTH_LIMIT))

        logger.info(f"folder_tree: include_counts={include_counts}, max_depth={max_depth}")

        def insert_path(
            tree: FolderTree,
            path_parts: tuple[str, ...],
            count: int,
            depth: int = 0,
        ) -> None:
            """Insert one path into the hierarchy."""
            if not path_parts or depth >= max_depth:
                return

            part = path_parts[0]
            remaining = path_parts[1:]

            current_value = tree.get(part)
            child: FolderTree
            if isinstance(current_value, dict):
                child = current_value
            else:
                child = {}
                tree[part] = child

            # Accumulate the count at this level.
            if include_counts:
                if not remaining or depth + 1 >= max_depth:
                    # Count a leaf or depth-truncated folder here.
                    current_count = child.get("_count", 0)
                    child["_count"] = (
                        current_count if isinstance(current_count, int) else 0
                    ) + count

            # Continue to the next level.
            if remaining and depth + 1 < max_depth:
                insert_path(child, remaining, count, depth + 1)

        try:
            catalog = get_catalog()
            if not catalog.is_available():
                return "Error: catalog is unavailable. Run reindex_vault first."

            # Aggregate folder counts in SQLite.
            with catalog._connection() as conn:
                rows = conn.execute("""
                    SELECT folder, COUNT(*) as count
                    FROM notes_catalog
                    GROUP BY folder
                    ORDER BY folder
                """).fetchall()

            if not rows:
                return {
                    "total_folders": 0,
                    "total_notes": 0,
                    "tree": {},
                }

            # Build the hierarchy.
            tree: FolderTree = {}
            total_notes = 0
            all_folders: set[str] = set()

            for row in rows:
                folder = row["folder"]
                count = row["count"]
                total_notes += count

                if not folder:
                    # Notes in the vault root.
                    if include_counts:
                        root_count = tree.get("_count", 0)
                        tree["_count"] = (root_count if isinstance(root_count, int) else 0) + count
                    continue

                # Parse stored POSIX paths.
                path = PurePosixPath(folder)
                parts = path.parts[:max_depth]

                # Record intermediate folders.
                for i in range(1, len(parts) + 1):
                    all_folders.add("/".join(parts[:i]))

                # Insert the folder into the hierarchy.
                insert_path(tree, parts, count)

            result = {
                "total_folders": len(all_folders),
                "total_notes": total_notes,
                "tree": tree,
            }

            logger.info(
                "folder_tree total_folders=%d total_notes=%d",
                result["total_folders"],
                total_notes,
            )
            return result

        except Exception as e:
            return public_error(logger, "folder_tree", e)

    @mcp.tool()
    def search_by_tags(
        tags: list[str],
        match_all: bool = False,
        limit: int = 50,
    ) -> list[TaggedNote] | str:
        """
        Find notes by exact tags without semantic search.

        Use tag_stats to discover available tags before filtering.

        Parameters:
            tags: tags to find, such as ["project", "2024"]
            match_all: require every tag when true, otherwise any tag
            limit: maximum returned notes

        Returns:
            Notes with path, title, folder, tags, and modified_at.
        """
        # Bound a potentially expensive read.

        # Validate input.
        if not tags:
            return "Error: tag list cannot be empty."

        # Normalize tags.
        clean_tags = []
        for tag in tags:
            if isinstance(tag, str):
                t = tag.strip().lower()
                if t:
                    clean_tags.append(t)

        if not clean_tags:
            return "Error: no valid tag was provided."

        # Bound the number of query clauses.
        if len(clean_tags) > 20:
            clean_tags = clean_tags[:20]
            logger.warning("search_by_tags tags_truncated maximum_count=20")

        limit = max(1, min(limit, 200))

        logger.info(
            "search_by_tags tag_count=%d match_all=%s limit=%d",
            len(clean_tags),
            match_all,
            limit,
        )

        try:
            table = indexer._ensure_table()
            total_rows = table.count_rows()

            if total_rows == 0:
                return []

            # Select only fields needed by the response.
            query = table.search().select(
                ["note_path", "note_title", "folder", "tags", "modified_at"]
            )

            # Build one WHERE predicate per stored comma-separated tag.
            conditions = []
            for tag in clean_tags:
                # Escape SQL string delimiters.
                escaped_tag = tag.replace("'", "''")
                # Match an exact tag at each possible list position.
                conditions.append(
                    f"(tags = '{escaped_tag}' OR "
                    f"tags LIKE '{escaped_tag}, %' OR "
                    f"tags LIKE '%, {escaped_tag}' OR "
                    f"tags LIKE '%, {escaped_tag}, %')"
                )

            # The initial database filter uses OR between tags.
            where_clause = " OR ".join(conditions)
            query = query.where(where_clause)

            # Read the filtered chunk rows.
            arrow_table = query.limit(total_rows).to_arrow()

            # Deduplicate chunks by note.
            notes_map: dict[str, TaggedNote] = {}

            for i in range(arrow_table.num_rows):
                note_path = arrow_table.column("note_path")[i].as_py()

                if note_path in notes_map:
                    continue

                tags_str = arrow_table.column("tags")[i].as_py() or ""
                note_tags = {t.strip().lower() for t in tags_str.split(",") if t.strip()}

                # Enforce all requested tags after deduplication.
                if match_all:
                    if not all(t in note_tags for t in clean_tags):
                        continue

                notes_map[note_path] = {
                    "path": note_path,
                    "title": arrow_table.column("note_title")[i].as_py(),
                    "folder": arrow_table.column("folder")[i].as_py(),
                    "tags": sorted(note_tags),
                    "modified_at": arrow_table.column("modified_at")[i].as_py(),
                }

            # Sort newest first and apply the limit.
            results = sorted(
                notes_map.values(), key=lambda x: x["modified_at"] or "", reverse=True
            )[:limit]

            logger.info("search_by_tags result_count=%d", len(results))
            return results

        except Exception as e:
            return public_error(logger, "search_by_tags", e)

    @mcp.tool()
    def random_note(
        folder: str | None = None,
        extension: str | None = None,
    ) -> dict[str, object] | str:
        """
        Return a random vault note.

        Uses SQLite's random ordering over the filtered catalog.

        Parameters:
            folder: optional folder filter
            extension: optional extension filter, such as .md or .pdf

        Returns:
            Note path, title, folder, modification time, and size.
        """
        from vault_search.config.search import INDEXABLE_EXTENSIONS

        # Bound a potentially expensive read.

        # Normalize filters.
        folder_filter = folder.strip() if folder and folder.strip() else None
        ext_filter = None
        if extension and extension.strip():
            ext = extension.strip().lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            if ext not in INDEXABLE_EXTENSIONS:
                valid_exts = ", ".join(sorted(INDEXABLE_EXTENSIONS))
                return f"Error: invalid extension '{ext}'. Valid values: {valid_exts}"
            ext_filter = ext

        logger.info(
            "random_note folder_filter=%s extension_filter=%s",
            bool(folder_filter),
            bool(ext_filter),
        )

        try:
            catalog = get_catalog()
            if not catalog.is_available():
                return "Error: catalog is unavailable. Run reindex_vault first."

            # Select one random matching catalog row.
            from vault_search.utils.security import escape_like_pattern

            conditions = []
            params = []

            if folder_filter:
                conditions.append("(folder = ? OR folder LIKE ? ESCAPE '\\')")
                escaped = escape_like_pattern(folder_filter)
                params.extend([folder_filter, f"{escaped}/%"])

            if ext_filter:
                conditions.append("extension = ?")
                params.append(ext_filter)

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            with catalog._connection() as conn:
                row = conn.execute(
                    f"""
                    SELECT path, folder, extension, title, mtime_ns, size
                    FROM notes_catalog
                    {where_clause}
                    ORDER BY RANDOM()
                    LIMIT 1
                """,
                    params,
                ).fetchone()

            if not row:
                msg = "No note found"
                if folder_filter:
                    msg += f" in folder '{folder_filter}'"
                if ext_filter:
                    msg += f" with extension '{ext_filter}'"
                return msg + "."

            from datetime import datetime

            return {
                "path": row["path"],
                "title": row["title"],
                "folder": row["folder"],
                "extension": row["extension"],
                "modified_at": datetime.fromtimestamp(row["mtime_ns"] / 1_000_000_000).isoformat(),
                "size_bytes": row["size"],
            }

        except Exception as e:
            return public_error(logger, "random_note", e)

    @mcp.tool()
    def daily_note(
        date: str | None = None,
        folder: str = "daily",
    ) -> dict[str, object] | str:
        """
        Return information about the daily note for one date.

        Daily notes use the Obsidian YYYY-MM-DD.md convention in the selected folder.

        Parameters:
            date: ISO date, such as 2024-01-15; defaults to today
            folder: daily-note folder

        Returns:
            Existing note metadata, or the expected path when absent.
        """
        from datetime import date as date_type
        from datetime import datetime

        # Bound a potentially expensive read.

        # Resolve the date.
        if date:
            date = date.strip()
            try:
                # Validate ISO format.
                parsed_date = datetime.fromisoformat(date).date()
            except ValueError:
                return f"Error: invalid date '{date}'. Use ISO format YYYY-MM-DD"
        else:
            parsed_date = date_type.today()

        date_str = parsed_date.isoformat()

        # Normalize the folder.
        folder = folder.strip() if folder and folder.strip() else "daily"

        # Build the expected path.
        expected_filename = f"{date_str}.md"
        expected_path = f"{folder}/{expected_filename}" if folder else expected_filename

        logger.info("daily_note requested")

        try:
            file_path = resolve_path(expected_path)
            expected_path = file_path.relative_to(resolve_internal_path()).as_posix()

            # Check the catalog first.
            catalog = get_catalog()

            if catalog.is_available():
                with catalog._connection() as conn:
                    row = conn.execute(
                        """
                        SELECT path, folder, title, mtime_ns, size
                        FROM notes_catalog
                        WHERE path = ?
                    """,
                        [expected_path],
                    ).fetchone()

                    if row:
                        return {
                            "exists": True,
                            "path": row["path"],
                            "title": row["title"],
                            "folder": row["folder"],
                            "date": date_str,
                            "modified_at": datetime.fromtimestamp(
                                row["mtime_ns"] / 1_000_000_000
                            ).isoformat(),
                            "size_bytes": row["size"],
                        }

            # Fall back to the filesystem.
            if file_path.exists():
                stat = file_path.stat()
                return {
                    "exists": True,
                    "path": expected_path,
                    "title": date_str,
                    "folder": folder,
                    "date": date_str,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "size_bytes": stat.st_size,
                }

            # The daily note is absent.
            return {
                "exists": False,
                "expected_path": expected_path,
                "date": date_str,
                "folder": folder,
            }

        except Exception as e:
            return public_error(logger, "daily_note", e)
