"""
MCP server for vault search and CRUD.

Exposes search and note-management tools to MCP clients.

Public tool names are registered in the server tool modules and checked by the
publication gate.
"""

import os
import threading

# Allow PyTorch to fall back for operations unsupported by Apple MPS.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

from fastmcp import FastMCP

from vault_search.config.paths import DATA_DIR
from vault_search.core.indexer import VaultIndexer
from vault_search.core.searcher import VaultSearcher
from vault_search.crud.catalog import get_catalog
from vault_search.server.crud_tools import register_crud_tools
from vault_search.server.graph_tools import register_graph_tools
from vault_search.server.middleware import SafeErrorMiddleware, SafeTimingMiddleware
from vault_search.server.resource_tools import register_resources
from vault_search.server.search_tools import register_search_tools
from vault_search.utils.logging import configure_logging, get_logger
from vault_search.utils.shutdown import ShutdownManager, protected_section
from vault_search.watching.watcher import VaultWatcher

# Configure privacy-safe structured logging.
configure_logging()
logger = get_logger("vault-search-mcp")

# Shared instances with internal lazy loading.
_indexer = VaultIndexer()
_searcher = VaultSearcher()
_watcher = VaultWatcher(_indexer, on_reindex=_searcher.invalidate_cache)

# Initialization threads retained for shutdown joins.
_init_threads: list[threading.Thread] = []


def _shutdown_watcher():
    """Stop the watcher gracefully."""
    with protected_section("stopping file watcher"):
        _watcher.stop()


def _shutdown_catalog():
    """Stop the catalog gracefully."""
    with protected_section("stopping catalog reconciliation"):
        try:
            catalog = get_catalog()
            catalog.stop_reconciliation()
        except Exception:
            pass


def _shutdown_models():
    """Release ML models from memory."""
    with protected_section("releasing ML models"):
        from vault_search.core.models import ModelManager

        ModelManager().cleanup()


def _shutdown_init_threads():
    """Wait for initialization threads to finish."""
    with protected_section("waiting for initialization threads"):
        for thread in _init_threads:
            if thread.is_alive():
                logger.debug("waiting_for_init_thread", thread_name=thread.name)
                thread.join(timeout=5.0)
                if thread.is_alive():
                    logger.warning("init_thread_still_running", thread_name=thread.name)


def _register_shutdown_callbacks():
    """Register shutdown callbacks with ShutdownManager."""
    # LIFO order reverses callback registration.
    ShutdownManager.register_callback(_shutdown_watcher)
    ShutdownManager.register_callback(_shutdown_catalog)
    ShutdownManager.register_callback(_shutdown_models)
    ShutdownManager.register_callback(_shutdown_init_threads)


mcp = FastMCP(
    "vault-search-mcp",
    instructions="Local semantic, lexical, and graph search for Markdown vaults",
)

# Middleware order: error handling, then timing.
mcp.add_middleware(SafeErrorMiddleware())
mcp.add_middleware(SafeTimingMiddleware())

# Register tools and resources.
register_search_tools(mcp, _indexer, _searcher)
register_crud_tools(mcp, _indexer, _searcher)
register_graph_tools(mcp, _indexer, _searcher)
register_resources(mcp, _indexer, _searcher)


def _init_catalog() -> bool:
    """Initialize the catalog schema without starting concurrent work."""
    try:
        catalog = get_catalog()
        catalog.initialize()
        logger.info("catalog_initialized")
        return True
    except Exception as error:
        logger.warning(
            "catalog_init_failed",
            error_type=type(error).__name__,
            fallback="list_notes via scan",
        )
        return False


def _start_catalog_reconciliation() -> None:
    """Start catalog reconciliation after index bootstrap."""
    try:
        get_catalog().start_reconciliation()
        logger.info("catalog_reconciliation_started", interval_min=2)
    except Exception as error:
        logger.warning(
            "catalog_reconciliation_failed",
            error_type=type(error).__name__,
        )


def _init_prewarm():
    """Attempt to prewarm LanceDB indexes in the background."""
    try:
        status = _searcher.try_prewarm()
        if status.get("enabled"):
            indices_count = int(status.get("indices_prewarmed", 0))
            duration = status.get("duration_ms", 0)
            logger.info(
                "prewarm_completed",
                indices_count=indices_count,
                duration_ms=round(duration, 1),
            )
        elif status.get("skipped_reason"):
            logger.info("prewarm_skipped", reason=status["skipped_reason"])
    except Exception as error:
        logger.warning(
            "prewarm_failed",
            error_type=type(error).__name__,
            impact="queries will work but may be slower",
        )


def _init_model_warmup():
    """Preload ML models outside the request path."""
    try:
        from vault_search.core.models import ModelManager

        models = ModelManager()
        result = models.warmup()
        embed_ms = result.get("embed_ms", 0)
        rerank_ms = result.get("rerank_ms", 0)
        logger.info("model_warmup_completed", embed_ms=round(embed_ms), rerank_ms=round(rerank_ms))
    except Exception as error:
        logger.warning(
            "model_warmup_failed",
            error_type=type(error).__name__,
            impact="first query will be slow",
        )


def _init_sync_check():
    """Check and synchronize vault files with the index."""
    try:
        stats = _indexer.sync_check(auto_sync=True)
        if stats["new_files"] or stats["modified_files"] or stats["deleted_files"]:
            logger.info(
                "sync_check_completed",
                new=stats["new_files"],
                modified=stats["modified_files"],
                deleted=stats["deleted_files"],
                synced=stats["synced"],
            )
        else:
            logger.info("sync_check_completed", status="in_sync")
    except Exception as error:
        logger.warning("sync_check_failed", error_type=type(error).__name__)


def _init_data_services() -> None:
    """Initialize data-directory consumers in deterministic order."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as error:
        logger.error(
            "data_dir_init_failed",
            error_type=type(error).__name__,
        )
        return

    catalog_ready = _init_catalog()
    _init_sync_check()
    _init_prewarm()
    if catalog_ready:
        _start_catalog_reconciliation()

    try:
        _watcher.start()
        logger.info("watcher_initialized")
    except Exception as error:
        logger.warning(
            "watcher_init_failed",
            error_type=type(error).__name__,
        )


def main():
    """Run the MCP server with the optional watcher."""
    logger.info("server_starting")

    # Initialize graceful shutdown handlers and the atexit fallback.
    ShutdownManager.initialize(timeout=30.0)
    _register_shutdown_callbacks()
    logger.info("shutdown_manager_initialized")

    # Catalog, sync, prewarm, and watcher share one data directory. Serial
    # startup avoids concurrent initialization and mutation.
    data_thread = threading.Thread(
        target=_init_data_services,
        daemon=True,
        name="data-services-init",
    )
    data_thread.start()
    _init_threads.append(data_thread)

    # Warm models in the background, outside the first request.
    warmup_thread = threading.Thread(target=_init_model_warmup, daemon=True, name="model-warmup")
    warmup_thread.start()
    _init_threads.append(warmup_thread)

    # A stdout banner would break the MCP stdio handshake.
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
