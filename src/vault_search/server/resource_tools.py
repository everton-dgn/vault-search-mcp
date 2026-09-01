"""
MCP resources for vault navigation.

Resources expose vault data through navigable URIs:
- vault://notes lists notes
- vault://notes/{path} returns one note
- vault://folders returns the folder tree
- vault://stats returns vault statistics
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
    """Count each tag once per note from the rebuildable index."""
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
    Register MCP resources for vault navigation.

    Parameters:
        mcp: FastMCP instance
        indexer: VaultIndexer instance
        searcher: VaultSearcher instance
    """

    @mcp.resource("vault://stats")
    def vault_stats_resource(ctx: Context) -> dict[str, object]:
        """
        Return aggregate vault statistics.

        Includes note count, chunk count, and last modification.
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
        Return the vault folder tree.

        The response is a hierarchical directory structure.
        """
        try:
            catalog = get_catalog()
            folders = catalog.get_all_folders()

            # Build the tree.
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
        Return one bounded page of the note catalog.

        ``has_more`` reports whether matches remain after the snapshot.
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
        Return the content of one note.

        Parameters:
            path: vault-relative note path, such as "folder/note.md"

        Returns complete note content with metadata.
        """
        # Validate the path.
        try:
            resolve_path(path)
        except ValueError:
            return {"error": "Path is invalid or outside the vault", "code": "invalid_path"}

        # Check the extension.
        ext = Path(path).suffix.lower()
        if ext not in READABLE_TEXT_EXTENSIONS:
            return {
                "error": (f"Extension {ext} is not readable. Supported: {READABLE_TEXT_EXTENSIONS}")
            }

        try:
            result = read_note(path)
            if isinstance(result, str):
                # A tool-level error returned as a string.
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
            return {"error": "Note not found", "code": "not_found"}
        except Exception as e:
            return public_error_dict(logger, "vault_note_resource", e)

    @mcp.resource("vault://search/recent")
    def vault_recent_resource(ctx: Context) -> dict[str, object]:
        """
        Return notes modified in the last seven days.

        This provides a bounded recent-activity snapshot.
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
        Return all indexed vault tags with counts.

        Counts each tag once per note.
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

    logger.info("MCP resources registered: 6")
