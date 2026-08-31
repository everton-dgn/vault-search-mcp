"""
Utilitários para criação e coleta de chunks.
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
    Cria um ChunkRecord a partir de texto e metadados.

    Parâmetros:
        text: texto do chunk (já stripped)
        headers: hierarquia de headers
        meta: metadados do arquivo
        tags: tags separadas por vírgula (default "")
        frontmatter_fields: campos opcionais do frontmatter (default None)

    Retorna:
        ChunkRecord pronto para inserção.
    """
    chunk: ChunkRecord = {
        "note_path": meta["relative_path"],
        "note_title": meta["title"],
        "folder": meta["folder"],
        "headers": headers,
        "tags": tags,
        "modified_at": meta["modified_at"],
        "text": text,
        # Campos opcionais do frontmatter (default vazio)
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

    # Preencher campos do frontmatter se fornecidos
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
    Divide texto em chunks e adiciona à lista (pattern DRY para parsers).

    Centraliza a lógica repetida em parse_note, parse_canvas e parse_pdf:
    - Divide o texto usando chunk_text
    - Remove chunks vazios
    - Cria ChunkRecord e adiciona à lista

    Parâmetros:
        text: texto a dividir
        headers: hierarquia de headers para os chunks
        meta: metadados do arquivo
        chunks: lista destino (modificada in-place)
        tags: tags separadas por vírgula (default "")
        frontmatter_fields: campos opcionais do frontmatter (default None)
    """
    from vault_search.config.chunking import CHUNK_OVERLAP, CHUNK_SIZE
    from vault_search.core.chunker import chunk_text

    parts = chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        chunks.append(make_chunk(part, headers, meta, tags, frontmatter_fields))
