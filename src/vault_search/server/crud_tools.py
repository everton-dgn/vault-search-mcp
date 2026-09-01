"""
MCP tools for note CRUD.
"""

import logging
from pathlib import Path

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
from vault_search.utils.security import validate_relative_path
from vault_search.utils.shutdown import ShutdownManager

logger = logging.getLogger("vault-search-mcp")

type ToolResult = dict[str, object] | str
type FrontmatterInput = dict[str, object]


def _folder_selector(folder: str) -> Path:
    """Return a validated vault-relative folder selector."""
    normalized = folder.strip()
    if not validate_relative_path(normalized):
        raise ValueError("Folder must be a non-empty vault-relative path")
    return Path(normalized)


def _path_is_in_folder(relative_path: str, folder: Path) -> bool:
    """Match one complete path component instead of a string prefix."""
    return Path(relative_path).is_relative_to(folder)


def _safe_reindex(
    scheduler: ReindexQueue,
    path: str,
    result: dict[str, object],
) -> dict[str, object]:
    """
    Schedule note reindexing without blocking the mutation.

    The write is already durable. A bounded worker updates the index, while the
    filesystem watcher provides eventual recovery after a failure.
    """
    result["reindex_status"] = scheduler.enqueue(path)
    return result


def register_crud_tools(mcp, indexer, searcher):
    """
    Register CRUD tools on the MCP server.

    Parameters:
        mcp: FastMCP instance
        indexer: VaultIndexer instance
        searcher: VaultSearcher instance
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
        Read a complete Markdown note with parsed frontmatter.

        Only .md is supported. Use search_vault for PDF or Canvas content.

        Parameters:
            path: vault-relative path, such as 'folder/note.md'

        Returns:
            Dictionary with content, frontmatter, body, tags, title, folder,
            modified_at, and size_bytes.
        """
        try:
            return dict(crud_read_note(path))
        except (ValueError, FileNotFoundError) as e:
            return public_error(
                logger,
                "read_note",
                e,
                code="invalid_request",
                message="The note does not exist or the path is invalid.",
            )
        except Exception as e:
            return public_error(logger, "read_note", e)

    @mcp.tool()
    def get_note_metadata(path: str) -> ToolResult:
        """
        Return Markdown note metadata without the body.

        Returns parsed frontmatter, extracted tags, and file metadata for .md.

        Parameters:
            path: vault-relative path, such as 'folder/note.md'

        Returns:
            Dictionary with frontmatter, tags, title, folder, modified_at,
            and size_bytes.
        """
        try:
            return dict(crud_get_note_metadata(path))
        except (ValueError, FileNotFoundError) as e:
            return public_error(
                logger,
                "get_note_metadata",
                e,
                code="invalid_request",
                message="The note does not exist or the path is invalid.",
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
        List vault notes with filters and pagination.

        Lists .md, .pdf, and .canvas. Only .md can be read with read_note.
        For PDF and Canvas files, use search_vault.

        Parameters:
            folder: optional folder filter, such as 'projects' or 'research/python'
            extension: optional extension filter, such as '.md'
            limit: maximum number of notes to return
            offset: number of matching notes to skip

        Returns:
            Dictionary with notes, total, limit, offset, and has_more. Notes are
            ordered by modified_at, newest first.
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
                message="The supplied filters are invalid.",
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
        Create a Markdown note and fail if it already exists.

        Only .md is supported.

        Parameters:
            path: vault-relative path, such as 'folder/new-note.md'
            content: note body without frontmatter
            frontmatter: optional YAML metadata

        Returns:
            Dictionary with success, message, path, and optional reindex_status.
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
                message="The content, frontmatter, or path is invalid.",
            )
        except Exception as e:
            return public_error(logger, "create_note", e)

    @mcp.tool()
    def write_note(path: str, content: str) -> ToolResult:
        """
        Overwrite or create a Markdown note from complete content.

        Use this when the caller already has the complete .md content.

        Parameters:
            path: vault-relative path, such as 'folder/note.md'
            content: complete note content

        Returns:
            Dictionary with success, message, path, and optional reindex_status.
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
                message="The content or path is invalid.",
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
        Append content to an existing Markdown note.

        Only .md is supported.

        Parameters:
            path: vault-relative path, such as 'folder/note.md'
            content: content to append
            separator: separator between existing and appended content (default: "\\n\\n")

        Returns:
            Dictionary with success, message, path, and optional reindex_status.
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
                message="The content or path is invalid.",
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
        Update YAML frontmatter on an existing Markdown note.

        Only .md is supported. Merge is shallow, so arrays and objects are replaced.

        Parameters:
            path: vault-relative path, such as 'folder/note.md'
            metadata: new metadata, such as {"status": "done", "priority": 1}
            merge: shallow-merge when true, otherwise replace all frontmatter

        Returns:
            Dictionary with success, message, path, and optional reindex_status.
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
                message="The frontmatter or path is invalid.",
            )
        except Exception as e:
            return public_error(logger, "update_frontmatter", e)

    @mcp.tool()
    def delete_note(path: str) -> ToolResult:
        """
        Delete a .md, .pdf, or .canvas note by moving it to vault trash.

        Permanent deletion is unsupported. Files remain recoverable in .trash.

        Parameters:
            path: path relative to the vault

        Returns:
            Dictionary with success, message, path, and optional reindex_status.
        """
        try:
            result: dict[str, object] = dict(crud_delete_note(path))
            if result["success"]:
                # Reindexing removes chunks for the now-missing file.
                _safe_reindex(reindex_queue, path, result)
            return result
        except ValueError as e:
            return public_error(
                logger,
                "delete_note",
                e,
                code="invalid_request",
                message="The note does not exist or the path is invalid.",
            )
        except Exception as e:
            return public_error(logger, "delete_note", e)

    @mcp.tool()
    def move_note(from_path: str, to_path: str) -> ToolResult:
        """
        Move or rename a note.

        Source and destination extensions must match, and ignored folders are blocked.

        Parameters:
            from_path: current vault-relative path
            to_path: new vault-relative path, such as 'new-folder/note.md'

        Returns:
            Dictionary with success, message, path, and optional reindex_status.
        """
        try:
            result: dict[str, object] = dict(crud_move_note(from_path, to_path))
            if result["success"]:
                # Remove old chunks and add destination chunks.
                _safe_reindex(reindex_queue, from_path, result)
                _safe_reindex(reindex_queue, to_path, result)
            return result
        except ValueError as e:
            return public_error(
                logger,
                "move_note",
                e,
                code="invalid_request",
                message="The source or destination is invalid.",
            )
        except Exception as e:
            return public_error(logger, "move_note", e)

    @mcp.tool()
    def generate_missing_ids(
        folder: str | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        """
        Add UUIDv7 ids to Markdown notes that lack a frontmatter id.

        UUIDv7 values follow RFC 9562 and are time-ordered.

        Parameters:
            folder: optional folder scope
            dry_run: list notes without ids without modifying them

        Returns:
            Dictionary with totals and processed note paths.
        """
        try:
            # Scan the vault once.
            all_notes = scan_vault(VAULT_PATH)

            # Keep only Markdown files.
            md_notes = [n for n in all_notes if n.suffix.lower() == ".md"]

            # Apply the optional folder scope.
            if folder:
                folder_path = _folder_selector(folder)
                md_notes = [
                    n
                    for n in md_notes
                    if _path_is_in_folder(str(n.relative_to(VAULT_PATH)), folder_path)
                ]

            # Find notes that have no id.
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
                    "notes": notes_without_id[:100],  # Bound response size.
                    "truncated": len(notes_without_id) > 100,
                }

            # Add ids.
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
                "added": added[:50],  # Bound response size.
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
        Validate note frontmatter or a supplied dictionary against the schema.

        Use this before creating or updating notes.

        Parameters:
            path: optional path of an existing note
            frontmatter: optional frontmatter dictionary to validate directly

        Returns:
            Validation result with final data, errors, warnings, and suggestions.

        Supply exactly one of path or frontmatter.
        """
        try:
            # Validate mutually exclusive inputs.
            if path and frontmatter:
                return "Error: provide either 'path' or 'frontmatter', not both."
            if not path and not frontmatter:
                return "Error: provide 'path' or 'frontmatter'."

            # Read frontmatter from the note when a path is supplied.
            if path:
                file_path = resolve_path(path)
                if not file_path.exists():
                    return "Error [not_found]: note not found."

                fm, _ = read_frontmatter_only(file_path)
                frontmatter = fm

            # Validate.
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
                message="The frontmatter or path is invalid.",
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
        Enqueue required-frontmatter enrichment in the background.

        Returns a job id immediately. Use enrich_frontmatter_status to inspect it.

        Parameters:
            path: optional single note
            paths: optional list of notes
            folder: optional folder of .md notes
            limit: folder selection limit
        """
        try:
            selectors = sum([1 if path else 0, 1 if paths else 0, 1 if folder else 0])
            if selectors != 1:
                return "Error: provide exactly one selector: path, paths, or folder."

            selected_paths: list[str] = []
            if path:
                selected_paths = [path]
            elif paths:
                selected_paths = paths
            elif folder is not None:
                folder_path = _folder_selector(folder)
                note_paths = [
                    str(note.relative_to(VAULT_PATH))
                    for note in scan_vault(VAULT_PATH)
                    if note.suffix.lower() == ".md"
                ]
                selected_paths = [
                    item for item in note_paths if _path_is_in_folder(item, folder_path)
                ][: max(1, min(limit, 1000))]
            else:
                return "Error: provide exactly one selector: path, paths, or folder."

            result = enrichment_jobs.enqueue(selected_paths, reason="manual_tool")
            result["selected_paths"] = len(selected_paths)
            return result
        except ValueError as e:
            return public_error(
                logger,
                "enrich_frontmatter",
                e,
                code="invalid_request",
                message="The supplied selectors are invalid.",
            )
        except Exception as e:
            return public_error(logger, "enrich_frontmatter", e)

    @mcp.tool()
    def enrich_frontmatter_status(
        job_id: str | None = None,
        limit: int = 20,
    ) -> ToolResult:
        """
        Return the status of frontmatter enrichment jobs.

        Parameters:
            job_id: optional job id
            limit: number of recent jobs when job_id is omitted
        """
        try:
            return enrichment_jobs.get_status(job_id=job_id, limit=limit)
        except Exception as e:
            return public_error(logger, "enrich_frontmatter_status", e)
