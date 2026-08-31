"""
Ferramentas MCP de CRUD para notas.
"""

import logging

from vault_search.config.paths import VAULT_PATH
from vault_search.core.scanner import scan_vault
from vault_search.crud.delete import delete_note as crud_delete_note
from vault_search.crud.delete import move_note as crud_move_note
from vault_search.crud.read import (
    get_note_metadata as crud_get_note_metadata,
)
from vault_search.crud.read import (
    list_notes as crud_list_notes,
)
from vault_search.crud.read import (
    read_note as crud_read_note,
)
from vault_search.crud.validation import (
    get_frontmatter_validator,
    resolve_path,
)
from vault_search.crud.write import (
    append_note as crud_append_note,
)
from vault_search.crud.write import (
    create_note as crud_create_note,
)
from vault_search.crud.write import (
    ensure_note_id as crud_ensure_note_id,
)
from vault_search.crud.write import (
    is_ai_enrichment_enabled,
)
from vault_search.crud.write import (
    update_frontmatter as crud_update_frontmatter,
)
from vault_search.crud.write import (
    write_note as crud_write_note,
)
from vault_search.parsers.frontmatter import read_frontmatter_only
from vault_search.server.errors import public_error
from vault_search.server.frontmatter_jobs import FrontmatterEnrichmentJobManager
from vault_search.server.reindex_queue import ReindexQueue
from vault_search.utils.shutdown import ShutdownManager

logger = logging.getLogger("vault-search-mcp")

type ToolResult = dict[str, object] | str
type FrontmatterInput = dict[str, object]


def _safe_reindex(
    scheduler: ReindexQueue,
    path: str,
    result: dict[str, object],
) -> dict[str, object]:
    """
    Reindexar nota em background, sem bloquear a operação.

    A escrita já foi concluída - disparamos o reindex em thread separada
    para não bloquear o retorno. Se falhar, o file watcher pegará depois.
    """
    result["reindex_status"] = scheduler.enqueue(path)
    return result


