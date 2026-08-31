"""
Parsers para diferentes formatos de arquivo.

Imports diretos:
    from vault_search.parsers.markdown import parse_note, split_by_headers
    from vault_search.parsers.frontmatter import parse_frontmatter, extract_tags
    from vault_search.parsers.canvas import parse_canvas
    from vault_search.parsers.pdf import parse_pdf

Para dispatch por extensão, use parse_file():
    from vault_search.parsers import parse_file
"""

import logging
from pathlib import Path

from vault_search.type_defs import ChunkRecord, LinkRecord, ParseResult, ParseStatus

logger = logging.getLogger(__name__)


def parse_file(
    file_path: Path, vault_path: Path
) -> tuple[list[ChunkRecord], list[LinkRecord], list[str]]:
    """
    Dispatcher que roteia parsing por extensão do arquivo.

    Fault-tolerant: exceções de parsers individuais são logadas
    e retornam listas vazias, não propagam para o caller.

    Parâmetros:
        file_path: caminho absoluto do arquivo
        vault_path: caminho raiz do vault

    Retorna:
        Tuple com:
        - Lista de ChunkRecord prontos para inserção no LanceDB
        - Lista de LinkRecord para inserção no links_index
        - Lista de aliases do frontmatter
    """
    result = parse_file_result(file_path, vault_path)
    return result.chunks, result.links, result.aliases


def parse_file_result(file_path: Path, vault_path: Path) -> ParseResult:
    """Parseia um arquivo preservando a distinção entre vazio e erro."""
    # Lazy import para evitar imports circulares
    from vault_search.parsers.canvas import parse_canvas
    from vault_search.parsers.markdown import parse_note
    from vault_search.parsers.mdx import parse_mdx
    from vault_search.parsers.pdf import parse_pdf

    ext = file_path.suffix.lower()

    try:
        # Markdown e variantes retornam tuple completo
        if ext in (".md", ".txt"):
            chunks, links, aliases = parse_note(file_path, vault_path, raise_on_error=True)

        elif ext == ".mdx":
            chunks, links, aliases = parse_mdx(file_path, vault_path, raise_on_error=True)

        # Canvas e PDF não têm links internos no mesmo formato
        elif ext == ".canvas":
            chunks = parse_canvas(file_path, vault_path, raise_on_error=True)
            links, aliases = [], []

        elif ext == ".pdf":
            chunks = parse_pdf(file_path, vault_path, raise_on_error=True)
            links, aliases = [], []

        else:
            return ParseResult(status=ParseStatus.UNSUPPORTED)

    except Exception as e:
        logger.warning(
            "Falha ao parsear arquivo (error_type=%s)",
            type(e).__name__,
        )
        return ParseResult(status=ParseStatus.ERROR, error_type=type(e).__name__)

    status = ParseStatus.SUCCESS if chunks else ParseStatus.EMPTY
    return ParseResult(
        status=status,
        chunks=chunks,
        links=links,
        aliases=aliases,
    )
