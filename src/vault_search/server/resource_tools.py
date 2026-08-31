"""
MCP Resources para navegação do vault.

Resources expõem dados do vault via URIs navegáveis:
- vault://notes — lista todas as notas
- vault://notes/{path} — conteúdo de uma nota específica
- vault://folders — árvore de pastas
- vault://stats — estatísticas do vault
"""

import logging
from collections import Counter
from pathlib import Path

from fastmcp import Context

from vault_search.config.search import READABLE_TEXT_EXTENSIONS
from vault_search.crud.catalog import get_catalog
from vault_search.crud.read import read_note
from vault_search.crud.validation import resolve_path
from vault_search.server.errors import public_error_dict

logger = logging.getLogger("vault-search-mcp")

type FolderTree = dict[str, FolderTree]


def _collect_tag_stats(indexer) -> list[dict[str, str | int]]:
    """Conta cada tag uma vez por nota a partir do índice reconstruível."""
    table = indexer._ensure_table()
    total_rows = table.count_rows()
    if total_rows == 0:
        return []

    arrow_table = table.search().select(["note_path", "tags"]).limit(total_rows).to_arrow()
    note_paths = arrow_table.column("note_path").to_pylist()
    tag_values = arrow_table.column("tags").to_pylist()
    tags_by_note: dict[str, set[str]] = {}
    for note_path, raw_tags in zip(note_paths, tag_values, strict=True):
        note_tags = tags_by_note.setdefault(str(note_path), set())
        if not raw_tags:
            continue
        note_tags.update(tag.strip() for tag in str(raw_tags).split(",") if tag.strip())

    counts: Counter[str] = Counter()
    for note_tags in tags_by_note.values():
        counts.update(note_tags)
    return [{"tag": tag, "count": count} for tag, count in counts.most_common()]


def register_resources(mcp, indexer, searcher):
    """
    Registra Resources MCP para navegação do vault.

    Parâmetros:
        mcp: instância do FastMCP
        indexer: instância do VaultIndexer
        searcher: instância do VaultSearcher
    """

    @mcp.resource("vault://stats")
    def vault_stats_resource(ctx: Context) -> dict[str, object]:
        """
        Estatísticas gerais do vault.

        Retorna contagem de notas, chunks, última modificação.
        """
        try:
            stats = indexer.get_stats()
            return {
                "uri": "vault://stats",
                "type": "statistics",
                "data": stats,
            }
        except Exception as e:
            return public_error_dict(logger, "vault_stats_resource", e)

    @mcp.resource("vault://folders")
    def vault_folders_resource(ctx: Context) -> dict[str, object]:
        """
        Árvore de pastas do vault.

        Retorna estrutura hierárquica de diretórios.
        """
        try:
            catalog = get_catalog()
            folders = catalog.get_all_folders()

            # Construir árvore
            tree: FolderTree = {}
            for folder in sorted(folders):
                parts = folder.split("/")
                current = tree
                for part in parts:
                    current = current.setdefault(part, {})

            return {
                "uri": "vault://folders",
                "type": "folder_tree",
                "total_folders": len(folders),
                "tree": tree,
            }
        except Exception as e:
            return public_error_dict(logger, "vault_folders_resource", e)

    @mcp.resource("vault://notes")
    def vault_notes_list_resource(ctx: Context) -> dict[str, object]:
        """
        Retorna uma página limitada do catálogo de notas.

        O campo ``has_more`` informa quando o total ultrapassa o snapshot.
        """
        try:
            catalog = get_catalog()
            limit = 5000
            notes, total = catalog.list_notes(limit=limit)

            return {
                "uri": "vault://notes",
                "type": "note_list",
                "total": total,
                "returned": len(notes),
                "limit": limit,
                "has_more": total > len(notes),
                "notes": [
                    {
                        "path": n["path"],
                        "title": n.get("title", Path(n["path"]).stem),
                        "folder": n.get("folder", ""),
                        "modified_at": n.get("modified_at"),
                    }
                    for n in notes
                ],
            }
        except Exception as e:
            return public_error_dict(logger, "vault_notes_list_resource", e)

    @mcp.resource("vault://notes/{path*}")
    def vault_note_resource(path: str, ctx: Context) -> dict[str, object]:
        """
        Conteúdo de uma nota específica.

        Parâmetros:
            path: caminho relativo da nota (ex: "pasta/nota.md")

        Retorna conteúdo completo da nota com metadados.
        """
        # Validar path
        try:
            resolve_path(path)
        except ValueError:
            return {"error": "Path inválido ou fora do vault", "code": "invalid_path"}

        # Verificar extensão
        ext = Path(path).suffix.lower()
        if ext not in READABLE_TEXT_EXTENSIONS:
            return {
                "error": f"Extensão {ext} não suportada para leitura. Suportadas: {READABLE_TEXT_EXTENSIONS}"
            }

        try:
            result = read_note(path)
            if isinstance(result, str):
                # Erro retornado como string
                return {"error": result}

            return {
                "uri": f"vault://notes/{path}",
                "type": "note",
                "path": path,
                "title": result.get("title", Path(path).stem),
                "content": result.get("content", ""),
                "frontmatter": result.get("frontmatter", {}),
                "modified_at": result.get("modified_at"),
                "size_bytes": result.get("size_bytes", 0),
            }
        except FileNotFoundError:
            return {"error": "Nota não encontrada", "code": "not_found"}
        except Exception as e:
            return public_error_dict(logger, "vault_note_resource", e)

    @mcp.resource("vault://search/recent")
    def vault_recent_resource(ctx: Context) -> dict[str, object]:
        """
        Notas modificadas recentemente (últimos 7 dias).

        Útil para descobrir atividade recente no vault.
        """
        try:
            catalog = get_catalog()
            recent = catalog.get_recent_notes(days=7, limit=50)

            return {
                "uri": "vault://search/recent",
                "type": "recent_notes",
                "days": 7,
                "total": len(recent),
                "notes": recent,
            }
        except Exception as e:
            return public_error_dict(logger, "vault_recent_resource", e)

    @mcp.resource("vault://tags")
    def vault_tags_resource(ctx: Context) -> dict[str, object]:
        """
        Todas as tags do vault com contagens.

        Retorna estatísticas de uso de tags.
        """
        try:
            tags = _collect_tag_stats(indexer)

            return {
                "uri": "vault://tags",
                "type": "tag_stats",
                "total_unique_tags": len(tags),
                "tags": tags,
            }
        except Exception as e:
            return public_error_dict(logger, "vault_tags_resource", e)

    logger.info("Resources MCP registrados: 6")
