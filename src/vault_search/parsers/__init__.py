"""
Parsers for supported file formats.

Direct imports:
    from vault_search.parsers.markdown import parse_note, split_by_headers
    from vault_search.parsers.frontmatter import parse_frontmatter, extract_tags
    from vault_search.parsers.canvas import parse_canvas
    from vault_search.parsers.pdf import parse_pdf

Use ``parse_file()`` for extension-based dispatch:
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
    Dispatch parsing by file extension.

    Log individual parser failures and return explicit error results instead
    of propagating exceptions to callers.

    Parameters:
        file_path: Absolute file path.
        vault_path: Vault root path.

    Returns:
        Chunks ready for LanceDB, links ready for ``links_index``, and
        frontmatter aliases.
    """
    result = parse_file_result(file_path, vault_path)
    return result.chunks, result.links, result.aliases


def parse_file_result(file_path: Path, vault_path: Path) -> ParseResult:
    """Parse a file while distinguishing empty content from an error."""
    # Import lazily to avoid cycles.
    from vault_search.parsers.canvas import parse_canvas
    from vault_search.parsers.markdown import parse_note
    from vault_search.parsers.mdx import parse_mdx
    from vault_search.parsers.pdf import parse_pdf

    ext = file_path.suffix.lower()

    try:
        # Markdown variants return the complete tuple.
        if ext in (".md", ".txt"):
            chunks, links, aliases = parse_note(file_path, vault_path, raise_on_error=True)

        elif ext == ".mdx":
            chunks, links, aliases = parse_mdx(file_path, vault_path, raise_on_error=True)

        # Canvas and PDF do not expose internal links in the same format.
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
            "Failed to parse file (error_type=%s)",
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
