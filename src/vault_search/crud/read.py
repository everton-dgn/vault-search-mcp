"""
Read operations for vault notes.

Implementation notes:
- SQLite catalog for bounded list_notes queries
- Partial frontmatter reads without loading the entire file
- LRU cache validated by (path, mtime_ns, size)
- Latency metrics for profiling
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

# Prefer the SQLite catalog when available.
USE_CATALOG = True


def read_note(relative_path: str) -> NoteContent:
    """
    Read the complete content of a Markdown note.

    Only .md supports plain-text reads with YAML frontmatter. Use search_vault
    for PDF and Canvas content.

    Parameters:
        relative_path: path relative to the vault, such as 'folder/note.md'

    Returns:
        NoteContent with text, parsed frontmatter, and metadata.

    Raises:
        ValueError: if the path is invalid, outside the vault, or unsupported
        FileNotFoundError: if the note does not exist
    """
    with _metrics.measure("read_note"):
        validate_readable_text(relative_path)
        file_path = resolve_path(relative_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Note not found: {relative_path}")

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
    Return metadata for a Markdown note without its full content.

    This is cheaper than read_note when the caller needs only metadata.

    Uses an LRU cache keyed by (path, mtime_ns, size) and a partial
    frontmatter read.

    Parameters:
        relative_path: path relative to the vault

    Returns:
        NoteMetadata with frontmatter, tags, and file information.
    """
    with _metrics.measure("get_note_metadata"):
        validate_readable_text(relative_path)
        file_path = resolve_path(relative_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Note not found: {relative_path}")

        stat = file_path.stat()

        # Check the cache first.
        cache = get_metadata_cache()
        cache_key = CacheKey.from_stat(str(file_path), stat)
        cached = cache.get(cache_key)
        if cached is not None:
            logger.debug("get_note_metadata cache_hit=true")
            return cached

        # Read only frontmatter on a cache miss.
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

        # Store the result in the cache.
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
    Recursively scan with os.scandir.

    DirEntry reuses operating-system stat data, avoids intermediate Path
    objects, and lets ignored directories exit early.
    """
    notes: list[NoteListItem] = []
    stack = [start_path]

    while stack:
        current_dir = stack.pop()

        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    # Skip ignored folders immediately.
                    if entry.name in ignored_folders:
                        continue

                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                        continue

                    if not entry.is_file(follow_symlinks=False):
                        continue

                    # Check the extension.
                    name = entry.name
                    dot_idx = name.rfind(".")
                    if dot_idx == -1:
                        continue

                    ext = name[dot_idx:].lower()
                    if ext not in indexable_extensions:
                        continue

                    if extension and ext != extension:
                        continue

                    # Reuse stat data from DirEntry.
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue

                    path = Path(entry.path)
                    relative_path = str(path.relative_to(VAULT_PATH))

                    notes.append(
                        {
                            "path": relative_path,
                            "title": name[:dot_idx],  # Stem without extension.
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
    List vault notes with filters and pagination.

    The result includes every indexable extension (.md, .pdf, .canvas), while
    read_note and get_note_metadata accept only .md. Use search_vault for PDF
    and Canvas content.

    The SQLite catalog is preferred. A filesystem scan provides a bounded
    fallback and skips ignored directories early.

    Parameters:
        folder: optional folder filter, such as 'projects' or 'research/python'
        extension: optional extension filter, such as '.md'
        limit: maximum number of notes to return
        offset: number of matching notes to skip

    Returns:
        NoteListResult containing notes, total, limit, offset, and has_more.
    """
    with _metrics.measure("list_notes"):
        # Apply the default and maximum limits.
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

        # Validate filters.
        if folder:
            if not validate_relative_path(folder):
                raise ValueError(f"Folder is invalid or outside the vault: {folder}")
            folder_parts = Path(folder).parts
            if any(ignored in folder_parts for ignored in IGNORED_FOLDERS):
                raise ValueError(f"Folder is ignored: {folder}")

        if extension:
            extension = extension.lower()
            if not extension.startswith("."):
                extension = f".{extension}"
            if extension not in INDEXABLE_EXTENSIONS:
                raise ValueError(
                    f"Extension '{extension}' is not supported. "
                    f"Use: {', '.join(sorted(INDEXABLE_EXTENSIONS))}"
                )

        # Prefer the SQLite catalog.
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

                logger.debug("list_notes via_catalog=%d total=%d", len(notes), total)

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

        # Fall back to a filesystem scan.
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
