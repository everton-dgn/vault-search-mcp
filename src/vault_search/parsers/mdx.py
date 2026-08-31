"""
Parser de arquivos MDX (Markdown + JSX).

Remove imports, exports e componentes JSX antes de parsear como markdown.
Útil para documentação de projetos Next.js, Docusaurus, etc.
"""

import logging
import re
from pathlib import Path

from vault_search.parsers.frontmatter import (
    extract_frontmatter_fields,
    extract_tags,
    parse_frontmatter,
)
from vault_search.parsers.markdown import extract_aliases, split_by_headers
from vault_search.type_defs import ChunkRecord, LinkRecord
from vault_search.utils.chunking import chunk_and_collect
from vault_search.utils.links import (
    extract_all_links,
    extract_link_context,
    normalize_link_target,
)
from vault_search.utils.metadata import FileMetadata, extract_file_metadata

logger = logging.getLogger(__name__)

# Regex patterns para limpar MDX
_IMPORT_PATTERN = re.compile(r"^import\s+.+$", re.MULTILINE)
_EXPORT_PATTERN = re.compile(
    r"^export\s+(?:default\s+)?(?:const\s+|let\s+|var\s+|function\s+|class\s+)?.+$", re.MULTILINE
)
_JSX_SELF_CLOSING = re.compile(r"<[A-Z][a-zA-Z0-9]*(?:\s+[^>]*)?\s*/>")
_JSX_BLOCK = re.compile(r"<([A-Z][a-zA-Z0-9]*)[^>]*>[\s\S]*?</\1>")


def clean_mdx(content: str) -> str:
    """
    Remove sintaxe JSX/ESM do conteúdo MDX, preservando markdown.

    Remove:
    - import statements
    - export statements
    - Componentes JSX self-closing: <Component />
    - Componentes JSX com children: <Component>...</Component>

    Parâmetros:
        content: conteúdo MDX bruto

    Retorna:
        Conteúdo limpo (markdown puro).
    """
    # Remove imports
    content = _IMPORT_PATTERN.sub("", content)

    # Remove exports
    content = _EXPORT_PATTERN.sub("", content)

    # Remove JSX self-closing
    content = _JSX_SELF_CLOSING.sub("", content)

    # Remove JSX blocks (pode precisar múltiplas passadas para aninhados)
    prev_len = -1
    while len(content) != prev_len:
        prev_len = len(content)
        content = _JSX_BLOCK.sub("", content)

    return content


def parse_mdx(
    mdx_path: Path,
    vault_path: Path,
    *,
    raise_on_error: bool = False,
) -> tuple[list[ChunkRecord], list[LinkRecord], list[str]]:
    """
    Processa um arquivo MDX e retorna chunks, links e aliases.

    Limpa sintaxe JSX antes de parsear como markdown.

    Parâmetros:
        mdx_path: caminho absoluto do arquivo MDX
        vault_path: caminho raiz do vault

    Retorna:
        Tuple com chunks, links e aliases.
    """
    try:
        meta = extract_file_metadata(mdx_path, vault_path)
    except (OSError, ValueError) as e:
        if raise_on_error:
            raise
        logger.warning(
            "Falha ao acessar MDX (error_type=%s)",
            type(e).__name__,
        )
        return [], [], []

    try:
        content = mdx_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        if raise_on_error:
            raise
        logger.warning(
            "Falha ao ler MDX (error_type=%s)",
            type(e).__name__,
        )
        return [], [], []

    # Limpar JSX antes de parsear frontmatter/markdown
    cleaned_content = clean_mdx(content)

    frontmatter, body = parse_frontmatter(cleaned_content)

    tags = extract_tags(frontmatter)
    tags_str = ", ".join(tags) if tags else ""

    fm_fields = extract_frontmatter_fields(frontmatter)
    aliases = extract_aliases(frontmatter)

    raw_title = frontmatter.get("title", meta["title"])
    if isinstance(raw_title, str):
        title = raw_title
    elif isinstance(raw_title, list) and raw_title:
        title = str(raw_title[0])
    elif raw_title is not None:
        title = str(raw_title)
    else:
        title = meta["title"]
    note_meta: FileMetadata = {**meta, "title": title}

    # Extrair links do body
    all_links = extract_all_links(body, include_external=True)
    links: list[LinkRecord] = []

    for wl in all_links["wikilinks"]:
        links.append(
            {
                "from_note_path": meta["relative_path"],
                "from_note_title": title,
                "link_type": "wikilink",
                "link_target": wl["target"],
                "link_target_normalized": normalize_link_target(wl["target"]),
                "to_note_path": "",
                "is_resolved": False,
                "alias": wl.get("alias") or "",
                "heading": wl.get("heading") or "",
                "block_ref": wl.get("block_ref") or "",
                "context": extract_link_context(body, wl["raw"]),
                "modified_at": meta["modified_at"],
            }
        )

    for ml in all_links["markdown_links"]:
        links.append(
            {
                "from_note_path": meta["relative_path"],
                "from_note_title": title,
                "link_type": "markdown",
                "link_target": ml["target"],
                "link_target_normalized": normalize_link_target(ml["target"]),
                "to_note_path": "",
                "is_resolved": False,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": extract_link_context(body, ml["raw"]),
                "modified_at": meta["modified_at"],
            }
        )

    for ext in all_links.get("external", []):
        links.append(
            {
                "from_note_path": meta["relative_path"],
                "from_note_title": title,
                "link_type": "external",
                "link_target": ext["url"],
                "link_target_normalized": "",
                "to_note_path": "",
                "is_resolved": True,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": extract_link_context(body, ext["raw"]),
                "modified_at": meta["modified_at"],
            }
        )

    sections = split_by_headers(body)
    chunks: list[ChunkRecord] = []

    for section in sections:
        headers = " > ".join(section["headers"]) if section["headers"] else ""
        chunk_and_collect(section["content"], headers, note_meta, chunks, tags_str, fm_fields)

    return chunks, links, aliases
