"""
Utilities for creating and collecting chunks.
"""

from collections.abc import Mapping

from vault_search.type_defs import ChunkRecord
from vault_search.utils.metadata import FileMetadata


def make_chunk(
    text: str,
    headers: str,
    meta: FileMetadata,
    tags: str = "",
    frontmatter_fields: Mapping[str, str] | None = None,
) -> ChunkRecord:
    """
    Create a ``ChunkRecord`` from text and metadata.

    Parameters:
        text: Chunk text, already stripped.
        headers: Heading hierarchy.
        meta: File metadata.
        tags: Comma-separated tags; defaults to an empty string.
        frontmatter_fields: Optional frontmatter fields.

    Returns:
        A ``ChunkRecord`` ready for insertion.
    """
    chunk: ChunkRecord = {
        "note_path": meta["relative_path"],
        "note_title": meta["title"],
        "folder": meta["folder"],
        "headers": headers,
        "tags": tags,
        "modified_at": meta["modified_at"],
        "text": text,
        # Optional frontmatter fields default to empty values.
        "id": "",
        "created_at": "",
        "updated_at": "",
        "description": "",
        "status": "",
        "note_type": "",
        "category": "",
        "project": "",
        "source": "",
    }

    # Populate frontmatter fields when provided.
    if frontmatter_fields:
        if value := frontmatter_fields.get("id"):
            chunk["id"] = value
        if value := frontmatter_fields.get("created_at"):
            chunk["created_at"] = value
        if value := frontmatter_fields.get("updated_at"):
            chunk["updated_at"] = value
        if value := frontmatter_fields.get("description"):
            chunk["description"] = value
        if value := frontmatter_fields.get("status"):
            chunk["status"] = value
        if value := frontmatter_fields.get("note_type"):
            chunk["note_type"] = value
        if value := frontmatter_fields.get("category"):
            chunk["category"] = value
        if value := frontmatter_fields.get("project"):
            chunk["project"] = value
        if value := frontmatter_fields.get("source"):
            chunk["source"] = value

    return chunk


def chunk_and_collect(
    text: str,
    headers: str,
    meta: FileMetadata,
    chunks: list[ChunkRecord],
    tags: str = "",
    frontmatter_fields: Mapping[str, str] | None = None,
) -> None:
    """
    Split text into chunks and append them to the destination list.

    Centralize logic shared by ``parse_note``, ``parse_canvas``, and ``parse_pdf``:
    - Split text with ``chunk_text``.
    - Remove empty chunks.
    - Create ``ChunkRecord`` objects and append them to the list.

    Parameters:
        text: Text to split.
        headers: Heading hierarchy for the chunks.
        meta: File metadata.
        chunks: Destination list, modified in place.
        tags: Comma-separated tags; defaults to an empty string.
        frontmatter_fields: Optional frontmatter fields.
    """
    from vault_search.config.chunking import CHUNK_OVERLAP, CHUNK_SIZE
    from vault_search.core.chunker import chunk_text

    parts = chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        chunks.append(make_chunk(part, headers, meta, tags, frontmatter_fields))
