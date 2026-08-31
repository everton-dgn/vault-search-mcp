"""
Ferramentas MCP de busca e indexação.
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

# Guardrail: Limite máximo de termos para excluir (previne DoS)
MAX_EXCLUDE_TERMS = 20

# Timestamp de início do servidor para uptime
_SERVER_START_TIME = time.time()


class BacklinkResult(TypedDict):
    """Backlink deduplicado retornado ao cliente."""

    path: str
    title: str
    link_type: str
    link_target: str
    context: NotRequired[str]


class BrokenNoteCount(TypedDict):
    """Contagem intermediária de links quebrados por nota."""

    path: str
    title: str
    count: int


class BrokenLinkDetail(TypedDict):
    """Detalhe público de um link quebrado."""

    target: str
    type: str
    context: str


class OrphanNote(TypedDict):
    """Nota órfã mantida durante a paginação global."""

    path: str
    title: str
    folder: str
    modified_at: str


class BacklinkRank(TypedDict):
    """Item do ranking de backlinks."""

    path: str
    backlinks: int


class OutlinkRank(TypedDict):
    """Item do ranking de outlinks."""

    path: str
    outlinks: int


class RecentNote(TypedDict):
    """Nota recente com idade calculada."""

    path: str
    title: str
    modified_at: str
    folder: str
    days_ago: int


class TaggedNote(TypedDict):
    """Nota agrupada por tags."""

    path: str
    title: str
    folder: str
    tags: list[str]
    modified_at: str


type FolderTree = dict[str, int | FolderTree]


def _iter_query_rows(query: Any, batch_size: int = 1000) -> Iterator[dict[str, Any]]:
    """Percorre uma consulta LanceDB em lotes, sem truncar o conjunto."""
    for batch in query.limit(None).to_batches(batch_size=batch_size):
        yield from batch.to_pylist()


def register_search_tools(mcp, indexer, searcher):
    """
    Registra ferramentas de busca no servidor MCP.

    Parâmetros:
        mcp: instância do FastMCP
        indexer: instância do VaultIndexer
        searcher: instância do VaultSearcher
    """

    @mcp.tool()
    async def search_vault(
        query: str,
        top_k: int = 10,
        ctx: Context | None = None,
    ) -> list[dict[str, object]] | str:
        """
        Busca semântica nas notas do vault com reranking para máxima precisão.

        Fluxo: embedding da query → busca vetorial → reranking com cross-encoder.
        Funciona em qualquer idioma (cross-lingual).

        Parâmetros:
            query: texto de busca (qualquer idioma)
            top_k: quantidade de resultados (padrão: 10, máximo: 100)

        Retorna:
            Lista de resultados com nota, seção, texto e score de relevância.
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
        Busca híbrida: combina busca semântica com busca por palavras-chave.

        Melhor para queries com termos técnicos, nomes próprios ou siglas
        que a busca vetorial pura pode perder.

        Parâmetros:
            query: texto de busca
            top_k: quantidade de resultados (padrão: 10, máximo: 100)

        Retorna:
            Lista de resultados com nota, seção, texto e score.
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
        Busca semântica filtrada por pasta do vault.

        Útil para restringir a busca a uma área específica do vault.

        Parâmetros:
            query: texto de busca
            folder: pasta para filtrar (ex: 'projetos', 'estudos/python')
            top_k: quantidade de resultados (padrão: 10, máximo: 100)

        Retorna:
            Lista de resultados apenas da pasta especificada.
        """
        if not folder or not folder.strip():
            return "Erro: folder não pode ser vazio."
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
        Retorna estatísticas do índice de busca.

        Retorna:
            Dict com total de chunks, notas únicas e data da última modificação.
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
        Reindexar todo o vault do zero.

        Útil após grandes reorganizações ou para reconstruir o índice.
        Pode demorar alguns minutos dependendo do tamanho do vault.

        Parâmetros:
            dry_run: se True, retorna preview sem executar (default: False)
            require_daemon: se True, falha se daemon não disponível (default: False)
                           Também via env VAULT_SEARCH_REQUIRE_DAEMON=1

        Retorna:
            Estatísticas: total de notas, chunks gerados e duração.
            Se dry_run=True, retorna contagens observadas sem modificar o índice.
        """
        import os

        from vault_search.core.models import ModelManager

        # Verificar se daemon é obrigatório (flag ou env)
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
                    message="O daemon obrigatório está indisponível.",
                )

        msg = f"reindex_vault: {'dry_run' if dry_run else 'iniciando reindexação completa'}"
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
        Reindexar uma nota específica (atualização incremental).

        Parâmetros:
            path: caminho relativo da nota no vault (ex: 'pasta/minha-nota.md')

        Retorna:
            Status da operação e quantidade de chunks indexados.
        """
        if not path or not path.strip():
            return "Erro: path não pode ser vazio."
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
        Retorna métricas de performance e estatísticas do sistema.

        Inclui:
        - Latências p50/p95 das operações (list_notes, read_note, get_note_metadata)
        - Estatísticas do cache de metadados (tamanho, hit rate)
        - Estatísticas do catálogo SQLite (total de notas, por extensão)
        - Estatísticas do índice vetorial

        Parâmetros:
            reset: se True, reseta as métricas após retornar (default: False)

        Retorna:
            Dict com métricas de performance e estatísticas do sistema.
        """
        logger.info(f"system_stats (reset={reset})")

        metrics = get_metrics()
        cache_stats = get_metadata_cache().stats()
        index_stats = indexer.get_stats()

        # Catálogo SQLite (pode não estar inicializado)
        catalog_stats: object
        try:
            catalog_stats = get_catalog().stats()
        except Exception:
            catalog_stats = {"status": "não inicializado"}

        # Cache de embeddings de query
        embedding_cache_stats = searcher.get_embedding_cache_stats()

        # Prewarm status
        prewarm_status = searcher.get_prewarm_status()

        result = {
            "performance": {
                "operations": metrics,
                "description": "Latências p50/p95 em milissegundos",
            },
            "cache": {
                "metadata_cache": cache_stats,
                "embedding_cache": embedding_cache_stats,
                "description": "Caches LRU (metadados de notas + embeddings de query)",
            },
            "catalog": {
                "notes_catalog": catalog_stats,
                "description": "Catálogo SQLite para list_notes() rápido",
            },
            "index": index_stats,
            "prewarm": {
                "status": prewarm_status,
                "description": "Índices carregados na RAM para baixa latência",
            },
        }

        if reset:
            reset_metrics()
            logger.info("Métricas resetadas")

        return result

    @mcp.tool()
    def sync_vault(
        dry_run: bool = False,
        require_daemon: bool = False,
    ) -> dict[str, object]:
        """
        Sincroniza arquivos do vault com o índice.

        Detecta e sincroniza:
        - Arquivos novos (no vault mas não no índice)
        - Arquivos modificados (alterados desde última indexação)
        - Arquivos deletados (no índice mas não mais no vault)

        Útil quando arquivos foram adicionados/modificados com o servidor parado.

        Parâmetros:
            dry_run: se True, apenas retorna o que seria sincronizado sem executar
            require_daemon: se True, falha se daemon não disponível (default: False)
                           Também via env VAULT_SEARCH_REQUIRE_DAEMON=1

        Retorna:
            Dict com vault_files, indexed_files, new_files, modified_files,
            deleted_files e synced counts.
        """
        import os

        from vault_search.core.models import ModelManager

        # Verificar se daemon é obrigatório (flag ou env)
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
                    message="O daemon obrigatório está indisponível.",
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
        Compacta o índice LanceDB para reduzir fragmentação.

        Útil após muitas operações incrementais (edições, criações, deleções).
        Merge arquivos pequenos e remove versões antigas.

        Retorna:
            Estatísticas da compactação (arquivos compactados, versões limpas).
        """
        logger.info("compact_index: iniciando compactação")
        try:
            stats = indexer.compact()
            return stats
        except Exception as e:
            return public_error(logger, "compact_index", e)

    @mcp.tool()
    def health_check() -> dict[str, object]:
        """
        Verifica a saúde do sistema para monitoramento.

        Útil para load balancers, health probes, e monitoramento externo.
        Verifica: índice, catálogo, modelos carregados, alertas de latência.

        Retorna:
            Dict com status geral e detalhes de cada componente.
        """
        logger.info("health_check")

        # Status dos componentes
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

        # Alertas de latência e cache
        latency_alerts = check_latency_health()
        cache_alerts = check_cache_health()
        all_alerts = latency_alerts + cache_alerts

        if daemon_required and not models_status["using_daemon"]:
            all_alerts.append(
                {
                    "type": "daemon_required_unavailable",
                    "severity": "critical",
                    "message": "Daemon obrigatório indisponível; fallback local desabilitado.",
                }
            )

        # Determinar status geral
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
        Encontra notas similares a uma nota específica.

        Calcula o embedding médio de todos os chunks da nota
        e busca outras notas com conteúdo semanticamente similar.
        Útil para descobrir notas relacionadas ou duplicadas.

        Parâmetros:
            path: caminho relativo da nota no vault (ex: 'projetos/meu-projeto.md')
            top_k: quantidade de notas similares (padrão: 5, máximo: 20)

        Retorna:
            Lista de notas similares com score de similaridade.
        """
        # Guardrail: operação de leitura computacionalmente cara

        if not path or not path.strip():
            return "Erro: path não pode ser vazio."

        path = path.strip()
        top_k = max(1, min(top_k, 20))  # Limitar entre 1 e 20

        logger.info("find_similar_notes top_k=%d", top_k)

        try:
            return searcher.find_similar_notes(path, top_k)
        except ValueError as e:
            return public_error(
                logger,
                "find_similar_notes",
                e,
                code="invalid_request",
                message="A nota não existe ou o path é inválido.",
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
        Encontra grupos de notas duplicadas ou muito similares no vault.

        Varre o vault comparando embeddings semânticos das notas e agrupa
        aquelas que excedem o threshold de similaridade. Útil para:
        - Identificar conteúdo duplicado
        - Encontrar notas que podem ser mescladas
        - Limpeza e organização do vault

        Parâmetros:
            threshold: similaridade mínima (0.0-1.0, padrão: 0.90)
                      0.90 = 90% similar (captura duplicatas óbvias)
                      0.80 = mais permissivo, mais resultados
                      0.95 = apenas duplicatas quase idênticas
            max_notes: máximo de notas a processar (padrão: 500)
            folder: opcional, restringir a uma pasta específica

        Retorna:
            Lista de grupos de duplicatas, cada grupo contendo:
            - notes: lista de notas no grupo (path, title, folder)
            - count: número de notas no grupo
            - avg_similarity: similaridade média do grupo

        Observação:
            Operação computacionalmente cara. Para vaults grandes,
            considere restringir por folder ou aumentar threshold.
        """
        # Validação
        threshold = max(0.5, min(threshold, 0.99))  # Entre 0.5 e 0.99
        max_notes = max(10, min(max_notes, 1000))  # Entre 10 e 1000

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
        Busca semântica avançada com filtros (faceted search).

        Combina busca vetorial com filtros estruturados para resultados precisos.
        Todos os filtros são opcionais e combinados com AND.

        Parâmetros:
            query: texto de busca semântica
            top_k: quantidade de resultados (padrão: 10, máximo: 100)
            tags: lista de tags para filtrar (OR entre tags)
            folder: pasta para filtrar (inclui subpastas)
            extension: extensão de arquivo (ex: 'md', 'canvas', 'pdf')
            date_range: período predefinido - 'today', 'week', 'month', 'year'
            date_from: data início ISO (ex: '2024-01-01') - ignorado se date_range
            date_to: data fim ISO (ex: '2024-12-31') - ignorado se date_range
            status: status da nota (draft, review, published, archived)
            note_type: tipo da nota (daily, weekly, monthly, yearly, meeting, idea, task)
            category: categoria (work, personal, reference, project)
            project: nome do projeto associado
            exclude: lista de termos para EXCLUIR dos resultados
            highlight: se True, destaca termos da query no texto com **marcadores**

        Retorna:
            Lista de resultados com nota, seção, texto, score e metadata.

        Exemplos:
            search_advanced("python", exclude=["django", "flask"])
            search_advanced("API REST", highlight=True)
            search_advanced("reunião", note_type="meeting", highlight=True)
        """
        # Guardrail: operação de leitura

        # Validar query
        if not query or not query.strip():
            return "Erro: query não pode ser vazia."

        # Guardrail: Truncar query para evitar DoS com strings enormes
        query = truncate_query(query.strip())
        top_k = clamp_top_k(top_k)

        # Construir date_range dict se datas customizadas
        effective_date_range: str | dict[str, str] | None = None
        if date_range:
            effective_date_range = date_range.strip().lower()
        elif date_from or date_to:
            effective_date_range = {}
            if date_from:
                effective_date_range["from"] = date_from.strip()
            if date_to:
                effective_date_range["to"] = date_to.strip()

        # Normalizar parâmetros
        normalized_tags = None
        if tags:
            normalized_tags = [t.strip() for t in tags if t and t.strip()]
            if not normalized_tags:
                normalized_tags = None

        normalized_folder = folder.strip() if folder and folder.strip() else None

        # Validação: Validar extensão contra whitelist
        normalized_extension = None
        if extension and extension.strip():
            ext = extension.strip().lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            if ext not in INDEXABLE_EXTENSIONS:
                valid_exts = ", ".join(sorted(INDEXABLE_EXTENSIONS))
                return f"Erro: extensão '{ext}' inválida. Válidas: {valid_exts}"
            normalized_extension = ext

        normalized_status = status.strip().lower() if status and status.strip() else None
        normalized_note_type = (
            note_type.strip().lower() if note_type and note_type.strip() else None
        )
        normalized_category = category.strip().lower() if category and category.strip() else None
        normalized_project = project.strip() if project and project.strip() else None

        # Normalizar exclude com limite operacional
        normalized_exclude = None
        if exclude:
            normalized_exclude = [t.strip() for t in exclude if t and t.strip()]
            if not normalized_exclude:
                normalized_exclude = None
            elif len(normalized_exclude) > MAX_EXCLUDE_TERMS:
                # Validação: Limitar tamanho da lista exclude (previne DoS)
                logger.warning(
                    f"Lista exclude truncada de {len(normalized_exclude)} para {MAX_EXCLUDE_TERMS} termos"
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
        Executa benchmark de busca para medir latência local.

        Útil para comparar performance antes/depois de otimizações
        ou validar hardware.

        Parâmetros:
            query: texto de busca para benchmark (padrão: "test")
            iterations: número de iterações (padrão: 10, máximo: 100)

        Retorna:
            Estatísticas de latência: mean, min, max, p50, p95 (em ms).
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

        # Evitar benchmark "falso" quando todas as execuções falham
        if error_types and len(error_types) == iterations:
            return {
                "query_length": len(query),
                "iterations": iterations,
                "errors": len(error_types),
                "sample_error_type": error_types[0],
                "hint": "Verifique se o daemon está rodando (ou permita fallback local).",
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
        Retorna status do índice vetorial ANN (Approximate Nearest Neighbor).

        O índice vetorial acelera buscas em vaults grandes (>5k notas).
        É criado automaticamente quando o vault atinge o threshold configurado.

        Retorna:
            Dict com:
            - exists: se o índice existe
            - threshold: número mínimo de chunks para criar índice
            - total_chunks: chunks atuais no índice
            - would_create: se o índice seria criado agora (threshold atingido, não existe)
            - auto_create_enabled: se criação automática está habilitada
        """
        logger.info("vector_index_status")
        return indexer.get_vector_index_status()

    @mcp.tool()
    def get_backlinks(
        path: str,
        include_context: bool = True,
    ) -> list[BacklinkResult] | str:
        """
        Encontra notas que linkam para uma nota específica (backlinks).

        Usa índice de links para busca O(1) ao invés de ler arquivos.
        Recurso essencial para navegação em knowledge bases tipo Obsidian.

        Parâmetros:
            path: caminho relativo da nota alvo (ex: 'projetos/meu-projeto.md')
            include_context: incluir trecho onde o link aparece (padrão: True)

        Retorna:
            Lista de notas que linkam para a nota alvo, com:
            - path: caminho da nota que contém o link
            - title: título da nota
            - link_type: tipo do link ('wikilink' ou 'markdown')
            - link_target: target original do link
            - context: trecho do texto onde o link aparece (se include_context=True)
        """
        if not path or not path.strip():
            return "Erro: path não pode ser vazio."

        path = path.strip()
        logger.info("get_backlinks include_context=%s", include_context)

        try:
            # Normalizar path alvo para matching
            target_normalized = normalize_link_target(path)
            target_stem = normalize_link_target(Path(path).stem)

            # Query no índice de links
            links_table = indexer._ensure_links_table()

            # Escapar valores para SQL
            path_escaped = escape_sql_string(path)
            target_norm_escaped = escape_sql_string(target_normalized)
            target_stem_escaped = escape_sql_string(target_stem)

            # Buscar links que apontam para esta nota
            # Considerar: to_note_path resolvido OU link_target_normalized match
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

            # Deduplicar por nota (uma nota pode ter vários links para o mesmo alvo)
            seen_notes = set()
            backlinks: list[BacklinkResult] = []

            for row in results:
                note_path = row["from_note_path"]
                # Não incluir a própria nota e evitar duplicatas
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
        """Extrai contexto ao redor de um link no texto."""
        idx = content.find(link_marker)
        if idx == -1:
            return ""

        start = max(0, idx - context_chars)
        end = min(len(content), idx + len(link_marker) + context_chars)

        # Ajustar para não cortar palavras
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

        # Remover quebras de linha para contexto mais limpo
        return " ".join(context.split())

    @mcp.tool()
    def get_outlinks(path: str) -> dict[str, object] | str:
        """
        Lista todos os links saindo de uma nota específica (outlinks).

        Usa índice de links para consistência com dados indexados.
        Complemento do get_backlinks para navegação bidirecional.

        Parâmetros:
            path: caminho relativo da nota (ex: 'projetos/meu-projeto.md')

        Retorna:
            Dict com:
            - wikilinks: lista de wikilinks com target, resolved, resolved_path
            - markdown_links: lista de markdown links
            - embeds: lista de embeds
            - external: lista de URLs externas
            - total: total de links
            - broken_count: quantidade de links quebrados
        """
        if not path or not path.strip():
            return "Erro: path não pode ser vazio."

        path = path.strip()
        logger.info("get_outlinks requested")

        try:
            links_table = indexer._ensure_links_table()

            # Query todos os links desta nota
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

            # Agrupar por tipo
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

            # Contar links quebrados (não resolvidos, exceto externos e embeds de imagens)
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
        Encontra links que apontam para notas inexistentes.

        Útil para manutenção e limpeza do vault.
        Links quebrados são aqueles onde is_resolved=false no índice.

        Parâmetros:
            folder: filtrar por pasta (opcional)
            limit: máximo de notas retornadas (padrão: 100, máximo: 500)

        Retorna:
            Dict com:
            - total_broken_links: total de links quebrados
            - notes_with_broken_links: quantidade de notas afetadas
            - notes: lista de notas com seus links quebrados
        """
        limit = max(1, min(limit, 500))
        logger.info("find_broken_links folder_filter=%s limit=%d", bool(folder), limit)

        try:
            normalized_folder = folder.strip() if folder else None
            if normalized_folder and not validate_relative_path(normalized_folder):
                raise ValueError("Folder inválido ou fora do vault")
            links_table = indexer._ensure_links_table()

            # Buscar links não resolvidos (exceto externos)
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
        Encontra notas sem nenhum backlink (isoladas no grafo).

        Útil para identificar conteúdo desconectado que pode
        precisar de mais links ou ser arquivado.

        Parâmetros:
            folder: filtrar por pasta (opcional)
            limit: máximo de notas retornadas (padrão: 100, máximo: 500)

        Retorna:
            Dict com:
            - total_notes: total de notas no vault/pasta
            - total_orphans: quantidade de notas órfãs
            - orphan_percentage: percentual de órfãs
            - notes: lista de notas órfãs
        """
        limit = max(1, min(limit, 500))
        logger.info("find_orphan_notes folder_filter=%s limit=%d", bool(folder), limit)

        try:
            normalized_folder = folder.strip() if folder else None
            if normalized_folder and not validate_relative_path(normalized_folder):
                raise ValueError("Folder inválido ou fora do vault")
            catalog = get_catalog()
            if not catalog.is_available():
                return "Erro: catálogo não disponível. Execute reindex_vault primeiro."

            # Obter notas que SÃO linkadas (têm backlinks)
            links_table = indexer._ensure_links_table()

            # Set de paths linkados
            linked_paths: set[str] = set()
            linked_normalized: set[str] = set()
            linked_query = links_table.search().select(["to_note_path", "link_target_normalized"])
            for row in _iter_query_rows(linked_query):
                if row["to_note_path"]:
                    linked_paths.add(row["to_note_path"])
                if row["link_target_normalized"]:
                    linked_normalized.add(row["link_target_normalized"])

            # O catálogo entrega notas da mais nova para a mais antiga. O deque
            # retém só as ``limit`` órfãs mais antigas enquanto o total é contado.
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
        Estatísticas de links do vault.

        Mostra totais, notas mais referenciadas (hub notes) e
        notas com mais links saindo.

        Parâmetros:
            limit: máximo de notas em cada ranking (padrão: 50, máximo: 200)

        Retorna:
            Dict com:
            - total_links: total de links indexados
            - total_resolved: links resolvidos
            - total_broken: links quebrados
            - total_external: URLs externas
            - resolution_rate: taxa de resolução (%)
            - most_referenced: notas com mais backlinks
            - most_outlinks: notas com mais links saindo
        """
        limit = max(1, min(limit, 200))
        logger.info(f"link_stats: limit={limit}")

        try:
            links_table = indexer._ensure_links_table()

            # Contar total de links
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

            # Contar backlinks por nota (mais referenciadas)
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

            # Contar outlinks por nota
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

            # Taxa de resolução (excluindo externos)
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
        Retorna notas modificadas recentemente.

        Útil para retomar trabalho ou entender contexto atual.
        Ordenado por data de modificação (mais recente primeiro).

        Parâmetros:
            days: janela de tempo em dias (padrão: 7, máximo: 365)
            limit: máximo de notas retornadas (padrão: 20, máximo: 100)
            folder: filtrar por pasta específica (opcional)

        Retorna:
            Lista de notas com path, title, modified_at, folder e days_ago.
        """
        from datetime import datetime, timedelta

        # Guardrail: operação potencialmente cara

        # Validar parâmetros
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
                return "Erro: catálogo não disponível. Execute reindex_vault primeiro."

            # Buscar notas do catálogo
            notes, _ = catalog.list_notes(
                folder=folder.strip() if folder else None,
                limit=limit * 2,  # Pegar mais para filtrar por data
            )

            # Filtrar por data
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

            # Ordenar por data (mais recente primeiro) e limitar
            recent.sort(key=lambda x: x["modified_at"], reverse=True)
            recent = recent[:limit]

            logger.info(
                f"get_recent_notes: encontradas {len(recent)} notas nos últimos {days} dias"
            )
            return recent

        except Exception as e:
            return public_error(logger, "get_recent_notes", e)

    @mcp.tool()
    def tag_stats(
        limit: int = 50,
        folder: str | None = None,
    ) -> dict[str, object] | str:
        """
        Retorna estatísticas de tags do vault (tag cloud).

        Mostra quais tags existem e quantas notas usam cada uma.
        Útil para descobrir temas, navegar por categorias e organizar.

        Parâmetros:
            limit: máximo de tags retornadas (padrão: 50, máximo: 500)
            folder: filtrar por pasta específica (opcional)

        Retorna:
            Dict com total_tags, total_notes_with_tags e lista de tags ordenada por frequência.
        """
        from collections import Counter

        # Guardrail: operação potencialmente cara

        limit = max(1, min(limit, 500))
        folder_filter = folder.strip() if folder else None

        logger.info("tag_stats limit=%d folder_filter=%s", limit, bool(folder_filter))

        try:
            # Obter tabela LanceDB
            table = indexer._ensure_table()
            total_rows = table.count_rows()

            if total_rows == 0:
                return {
                    "total_tags": 0,
                    "total_notes_with_tags": 0,
                    "tags": [],
                }

            # Query LanceDB: buscar note_path + tags únicos por nota
            # Usar PyArrow para eficiência
            query = table.search().select(["note_path", "tags", "folder"])

            if folder_filter:
                # Filtro de pasta (exato ou subpastas)
                query = query.where(
                    f"folder = '{folder_filter}' OR folder LIKE '{folder_filter}/%'"
                )

            arrow_table = query.limit(total_rows).to_arrow()

            # Extrair dados únicos por nota (evitar contar mesma tag várias vezes por nota)
            note_tags: dict[str, set[str]] = {}

            for i in range(arrow_table.num_rows):
                note_path = arrow_table.column("note_path")[i].as_py()
                tags_str = arrow_table.column("tags")[i].as_py()

                if note_path not in note_tags:
                    note_tags[note_path] = set()

                if tags_str:
                    # Tags são comma-separated
                    for tag in tags_str.split(","):
                        tag = tag.strip()
                        if tag:
                            note_tags[note_path].add(tag)

            # Contar frequência de cada tag (quantas notas a usam)
            tag_counter: Counter[str] = Counter()
            notes_with_tags = 0

            for tags in note_tags.values():
                if tags:
                    notes_with_tags += 1
                    tag_counter.update(tags)

            # Ordenar por frequência e limitar
            top_tags = [
                {"tag": tag, "count": count} for tag, count in tag_counter.most_common(limit)
            ]

            result = {
                "total_tags": len(tag_counter),
                "total_notes_with_tags": notes_with_tags,
                "tags": top_tags,
            }

            logger.info(
                f"tag_stats: {result['total_tags']} tags únicas, {notes_with_tags} notas com tags"
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
        Retorna a estrutura de pastas do vault como árvore hierárquica.

        Útil para entender organização, descobrir pastas e navegar.
        Usa o catálogo SQLite (eficiente, não escaneia filesystem).

        Parâmetros:
            include_counts: incluir contagem de notas por pasta (padrão: True)
            max_depth: profundidade máxima da árvore, limitada pela configuração

        Retorna:
            Dict com total_folders, total_notes e tree hierárquico.
        """
        from pathlib import PurePosixPath

        # Guardrail: operação potencialmente cara

        max_depth = max(1, min(max_depth, FOLDER_TREE_MAX_DEPTH_LIMIT))

        logger.info(f"folder_tree: include_counts={include_counts}, max_depth={max_depth}")

        def insert_path(
            tree: FolderTree,
            path_parts: tuple[str, ...],
            count: int,
            depth: int = 0,
        ) -> None:
            """Insere um path na árvore recursivamente."""
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

            # Acumular contagem neste nível
            if include_counts:
                if not remaining or depth + 1 >= max_depth:
                    # Pasta folha ou truncada: adicionar contagem aqui
                    current_count = child.get("_count", 0)
                    child["_count"] = (
                        current_count if isinstance(current_count, int) else 0
                    ) + count

            # Recursão para o próximo nível
            if remaining and depth + 1 < max_depth:
                insert_path(child, remaining, count, depth + 1)

        try:
            catalog = get_catalog()
            if not catalog.is_available():
                return "Erro: catálogo não disponível. Execute reindex_vault primeiro."

            # Query direta no SQLite para eficiência
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

            # Construir árvore recursivamente
            tree: FolderTree = {}
            total_notes = 0
            all_folders: set[str] = set()

            for row in rows:
                folder = row["folder"]
                count = row["count"]
                total_notes += count

                if not folder:
                    # Notas na raiz
                    if include_counts:
                        root_count = tree.get("_count", 0)
                        tree["_count"] = (root_count if isinstance(root_count, int) else 0) + count
                    continue

                # Usar PurePosixPath para parsing robusto de paths
                path = PurePosixPath(folder)
                parts = path.parts[:max_depth]

                # Registrar todas as pastas intermediárias
                for i in range(1, len(parts) + 1):
                    all_folders.add("/".join(parts[:i]))

                # Inserir na árvore recursivamente
                insert_path(tree, parts, count)

            result = {
                "total_folders": len(all_folders),
                "total_notes": total_notes,
                "tree": tree,
            }

            logger.info(f"folder_tree: {result['total_folders']} pastas, {total_notes} notas")
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
        Busca notas por tags específicas (busca exata, sem semântica).

        Complemento ao tag_stats: descobrir tags disponíveis, depois buscar.
        Mais rápido que busca semântica para filtros por categoria.

        Parâmetros:
            tags: lista de tags a buscar (ex: ["projeto", "2024"])
            match_all: True = nota deve ter TODAS as tags (AND),
                      False = nota deve ter QUALQUER tag (OR, padrão)
            limit: máximo de notas retornadas (padrão: 50, máximo: 200)

        Retorna:
            Lista de notas com path, title, folder, tags e modified_at.
        """
        # Guardrail: operação potencialmente cara

        # Validação
        if not tags:
            return "Erro: lista de tags não pode ser vazia."

        # Normalizar e limpar tags
        clean_tags = []
        for tag in tags:
            if isinstance(tag, str):
                t = tag.strip().lower()
                if t:
                    clean_tags.append(t)

        if not clean_tags:
            return "Erro: nenhuma tag válida fornecida."

        # Limitar quantidade de tags para evitar queries muito complexas
        if len(clean_tags) > 20:
            clean_tags = clean_tags[:20]
            logger.warning("search_by_tags: lista truncada para 20 tags")

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

            # Query otimizada: buscar apenas colunas necessárias
            # Usar filtro SQL para reduzir dados transferidos
            query = table.search().select(
                ["note_path", "note_title", "folder", "tags", "modified_at"]
            )

            # Construir filtro WHERE com LIKE para cada tag
            # Tags são armazenadas como "tag1, tag2, tag3"
            conditions = []
            for tag in clean_tags:
                # Escapar caracteres especiais SQL
                escaped_tag = tag.replace("'", "''")
                # Match: tag exata (não substring de outra tag)
                # Casos: início ", tag", meio ", tag,", fim ", tag" ou única "tag"
                conditions.append(
                    f"(tags = '{escaped_tag}' OR "
                    f"tags LIKE '{escaped_tag}, %' OR "
                    f"tags LIKE '%, {escaped_tag}' OR "
                    f"tags LIKE '%, {escaped_tag}, %')"
                )

            # OR entre tags (filtro inicial)
            where_clause = " OR ".join(conditions)
            query = query.where(where_clause)

            # Limitar resultados brutos (chunks)
            arrow_table = query.limit(total_rows).to_arrow()

            # Agrupar por nota (evitar duplicatas de chunks)
            notes_map: dict[str, TaggedNote] = {}

            for i in range(arrow_table.num_rows):
                note_path = arrow_table.column("note_path")[i].as_py()

                if note_path in notes_map:
                    continue  # Já processada

                tags_str = arrow_table.column("tags")[i].as_py() or ""
                note_tags = {t.strip().lower() for t in tags_str.split(",") if t.strip()}

                # Para match_all, verificar se nota tem TODAS as tags
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

            # Ordenar por data (mais recente primeiro) e limitar
            results = sorted(
                notes_map.values(), key=lambda x: x["modified_at"] or "", reverse=True
            )[:limit]

            logger.info(f"search_by_tags: encontradas {len(results)} notas")
            return results

        except Exception as e:
            return public_error(logger, "search_by_tags", e)

    @mcp.tool()
    def random_note(
        folder: str | None = None,
        extension: str | None = None,
    ) -> dict[str, object] | str:
        """
        Retorna uma nota aleatória do vault.

        Útil para redescoberta, serendipidade e exploração.
        Usa SQLite ORDER BY RANDOM() para eficiência.

        Parâmetros:
            folder: filtrar por pasta específica (opcional)
            extension: filtrar por extensão (ex: ".md", ".pdf") (opcional)

        Retorna:
            Dict com path, title, folder, modified_at e size_bytes.
        """
        from vault_search.config.search import INDEXABLE_EXTENSIONS

        # Guardrail: operação potencialmente cara

        # Normalizar parâmetros
        folder_filter = folder.strip() if folder and folder.strip() else None
        ext_filter = None
        if extension and extension.strip():
            ext = extension.strip().lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            if ext not in INDEXABLE_EXTENSIONS:
                valid_exts = ", ".join(sorted(INDEXABLE_EXTENSIONS))
                return f"Erro: extensão '{ext}' inválida. Válidas: {valid_exts}"
            ext_filter = ext

        logger.info(
            "random_note folder_filter=%s extension_filter=%s",
            bool(folder_filter),
            bool(ext_filter),
        )

        try:
            catalog = get_catalog()
            if not catalog.is_available():
                return "Erro: catálogo não disponível. Execute reindex_vault primeiro."

            # Query direta com ORDER BY RANDOM() LIMIT 1
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
                msg = "Nenhuma nota encontrada"
                if folder_filter:
                    msg += f" na pasta '{folder_filter}'"
                if ext_filter:
                    msg += f" com extensão '{ext_filter}'"
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
        Retorna informações sobre a daily note de uma data específica.

        Daily notes seguem o padrão Obsidian: YYYY-MM-DD.md na pasta configurada.
        Útil para verificar existência, obter contexto temporal ou integrar workflows.

        Parâmetros:
            date: data no formato ISO (ex: "2024-01-15"), None = hoje
            folder: pasta das daily notes (padrão: "daily")

        Retorna:
            Dict com exists, path e metadados se existe, ou expected_path se não.
        """
        from datetime import date as date_type
        from datetime import datetime

        # Guardrail: operação potencialmente cara

        # Determinar data
        if date:
            date = date.strip()
            try:
                # Validar formato ISO
                parsed_date = datetime.fromisoformat(date).date()
            except ValueError:
                return f"Erro: formato de data inválido '{date}'. Use ISO: YYYY-MM-DD"
        else:
            parsed_date = date_type.today()

        date_str = parsed_date.isoformat()  # YYYY-MM-DD

        # Normalizar pasta
        folder = folder.strip() if folder and folder.strip() else "daily"

        # Construir path esperado
        expected_filename = f"{date_str}.md"
        expected_path = f"{folder}/{expected_filename}" if folder else expected_filename

        logger.info("daily_note requested")

        try:
            file_path = resolve_path(expected_path)
            expected_path = file_path.relative_to(resolve_internal_path()).as_posix()

            # Verificar no catálogo primeiro (mais eficiente)
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

            # Fallback: verificar filesystem diretamente
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

            # Não existe
            return {
                "exists": False,
                "expected_path": expected_path,
                "date": date_str,
                "folder": folder,
            }

        except Exception as e:
            return public_error(logger, "daily_note", e)
