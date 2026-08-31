"""
MCP Server para busca semântica e CRUD no vault.

Expõe ferramentas de busca e gerenciamento via protocolo MCP,
permitindo que Claude Code e outros clientes MCP façam busca
semântica de alta qualidade nas notas do vault.

Ferramentas expostas:
- search_vault: busca semântica com reranking
- search_vault_hybrid: busca híbrida (semântica + keyword)
- search_by_folder: busca filtrada por pasta
- vault_stats: estatísticas do índice
- reindex_vault: reindexar todo o vault
- reindex_note: reindexar uma nota específica
- read_note: leitura completa de nota
- get_note_metadata: metadados sem conteúdo
- list_notes: listar notas com filtros
- create_note: criar nova nota
- write_note: sobrescrever/criar nota
- append_note: adicionar conteúdo ao final
- update_frontmatter: atualizar YAML frontmatter
- delete_note: mover nota para a lixeira interna do vault
- move_note: mover/renomear nota
- system_stats: métricas de performance e cache
"""

import os
import threading

# MPS fallback para operações não suportadas no Apple Silicon
# Sem isso, algumas ops do PyTorch podem falhar com device="mps"
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
from vault_search.server.watcher import VaultWatcher
from vault_search.utils.logging import configure_logging, get_logger
from vault_search.utils.shutdown import ShutdownManager, protected_section

# Configurar structured logging (JSON em produção, console colorido em dev)
configure_logging()
logger = get_logger("vault-search-mcp")

# Instâncias compartilhadas (lazy loading interno)
_indexer = VaultIndexer()
_searcher = VaultSearcher()
_watcher = VaultWatcher(_indexer, on_reindex=_searcher.invalidate_cache)

# Threads de inicialização (guardadas para join no shutdown)
_init_threads: list[threading.Thread] = []


def _shutdown_watcher():
    """Para o watcher graciosamente."""
    with protected_section("parando file watcher"):
        _watcher.stop()


def _shutdown_catalog():
    """Para o catálogo graciosamente."""
    with protected_section("parando reconciliação do catálogo"):
        try:
            catalog = get_catalog()
            catalog.stop_reconciliation()
        except Exception:
            pass


def _shutdown_models():
    """Libera modelos ML da memória."""
    with protected_section("liberando modelos ML"):
        from vault_search.core.models import ModelManager

        ModelManager().cleanup()


def _shutdown_init_threads():
    """Aguarda threads de inicialização terminarem."""
    with protected_section("aguardando threads de inicialização"):
        for thread in _init_threads:
            if thread.is_alive():
                logger.debug("waiting_for_init_thread", thread_name=thread.name)
                thread.join(timeout=5.0)
                if thread.is_alive():
                    logger.warning("init_thread_still_running", thread_name=thread.name)


def _register_shutdown_callbacks():
    """Registra callbacks de shutdown no ShutdownManager."""
    # Ordem LIFO: init_threads -> models -> catalog -> watcher (inverso do registro)
    ShutdownManager.register_callback(_shutdown_watcher)
    ShutdownManager.register_callback(_shutdown_catalog)
    ShutdownManager.register_callback(_shutdown_models)
    ShutdownManager.register_callback(_shutdown_init_threads)


mcp = FastMCP(
    "vault-search-mcp",
    instructions="Busca semântica de alta qualidade em vault Obsidian com suporte multilíngue",
)

# Middlewares (ordem: error handling -> timing)
mcp.add_middleware(SafeErrorMiddleware())
mcp.add_middleware(SafeTimingMiddleware())

# Registrar ferramentas e resources
register_search_tools(mcp, _indexer, _searcher)
register_crud_tools(mcp, _indexer, _searcher)
register_graph_tools(mcp, _indexer, _searcher)
register_resources(mcp, _indexer, _searcher)


def _init_catalog() -> bool:
    """Inicializa o schema do catálogo sem iniciar trabalho concorrente."""
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
    """Inicia reconciliação somente após o bootstrap do índice."""
    try:
        get_catalog().start_reconciliation()
        logger.info("catalog_reconciliation_started", interval_min=2)
    except Exception as error:
        logger.warning(
            "catalog_reconciliation_failed",
            error_type=type(error).__name__,
        )


def _init_prewarm():
    """Tenta prewarm dos índices LanceDB em background."""
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
    """Pré-carrega modelos ML para eliminar latência na primeira query."""
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
    """Verifica e sincroniza arquivos do vault com o índice."""
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
    """Inicializa consumidores do data dir em uma sequência determinística."""
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
    """Entry point para o servidor MCP com watcher ativo."""
    logger.info("server_starting")

    # Inicializar graceful shutdown (signal handlers + atexit)
    ShutdownManager.initialize(timeout=30.0)
    _register_shutdown_callbacks()
    logger.info("shutdown_manager_initialized")

    # Catálogo, sync, prewarm e watcher compartilham o mesmo data dir. A ordem
    # serial evita abertura e mutação concorrentes durante o bootstrap.
    data_thread = threading.Thread(
        target=_init_data_services,
        daemon=True,
        name="data-services-init",
    )
    data_thread.start()
    _init_threads.append(data_thread)

    # Model warmup em background (carrega modelos para primeira query ser rápida)
    warmup_thread = threading.Thread(target=_init_model_warmup, daemon=True, name="model-warmup")
    warmup_thread.start()
    _init_threads.append(warmup_thread)

    # Banner usa stdout e pode quebrar handshake MCP em transporte stdio.
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
