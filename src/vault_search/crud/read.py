"""
Operações de leitura para notas do vault.

Otimizações:
- Catálogo SQLite para list_notes() em O(1)
- Leitura parcial de frontmatter (não carrega arquivo inteiro)
- Cache LRU com validação por (path, mtime_ns, size)
- Métricas de latência para profiling
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from vault_search.config.paths import VAULT_PATH
from vault_search.config.search import (
    IGNORED_FOLDERS,
    INDEXABLE_EXTENSIONS,
    LIST_NOTES_DEFAULT_LIMIT,
    LIST_NOTES_MAX_LIMIT,
)
from vault_search.crud.cache import CacheKey, get_metadata_cache
from vault_search.crud.catalog import get_catalog
from vault_search.crud.types import NoteContent, NoteListItem, NoteListResult, NoteMetadata
from vault_search.crud.validation import (
    get_folder,
    resolve_path,
    validate_readable_text,
)
from vault_search.parsers.frontmatter import extract_tags, parse_frontmatter, read_frontmatter_only
from vault_search.utils.metrics import MetricsCollector
from vault_search.utils.security import validate_relative_path

logger = logging.getLogger(__name__)
_metrics = MetricsCollector()

# Flag para usar catálogo SQLite (habilitado por padrão)
USE_CATALOG = True


def read_note(relative_path: str) -> NoteContent:
    """
    Lê conteúdo completo de uma nota markdown.

    Apenas .md é suportado (texto plano com frontmatter YAML).
    Para buscar em PDFs/Canvas, use search_vault.

    Parâmetros:
        relative_path: caminho relativo no vault (ex: 'pasta/nota.md')

    Retorna:
        NoteContent com conteúdo, frontmatter parseado e metadados.

    Raises:
        ValueError: path inválido, fora do vault, ou extensão não suportada
        FileNotFoundError: nota não existe
    """
    with _metrics.measure("read_note"):
        validate_readable_text(relative_path)
        file_path = resolve_path(relative_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Nota não encontrada: {relative_path}")

        content = file_path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
        tags = extract_tags(frontmatter)
        stat = file_path.stat()

        # Title: frontmatter > filename
        title = frontmatter.get("title", file_path.stem)
        if not isinstance(title, str):
            title = str(title) if title else file_path.stem

        logger.debug("read_note completed")

        return {
            "path": relative_path,
            "content": content,
            "frontmatter": frontmatter,
            "body": body,
            "tags": tags,
            "title": title,
            "folder": get_folder(file_path),
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "size_bytes": stat.st_size,
        }


def get_note_metadata(relative_path: str) -> NoteMetadata:
    """
    Retorna apenas metadados de uma nota markdown (sem conteúdo completo).

    Apenas .md é suportado (texto plano com frontmatter YAML).
    Mais eficiente que read_note quando só precisa de metadados.

    Otimizações:
    - Cache LRU com validação por (path, mtime_ns, size)
    - Leitura parcial de frontmatter (não carrega arquivo inteiro)

    Parâmetros:
        relative_path: caminho relativo no vault

    Retorna:
        NoteMetadata com frontmatter, tags e info do arquivo.
    """
    with _metrics.measure("get_note_metadata"):
        validate_readable_text(relative_path)
        file_path = resolve_path(relative_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Nota não encontrada: {relative_path}")

        stat = file_path.stat()

        # Tentar cache primeiro
        cache = get_metadata_cache()
        cache_key = CacheKey.from_stat(str(file_path), stat)
        cached = cache.get(cache_key)
        if cached is not None:
            logger.debug("get_note_metadata cache_hit=true")
            return cached

        # Cache miss - leitura otimizada (apenas frontmatter)
        frontmatter, _ = read_frontmatter_only(file_path)
        tags = extract_tags(frontmatter)

        title = frontmatter.get("title", file_path.stem)
        if not isinstance(title, str):
            title = str(title) if title else file_path.stem

        metadata: NoteMetadata = {
            "path": relative_path,
            "frontmatter": frontmatter,
            "tags": tags,
            "title": title,
            "folder": get_folder(file_path),
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "size_bytes": stat.st_size,
        }

        # Armazenar no cache
        cache.set(cache_key, metadata)

        logger.debug("get_note_metadata cache_hit=false")
        return metadata


def _scandir_recursive(
    start_path: Path,
    extension: str | None,
    ignored_folders: set[str],
    indexable_extensions: set[str],
) -> list[NoteListItem]:
    """
    Scan recursivo usando os.scandir (mais rápido que Path.rglob).

    os.scandir é mais eficiente porque:
    - Retorna DirEntry com stat() em cache no SO
    - Não cria objetos Path intermediários
    - Permite early-exit de diretórios ignorados
    """
    notes: list[NoteListItem] = []
    stack = [start_path]

    while stack:
        current_dir = stack.pop()

        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    # Skip folders ignorados imediatamente
                    if entry.name in ignored_folders:
                        continue

                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                        continue

                    if not entry.is_file(follow_symlinks=False):
                        continue

                    # Verificar extensão
                    name = entry.name
                    dot_idx = name.rfind(".")
                    if dot_idx == -1:
                        continue

                    ext = name[dot_idx:].lower()
                    if ext not in indexable_extensions:
                        continue

                    if extension and ext != extension:
                        continue

                    # Obter stat (já em cache no DirEntry)
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue

                    path = Path(entry.path)
                    relative_path = str(path.relative_to(VAULT_PATH))

                    notes.append(
                        {
                            "path": relative_path,
                            "title": name[:dot_idx],  # stem sem extensão
                            "folder": get_folder(path),
                            "extension": ext,
                            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            "size_bytes": stat.st_size,
                        }
                    )

        except PermissionError:
            logger.warning("vault_scan_permission_denied")
            continue

    return notes


def list_notes(
    folder: str | None = None,
    extension: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> NoteListResult:
    """
    Lista notas do vault com filtros e paginação.

    NOTA: Lista todas as extensões indexáveis (.md, .pdf, .canvas).
    Porém, apenas .md pode ser lido via read_note/get_note_metadata.
    Para PDFs/Canvas, use search_vault para buscar conteúdo.

    Otimizações:
    - Catálogo SQLite para query O(1) (se habilitado)
    - Fallback: os.scandir (mais rápido que rglob)
    - Early-exit de diretórios ignorados
    - stat() via DirEntry (sem syscall extra)

    Parâmetros:
        folder: filtrar por pasta (ex: 'projetos', 'estudos/python')
        extension: filtrar por extensão (ex: '.md' para apenas markdown)
        limit: máximo de notas a retornar (default: 500, max: 5000)
        offset: pular N primeiras notas (para paginação)

    Retorna:
        NoteListResult com notes, total, limit, offset, has_more.
    """
    with _metrics.measure("list_notes"):
        # Aplicar limite padrão e máximo
        if limit is None:
            limit = LIST_NOTES_DEFAULT_LIMIT
        limit = max(1, min(limit, LIST_NOTES_MAX_LIMIT))
        offset = max(0, offset)

        logger.debug(
            "list_notes folder_filter=%s extension_filter=%s limit=%d offset=%d",
            bool(folder),
            bool(extension),
            limit,
            offset,
        )

        # Validações
        if folder:
            if not validate_relative_path(folder):
                raise ValueError(f"Folder inválido ou fora do vault: {folder}")
            folder_parts = Path(folder).parts
            if any(ignored in folder_parts for ignored in IGNORED_FOLDERS):
                raise ValueError(f"Folder está na lista de ignorados: {folder}")

        if extension:
            extension = extension.lower()
            if not extension.startswith("."):
                extension = f".{extension}"
            if extension not in INDEXABLE_EXTENSIONS:
                raise ValueError(
                    f"Extensão '{extension}' não suportada. "
                    f"Use: {', '.join(sorted(INDEXABLE_EXTENSIONS))}"
                )

        # Tentar usar catálogo SQLite (O(1) query)
        if USE_CATALOG:
            try:
                catalog = get_catalog()
                notes, total = catalog.list_notes(
                    folder=folder,
                    extension=extension,
                    limit=limit,
                    offset=offset,
                )
                has_more = (offset + limit) < total

                logger.debug(f"list_notes via catalog: {len(notes)} de {total}")

                return {
                    "notes": notes,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": has_more,
                }
            except Exception as e:
                logger.warning(
                    "catalog_unavailable fallback=filesystem error_type=%s",
                    type(e).__name__,
                )

        # Fallback: scan do filesystem
        if folder:
            start_path = resolve_path(folder)
            if not start_path.exists():
                return {
                    "notes": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                    "has_more": False,
                }
        else:
            start_path = VAULT_PATH

        notes = _scandir_recursive(
            start_path,
            extension,
            IGNORED_FOLDERS,
            INDEXABLE_EXTENSIONS,
        )

        notes.sort(key=lambda n: n["modified_at"], reverse=True)

        total = len(notes)
        paginated = notes[offset : offset + limit]
        has_more = (offset + limit) < total

        return {
            "notes": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
        }
