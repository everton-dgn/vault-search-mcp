"""
Typed dictionaries for structured CRUD responses.
"""

from typing import Any, NotRequired, TypedDict


class NoteContent(TypedDict):
    """Complete note content."""

    path: str
    content: str
    frontmatter: dict[str, Any]
    body: str
    tags: list[str]
    title: str
    folder: str
    modified_at: str
    size_bytes: int


class NoteMetadata(TypedDict):
    """Note metadata without the full content."""

    path: str
    frontmatter: dict[str, Any]
    tags: list[str]
    title: str
    folder: str
    modified_at: str
    size_bytes: int


class NoteListItem(TypedDict):
    """One item in a note listing."""

    path: str
    title: str
    folder: str
    extension: str
    modified_at: str
    size_bytes: int


class NoteListResult(TypedDict):
    """Paginated list_notes result."""

    notes: list[NoteListItem]
    total: int
    limit: int
    offset: int
    has_more: bool


class OperationResult(TypedDict):
    """Result of a write or delete operation."""

    success: bool
    message: str
    path: str
    error_code: NotRequired[str]
    reindex_status: NotRequired[str]
    frontmatter_enrichment_job_id: NotRequired[str]
    frontmatter_enriched: NotRequired[bool]
    frontmatter_fields_filled: NotRequired[int]
    id_added: NotRequired[bool]
    id: NotRequired[str]
    _validation_warnings: NotRequired[list[dict[str, Any]]]
    _validation_suggestions: NotRequired[list[dict[str, Any]]]


# OperationResult factories.
def success_result(path: str, message: str) -> OperationResult:
    """Create a standardized success result."""
    return OperationResult(success=True, message=message, path=path)


def error_result(
    path: str,
    message: str,
    *,
    error_code: str | None = None,
) -> OperationResult:
    """Create a standardized error result."""
    result = OperationResult(success=False, message=message, path=path)
    if error_code is not None:
        result["error_code"] = error_code
    return result
