"""
TypedDicts para respostas estruturadas das operações CRUD.
"""

from typing import Any, NotRequired, TypedDict


class NoteContent(TypedDict):
    """Conteúdo completo de uma nota."""

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
    """Metadados de uma nota (sem conteúdo)."""

    path: str
    frontmatter: dict[str, Any]
    tags: list[str]
    title: str
    folder: str
    modified_at: str
    size_bytes: int


class NoteListItem(TypedDict):
    """Item na listagem de notas."""

    path: str
    title: str
    folder: str
    extension: str
    modified_at: str
    size_bytes: int


class NoteListResult(TypedDict):
    """Resultado paginado de list_notes."""

    notes: list[NoteListItem]
    total: int
    limit: int
    offset: int
    has_more: bool


class OperationResult(TypedDict):
    """Resultado de operação de escrita/delete."""

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


# Factory functions para OperationResult
def success_result(path: str, message: str) -> OperationResult:
    """Cria resultado de sucesso padronizado."""
    return OperationResult(success=True, message=message, path=path)


def error_result(
    path: str,
    message: str,
    *,
    error_code: str | None = None,
) -> OperationResult:
    """Cria resultado de erro padronizado."""
    result = OperationResult(success=False, message=message, path=path)
    if error_code is not None:
        result["error_code"] = error_code
    return result
