"""
Parser de notas markdown do vault Obsidian.

Responsável por:
- Dividir corpo por headers markdown
- Processar nota completa em chunks com metadados
"""

import logging
import re
from pathlib import Path

from vault_search.config.chunking import MARKDOWN_HEADER_LEVELS
from vault_search.parsers.frontmatter import (
    extract_frontmatter_fields,
    extract_tags,
    parse_frontmatter,
)
from vault_search.type_defs import ChunkRecord, HeaderSection, LinkRecord
from vault_search.utils.chunking import chunk_and_collect
from vault_search.utils.links import (
    extract_all_links,
    extract_link_context,
    normalize_link_target,
)
from vault_search.utils.metadata import FileMetadata, extract_file_metadata

logger = logging.getLogger(__name__)


def split_by_headers(body: str) -> list[HeaderSection]:
    """
    Divide o corpo do markdown por headers, preservando a hierarquia.
    O texto de cada header é incluído no conteúdo da seção para
    melhorar a qualidade dos embeddings de busca.

    Parâmetros:
        body: corpo do markdown (sem frontmatter)

    Retorna:
        Lista de HeaderSection com 'headers' (hierarquia) e 'content' (inclui header).
    """
    pattern = re.compile(
        r"^(#{1," + str(MARKDOWN_HEADER_LEVELS) + r"})\s+(.+)$",
        re.MULTILINE,
    )

    sections: list[HeaderSection] = []
    current_headers: dict[int, str] = {}
    last_end = 0

    for match in pattern.finditer(body):
        # Salvar seção anterior (texto entre último header e este)
        if last_end < match.start():
            text = body[last_end : match.start()].strip()
            if text:
                headers_list = [current_headers[level] for level in sorted(current_headers.keys())]
                sections.append({"headers": list(headers_list), "content": text})

        level = len(match.group(1))
        title = match.group(2).strip()

        current_headers[level] = title
        for lv in list(current_headers.keys()):
            if lv > level:
                del current_headers[lv]

        # Incluir a linha do header no início do conteúdo da próxima seção
        last_end = match.start()

    remaining = body[last_end:].strip()
    if remaining:
        headers_list = [current_headers[level] for level in sorted(current_headers.keys())]
        sections.append({"headers": list(headers_list), "content": remaining})

    if not sections and body.strip():
        sections.append({"headers": [], "content": body.strip()})

    return sections


def extract_aliases(frontmatter: dict[str, object]) -> list[str]:
    """
    Extrai aliases do frontmatter.

    Suporta:
    - aliases: [a, b, c]
    - aliases: "a, b, c"
    - alias: "single"

    Parâmetros:
        frontmatter: dicionário do frontmatter

    Retorna:
        Lista de aliases.
    """
    aliases = []

    # Campo 'aliases' (lista ou string)
    raw_aliases = frontmatter.get("aliases", [])
    if isinstance(raw_aliases, str):
        aliases.extend([a.strip() for a in raw_aliases.split(",") if a.strip()])
    elif isinstance(raw_aliases, list):
        aliases.extend([str(a) for a in raw_aliases if a])

    # Campo 'alias' (singular)
    single_alias = frontmatter.get("alias")
    if single_alias:
        if isinstance(single_alias, str):
            aliases.append(single_alias)
        elif isinstance(single_alias, list):
            aliases.extend([str(a) for a in single_alias if a])

    return aliases


def parse_note(
    note_path: Path,
    vault_path: Path,
    *,
    raise_on_error: bool = False,
) -> tuple[list[ChunkRecord], list[LinkRecord], list[str]]:
    """
    Processa uma nota markdown e retorna chunks, links e aliases.

    Parâmetros:
        note_path: caminho absoluto da nota
        vault_path: caminho raiz do vault

    Retorna:
        Tuple com:
        - Lista de ChunkRecord prontos para inserção no LanceDB
        - Lista de LinkRecord para inserção no links_index
        - Lista de aliases do frontmatter
    """
    # Extrair metadados do filesystem primeiro (valida existência)
    try:
        meta = extract_file_metadata(note_path, vault_path)
    except (OSError, ValueError) as e:
        if raise_on_error:
            raise
        logger.warning(
            "Falha ao acessar nota (error_type=%s)",
            type(e).__name__,
        )
        return [], [], []

    try:
        content = note_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        if raise_on_error:
            raise
        logger.warning(
            "Falha ao ler nota (error_type=%s)",
            type(e).__name__,
        )
        return [], [], []

    frontmatter, body = parse_frontmatter(content)

    # Tags do frontmatter
    tags = extract_tags(frontmatter)
    tags_str = ", ".join(tags) if tags else ""

    # Campos estruturados do frontmatter
    fm_fields = extract_frontmatter_fields(frontmatter)

    # Aliases do frontmatter
    aliases = extract_aliases(frontmatter)

    # Título: frontmatter sobrescreve stem do arquivo
    raw_title = frontmatter.get("title", meta["title"])
    if isinstance(raw_title, str):
        title = raw_title
    elif isinstance(raw_title, list) and raw_title:
        first_item = raw_title[0]
        title = str(first_item) if first_item is not None else meta["title"]
    elif raw_title is not None:
        title = str(raw_title)
    else:
        title = meta["title"]
    note_meta: FileMetadata = {**meta, "title": title}

    # Extrair links do body completo
    all_links = extract_all_links(body, include_external=True)
    links: list[LinkRecord] = []

    # Processar wikilinks
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

    # Processar markdown links
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

    # Processar embeds
    for embed in all_links["embeds"]:
        links.append(
            {
                "from_note_path": meta["relative_path"],
                "from_note_title": title,
                "link_type": "embed",
                "link_target": embed["target"],
                "link_target_normalized": normalize_link_target(embed["target"]),
                "to_note_path": "",
                "is_resolved": False,
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": extract_link_context(body, embed["raw"]),
                "modified_at": meta["modified_at"],
            }
        )

    # Processar URLs externas
    for ext in all_links.get("external", []):
        links.append(
            {
                "from_note_path": meta["relative_path"],
                "from_note_title": title,
                "link_type": "external",
                "link_target": ext["url"],
                "link_target_normalized": "",  # URLs não precisam normalização
                "to_note_path": "",
                "is_resolved": True,  # URLs externas são sempre "resolvidas"
                "alias": "",
                "heading": "",
                "block_ref": "",
                "context": extract_link_context(body, ext["raw"]),
                "modified_at": meta["modified_at"],
            }
        )

    # Processar chunks
    sections = split_by_headers(body)
    chunks: list[ChunkRecord] = []

    for section in sections:
        headers = " > ".join(section["headers"]) if section["headers"] else ""
        chunk_and_collect(section["content"], headers, note_meta, chunks, tags_str, fm_fields)

    return chunks, links, aliases