def register_crud_tools(mcp, indexer, searcher):
    """
    Registra ferramentas CRUD no servidor MCP.

    Parâmetros:
        mcp: instância do FastMCP
        indexer: instância do VaultIndexer
        searcher: instância do VaultSearcher
    """
    enrichment_jobs = FrontmatterEnrichmentJobManager(indexer, searcher, logger)
    reindex_queue = ReindexQueue(indexer, searcher, logger)

    def stop_enrichment_jobs() -> None:
        enrichment_jobs.stop()

    ShutdownManager.register_callback(stop_enrichment_jobs)
    ShutdownManager.register_callback(reindex_queue.stop)

    @mcp.tool()
    def read_note(path: str) -> ToolResult:
        """
        Lê conteúdo completo de uma nota markdown com frontmatter parseado.

        Apenas .md é suportado. Para PDFs/Canvas, use search_vault.

        Parâmetros:
            path: caminho relativo no vault (ex: 'pasta/nota.md')

        Retorna:
            Dict com content, frontmatter, body, tags, title, folder, modified_at, size_bytes.
        """
        try:
            return dict(crud_read_note(path))
        except (ValueError, FileNotFoundError) as e:
            return public_error(
                logger,
                "read_note",
                e,
                code="invalid_request",
                message="A nota não existe ou o path é inválido.",
            )
        except Exception as e:
            return public_error(logger, "read_note", e)

    @mcp.tool()
    def get_note_metadata(path: str) -> ToolResult:
        """
        Retorna metadados de uma nota markdown (sem corpo).

        Apenas .md é suportado. Retorna frontmatter parseado, tags extraídas e info do arquivo.

        Parâmetros:
            path: caminho relativo no vault (ex: 'pasta/nota.md')

        Retorna:
            Dict com frontmatter, tags, title, folder, modified_at, size_bytes.
        """
        try:
            return dict(crud_get_note_metadata(path))
        except (ValueError, FileNotFoundError) as e:
            return public_error(
                logger,
                "get_note_metadata",
                e,
                code="invalid_request",
                message="A nota não existe ou o path é inválido.",
            )
        except Exception as e:
            return public_error(logger, "get_note_metadata", e)

    @mcp.tool()
    def list_notes(
        folder: str | None = None,
        extension: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> ToolResult:
        """
        Lista notas do vault com filtros e paginação.

        Lista .md, .pdf, .canvas. NOTA: apenas .md pode ser lido via read_note.
        Para PDFs/Canvas, use search_vault.

        Parâmetros:
            folder: filtrar por pasta (ex: 'projetos', 'estudos/python')
            extension: filtrar por extensão (ex: '.md' para apenas markdown)
            limit: máximo de notas a retornar (default: 500, max: 5000)
            offset: pular N primeiras notas (para paginação)

        Retorna:
            Dict com notes (lista), total, limit, offset, has_more.
            Notas ordenadas por modified_at (mais recente primeiro).
        """
        try:
            return dict(
                crud_list_notes(
                    folder=folder,
                    extension=extension,
                    limit=limit,
                    offset=offset,
                )
            )
        except ValueError as e:
            return public_error(
                logger,
                "list_notes",
                e,
                code="invalid_request",
                message="Os filtros informados são inválidos.",
            )
        except Exception as e:
            return public_error(logger, "list_notes", e)

    @mcp.tool()
    def create_note(
        path: str,
        content: str,
        frontmatter: FrontmatterInput | None = None,
    ) -> ToolResult:
        """
        Cria uma nova nota markdown. Erro se já existir.

        Apenas .md é suportado.

        Parâmetros:
            path: caminho relativo no vault (ex: 'pasta/nova-nota.md')
            content: conteúdo da nota (corpo, sem frontmatter)
            frontmatter: metadados YAML opcionais (ex: {"title": "Minha Nota", "tags": ["tag1"]})

        Retorna:
            Dict com success, message, path e reindex_status quando o índice foi agendado.
        """
        try:
            result: dict[str, object] = dict(crud_create_note(path, content, frontmatter))
            if result["success"]:
                _safe_reindex(reindex_queue, path, result)
                if is_ai_enrichment_enabled() and path.lower().endswith(".md"):
                    enqueue_result = enrichment_jobs.enqueue([path], reason="create_note")
                    if enqueue_result.get("accepted"):
                        result["frontmatter_enrichment_job_id"] = enqueue_result["job_id"]
            return result
        except ValueError as e:
            return public_error(
                logger,
                "create_note",
                e,
                code="invalid_request",
                message="O conteúdo, frontmatter ou path é inválido.",
            )
        except Exception as e:
            return public_error(logger, "create_note", e)

    @mcp.tool()
    def write_note(path: str, content: str) -> ToolResult:
        """
        Sobrescreve ou cria nota markdown com conteúdo completo.

        Apenas .md é suportado. Use quando já tem o conteúdo completo (incluindo frontmatter).

        Parâmetros:
            path: caminho relativo no vault (ex: 'pasta/nota.md')
            content: conteúdo completo da nota

        Retorna:
            Dict com success, message, path e reindex_status quando o índice foi agendado.
        """
        try:
            result: dict[str, object] = dict(crud_write_note(path, content))
            if result["success"]:
                _safe_reindex(reindex_queue, path, result)
            return result
        except ValueError as e:
            return public_error(
                logger,
                "write_note",
                e,
                code="invalid_request",
                message="O conteúdo ou path é inválido.",
            )
        except Exception as e:
            return public_error(logger, "write_note", e)

    @mcp.tool()
    def append_note(
        path: str,
        content: str,
        separator: str = "\n\n",
    ) -> ToolResult:
        """
        Adiciona conteúdo ao final de uma nota markdown existente.

        Apenas .md é suportado.

        Parâmetros:
            path: caminho relativo no vault (ex: 'pasta/nota.md')
            content: conteúdo a adicionar
            separator: separador entre existente e novo (default: "\\n\\n")

        Retorna:
            Dict com success, message, path e reindex_status quando o índice foi agendado.
        """
        try:
            result: dict[str, object] = dict(crud_append_note(path, content, separator))
            if result["success"]:
                _safe_reindex(reindex_queue, path, result)
            return result
        except ValueError as e:
            return public_error(
                logger,
                "append_note",
                e,
                code="invalid_request",
                message="O conteúdo ou path é inválido.",
            )
        except Exception as e:
            return public_error(logger, "append_note", e)

    @mcp.tool()
    def update_frontmatter(
        path: str,
        metadata: FrontmatterInput,
        merge: bool = True,
    ) -> ToolResult:
        """
        Atualiza frontmatter YAML de uma nota markdown existente.

        Apenas .md é suportado. Merge é shallow (arrays/objetos são substituídos, não mesclados).

        Parâmetros:
            path: caminho relativo no vault (ex: 'pasta/nota.md')
            metadata: novos metadados (ex: {"status": "done", "priority": 1})
            merge: se True (default), mescla shallow; se False, substitui tudo

        Retorna:
            Dict com success, message, path e reindex_status quando o índice foi agendado.
        """
        try:
            result: dict[str, object] = dict(crud_update_frontmatter(path, metadata, merge))
            if result["success"]:
                _safe_reindex(reindex_queue, path, result)
            return result
        except ValueError as e:
            return public_error(
                logger,
                "update_frontmatter",
                e,
                code="invalid_request",
                message="O frontmatter ou path é inválido.",
            )
        except Exception as e:
            return public_error(logger, "update_frontmatter", e)

    @mcp.tool()
    def delete_note(path: str) -> ToolResult:
        """
        Deleta uma nota do vault (.md, .pdf ou .canvas), movendo para lixeira.

        Por segurança, deleção permanente não é suportada.
        Arquivos deletados ficam em .trash/ e podem ser recuperados.

        Parâmetros:
            path: caminho relativo no vault

        Retorna:
            Dict com success, message, path e reindex_status quando o índice foi agendado.
        """
        try:
            result: dict[str, object] = dict(crud_delete_note(path))
            if result["success"]:
                # Reindex remove chunks do índice (arquivo não existe mais)
                _safe_reindex(reindex_queue, path, result)
            return result
        except ValueError as e:
            return public_error(
                logger,
                "delete_note",
                e,
                code="invalid_request",
                message="A nota não existe ou o path é inválido.",
            )
        except Exception as e:
            return public_error(logger, "delete_note", e)

    @mcp.tool()
    def move_note(from_path: str, to_path: str) -> ToolResult:
        """
        Move ou renomeia uma nota para destino .md.

        Destino deve ser .md e não pode ser pasta ignorada (.trash, .obsidian, etc).

        Parâmetros:
            from_path: caminho atual relativo no vault
            to_path: novo caminho relativo no vault (ex: 'nova-pasta/nota.md')

        Retorna:
            Dict com success, message, path e reindex_status quando o índice foi agendado.
        """
        try:
            result: dict[str, object] = dict(crud_move_note(from_path, to_path))
            if result["success"]:
                # Reindex from_path (remove chunks antigos) e to_path (adiciona novos)
                _safe_reindex(reindex_queue, from_path, result)
                _safe_reindex(reindex_queue, to_path, result)
            return result
        except ValueError as e:
            return public_error(
                logger,
                "move_note",
                e,
                code="invalid_request",
                message="A origem ou o destino é inválido.",
            )
        except Exception as e:
            return public_error(logger, "move_note", e)

    @mcp.tool()
    def generate_missing_ids(
        folder: str | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        """
        Adiciona UUID v7 a todas as notas .md que não têm 'id' no frontmatter.

        Útil para migração de vaults existentes. IDs são gerados com UUID v7
        (ordenável por tempo, RFC 9562).

        Parâmetros:
            folder: processar apenas notas em pasta específica (opcional)
            dry_run: se True, apenas lista notas sem ID (não modifica)

        Retorna:
            Dict com total_scanned, missing_ids, ids_added (ou would_add se dry_run),
            e lista de notas processadas.
        """
        try:
            # Escanear vault
            all_notes = scan_vault(VAULT_PATH)

            # Filtrar apenas .md
            md_notes = [n for n in all_notes if n.suffix.lower() == ".md"]

            # Filtrar por pasta se especificado
            if folder:
                folder_normalized = folder.strip("/")
                md_notes = [
                    n
                    for n in md_notes
                    if str(n.relative_to(VAULT_PATH)).startswith(folder_normalized)
                ]

            # Verificar quais não têm ID
            notes_without_id = []
            for note_path in md_notes:
                try:
                    fm, _ = read_frontmatter_only(note_path)
                    if "id" not in fm:
                        rel_path = str(note_path.relative_to(VAULT_PATH))
                        notes_without_id.append(rel_path)
                except Exception as e:
                    logger.warning(
                        "frontmatter_read_failed error_type=%s",
                        type(e).__name__,
                    )

            if dry_run:
                return {
                    "dry_run": True,
                    "total_scanned": len(md_notes),
                    "missing_ids": len(notes_without_id),
                    "would_add": len(notes_without_id),
                    "notes": notes_without_id[:100],  # Limitar output
                    "truncated": len(notes_without_id) > 100,
                }

            # Adicionar IDs
            added = []
            errors = []
            for rel_path in notes_without_id:
                result = crud_ensure_note_id(rel_path)
                if result.get("id_added"):
                    added.append({"path": rel_path, "id": result.get("id")})
                elif not result.get("success"):
                    errors.append({"path": rel_path, "error": result.get("message")})

            reindex_status = reindex_queue.enqueue_sync() if added else "not_needed"

            return {
                "total_scanned": len(md_notes),
                "missing_ids": len(notes_without_id),
                "ids_added": len(added),
                "errors": len(errors),
                "reindex_status": reindex_status,
                "added": added[:50],  # Limitar output
                "error_details": errors[:10] if errors else [],
            }

        except Exception as e:
            return public_error(logger, "generate_missing_ids", e)

    @mcp.tool()
    def validate_frontmatter(
        path: str | None = None,
        frontmatter: FrontmatterInput | None = None,
    ) -> ToolResult:
        """
        Valida frontmatter de uma nota ou dict direto contra o schema configurado.

        Use para testar validação antes de criar/atualizar notas.

        Parâmetros:
            path: caminho relativo de nota existente para validar (opcional)
            frontmatter: dict de frontmatter para validar diretamente (opcional)

        Retorna:
            Dict com valid, errors, warnings, suggestions, auto_generated, validated_data.

        Nota: Forneça path OU frontmatter, não ambos.
        """
        try:
            # Validar parâmetros
            if path and frontmatter:
                return "Erro: Forneça 'path' ou 'frontmatter', não ambos."
            if not path and not frontmatter:
                return "Erro: Forneça 'path' ou 'frontmatter'."

            # Se path, ler frontmatter da nota
            if path:
                file_path = resolve_path(path)
                if not file_path.exists():
                    return "Erro [not_found]: nota não encontrada."

                fm, _ = read_frontmatter_only(file_path)
                frontmatter = fm

            # Validar
            validator = get_frontmatter_validator()
            result = validator.validate(frontmatter)

            return {
                "valid": result["valid"],
                "errors": result["errors"],
                "warnings": result["warnings"],
                "suggestions": result["suggestions"],
                "auto_generated": result["auto_generated"],
                "validated_data": result["validated_data"],
            }

        except ValueError as e:
            return public_error(
                logger,
                "validate_frontmatter",
                e,
                code="invalid_request",
                message="O frontmatter ou path é inválido.",
            )
        except Exception as e:
            return public_error(logger, "validate_frontmatter", e)

    @mcp.tool()
    def enrich_frontmatter(
        path: str | None = None,
        paths: list[str] | None = None,
        folder: str | None = None,
        limit: int = 100,
    ) -> ToolResult:
        """
        Enfileira enriquecimento de frontmatter obrigatório em background.

        Sempre assíncrono: retorna job_id imediatamente e processa depois.
        Use `enrich_frontmatter_status` para acompanhar o andamento.

        Parâmetros:
            path: nota única (opcional)
            paths: lista de notas (opcional)
            folder: enfileira notas .md de uma pasta (opcional)
            limit: limite quando usar folder (default: 100, max: 1000)
        """
        try:
            selectors = sum([1 if path else 0, 1 if paths else 0, 1 if folder else 0])
            if selectors != 1:
                return "Erro: Forneça exatamente um seletor: path, paths ou folder."

            selected_paths: list[str] = []
            if path:
                selected_paths = [path]
            elif paths:
                selected_paths = paths
            elif folder is not None:
                folder_normalized = folder.strip("/")
                note_paths = [
                    str(note.relative_to(VAULT_PATH))
                    for note in scan_vault(VAULT_PATH)
                    if note.suffix.lower() == ".md"
                ]
                selected_paths = [
                    item for item in note_paths if item.startswith(folder_normalized)
                ][: max(1, min(limit, 1000))]
            else:
                return "Erro: Forneça exatamente um seletor: path, paths ou folder."

            result = enrichment_jobs.enqueue(selected_paths, reason="manual_tool")
            result["selected_paths"] = len(selected_paths)
            return result
        except ValueError as e:
            return public_error(
                logger,
                "enrich_frontmatter",
                e,
                code="invalid_request",
                message="Os seletores informados são inválidos.",
            )
        except Exception as e:
            return public_error(logger, "enrich_frontmatter", e)

    @mcp.tool()
    def enrich_frontmatter_status(
        job_id: str | None = None,
        limit: int = 20,
    ) -> ToolResult:
        """
        Consulta status de jobs de enriquecimento de frontmatter.

        Parâmetros:
            job_id: id do job específico (opcional)
            limit: quantidade de jobs recentes quando job_id não for informado
        """
        try:
            return enrichment_jobs.get_status(job_id=job_id, limit=limit)
        except Exception as e:
            return public_error(logger, "enrich_frontmatter_status", e)
