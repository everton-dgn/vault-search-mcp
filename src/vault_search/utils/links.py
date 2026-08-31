"""
Utilitários para extração e análise de links em notas markdown.

Suporta:
- Wikilinks: [[nota]] ou [[nota|alias]]
- Markdown links: [texto](path.md) ou [texto](path)
- Embeds: ![[imagem.png]] ou ![[nota]]
"""

import re
from pathlib import Path
from typing import Literal, TypedDict, overload


class WikilinkParts(TypedDict):
    """Partes normalizadas de um wikilink."""

    target: str
    alias: str | None
    heading: str | None
    block_ref: str | None


class WikilinkInfo(WikilinkParts):
    """Wikilink extraído com sua representação original."""

    raw: str


class MarkdownLinkInfo(TypedDict):
    """Link Markdown interno extraído."""

    raw: str
    text: str
    target: str


class EmbedInfo(TypedDict):
    """Embed Obsidian extraído."""

    raw: str
    target: str


class ExternalLinkInfo(TypedDict):
    """Link externo extraído."""

    raw: str
    url: str
    text: str | None


class ExtractedLinks(TypedDict):
    """Coleções de links internos extraídos."""

    wikilinks: list[WikilinkInfo]
    markdown_links: list[MarkdownLinkInfo]
    embeds: list[EmbedInfo]


class ExtractedLinksWithExternal(ExtractedLinks):
    """Coleções de links com URLs externas."""

    external: list[ExternalLinkInfo]


# Regex para wikilinks: [[nota]] ou [[nota|alias]] ou [[nota#heading]]
# Captura o target (antes de | ou #)
# Usa negative lookbehind (?<!!) para excluir embeds (![[...]])
WIKILINK_PATTERN = re.compile(r"(?<!!)\[\[([^\]|#]+)(?:[|#][^\]]*)?]]", re.MULTILINE)

# Regex para markdown links: [texto](path) ou [texto](path.md)
# Ignora URLs externas (http://, https://, mailto:, etc.)
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((?!https?://|mailto:|#)([^)]+)\)", re.MULTILINE)

# Regex para embeds: ![[arquivo]] ou ![[arquivo|size]]
EMBED_PATTERN = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?]]", re.MULTILINE)


def extract_wikilinks(text: str | None) -> list[str]:
    """
    Extrai wikilinks de um texto markdown.

    Parâmetros:
        text: conteúdo markdown

    Retorna:
        Lista de targets únicos (sem duplicatas, lowercase para comparação).
    """
    if not text:
        return []

    matches = WIKILINK_PATTERN.findall(text)
    # Normalizar: remover espaços extras, converter para lowercase para comparação
    seen = set()
    result = []
    for match in matches:
        normalized = match.strip()
        lower = normalized.lower()
        if lower not in seen:
            seen.add(lower)
            result.append(normalized)
    return result


def extract_markdown_links(text: str | None) -> list[str]:
    """
    Extrai markdown links internos de um texto.

    Ignora links externos (http://, https://, mailto:).

    Parâmetros:
        text: conteúdo markdown

    Retorna:
        Lista de paths únicos.
    """
    if not text:
        return []

    matches = MARKDOWN_LINK_PATTERN.findall(text)
    seen = set()
    result = []
    for _, path in matches:
        # Remover âncoras (#section)
        clean_path = path.split("#")[0].strip()
        if clean_path and clean_path.lower() not in seen:
            seen.add(clean_path.lower())
            result.append(clean_path)
    return result


def extract_embeds(text: str | None) -> list[str]:
    """
    Extrai embeds (![[arquivo]]) de um texto markdown.

    Parâmetros:
        text: conteúdo markdown

    Retorna:
        Lista de arquivos únicos embedados.
    """
    if not text:
        return []

    matches = EMBED_PATTERN.findall(text)
    seen = set()
    result = []
    for match in matches:
        normalized = match.strip()
        lower = normalized.lower()
        if lower not in seen:
            seen.add(lower)
            result.append(normalized)
    return result


@overload
def extract_all_links(text: str, include_external: Literal[False] = False) -> ExtractedLinks: ...


@overload
def extract_all_links(text: str, include_external: Literal[True]) -> ExtractedLinksWithExternal: ...


@overload
def extract_all_links(
    text: str, include_external: bool
) -> ExtractedLinks | ExtractedLinksWithExternal: ...


def extract_all_links(
    text: str, include_external: bool = False
) -> ExtractedLinks | ExtractedLinksWithExternal:
    """
    Extrai todos os tipos de links de um texto markdown.

    Parâmetros:
        text: conteúdo markdown
        include_external: se True, inclui URLs https://

    Retorna:
        Dict com:
        - wikilinks: lista de dicts com raw, target, e partes parseadas
        - markdown_links: lista de dicts com raw, text, target
        - embeds: lista de dicts com raw, target
        - external: lista de dicts com url (se include_external=True)
    """
    result: ExtractedLinks = {
        "wikilinks": [],
        "markdown_links": [],
        "embeds": [],
    }
    external_links: list[ExternalLinkInfo] = []

    if not text:
        return result

    # Wikilinks com estrutura completa
    # Padrão que captura o conteúdo completo entre [[...]]
    # Usa negative lookbehind (?<!!) para excluir embeds (![[...]])
    wikilink_full_pattern = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
    seen_wikilinks = set()

    for match in wikilink_full_pattern.finditer(text):
        raw = match.group(0)  # [[...]]
        inner = match.group(1)  # conteúdo entre [[]]

        parts = parse_wikilink_parts(inner)
        target_lower = parts["target"].lower() if parts["target"] else ""

        if target_lower and target_lower not in seen_wikilinks:
            seen_wikilinks.add(target_lower)
            result["wikilinks"].append(
                {
                    "raw": raw,
                    "target": parts["target"],
                    "alias": parts["alias"],
                    "heading": parts["heading"],
                    "block_ref": parts["block_ref"],
                }
            )

    # Embeds (![[...]])
    embed_full_pattern = re.compile(r"!\[\[([^\]]+)\]\]")
    seen_embeds = set()

    for match in embed_full_pattern.finditer(text):
        raw = match.group(0)
        inner = match.group(1)

        # Embeds podem ter |size, extrair só o target
        target = inner.split("|")[0].strip()
        target_lower = target.lower()

        if target_lower and target_lower not in seen_embeds:
            seen_embeds.add(target_lower)
            result["embeds"].append(
                {
                    "raw": raw,
                    "target": target,
                }
            )

    # Markdown links internos [text](path)
    # Padrão que ignora URLs externas
    md_link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    seen_md_links = set()

    for match in md_link_pattern.finditer(text):
        raw = match.group(0)
        link_text = match.group(1)
        href = match.group(2).strip()

        # Verificar se é externo
        is_external = href.startswith(("http://", "https://", "mailto:", "tel:", "ftp://"))

        if is_external:
            if include_external and href not in seen_md_links:
                seen_md_links.add(href)
                external_links.append(
                    {
                        "raw": raw,
                        "url": href,
                        "text": link_text,
                    }
                )
        else:
            # Link interno - remover âncora para normalização
            target = href.split("#")[0].strip()
            target_lower = target.lower()

            if target and target_lower not in seen_md_links:
                seen_md_links.add(target_lower)
                result["markdown_links"].append(
                    {
                        "raw": raw,
                        "text": link_text,
                        "target": target,
                    }
                )

    # URLs soltas (se include_external)
    if include_external:
        bare_url_pattern = re.compile(r"(?<![(\[])(https?://[^\s\)>\]\"\']+)")
        for match in bare_url_pattern.finditer(text):
            url = match.group(1)
            if url not in seen_md_links:
                seen_md_links.add(url)
                external_links.append(
                    {
                        "raw": url,
                        "url": url,
                        "text": None,
                    }
                )

    if include_external:
        return {**result, "external": external_links}
    return result


def normalize_link_target(target: str) -> str:
    """
    Normaliza um target de link para matching consistente.

    Transformações:
    - Lowercase
    - Remove extensões (.md, .canvas, .pdf, etc.)
    - Espaços → hífens
    - Strip whitespace

    Parâmetros:
        target: nome ou path do link

    Retorna:
        Target normalizado.

    Exemplos:
        "Meu Projeto" → "meu-projeto"
        "docs/API.md" → "docs/api"
        "  nota  " → "nota"
    """
    normalized = target.lower().strip()

    # Remover extensão se presente
    for ext in (".md", ".canvas", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        if normalized.endswith(ext):
            normalized = normalized[: -len(ext)]
            break

    # Normalizar espaços para hífens
    normalized = normalized.replace(" ", "-")

    return normalized


def parse_wikilink_parts(wikilink: str) -> WikilinkParts:
    """
    Parseia partes de um wikilink completo.

    Formatos suportados:
    - [[Nota]]
    - [[Nota|alias]]
    - [[Nota#Heading]]
    - [[Nota#Heading|alias]]
    - [[Nota^block]]
    - [[pasta/Nota]]

    Parâmetros:
        wikilink: conteúdo do wikilink (sem os colchetes)

    Retorna:
        Dict com target, alias, heading, block_ref.
    """
    result: WikilinkParts = {
        "target": wikilink,
        "alias": None,
        "heading": None,
        "block_ref": None,
    }

    working = wikilink

    # Extrair alias (|) - sempre no final
    if "|" in working:
        parts = working.split("|", 1)
        working = parts[0]
        result["alias"] = parts[1].strip() if parts[1].strip() else None

    # Extrair block ref (^)
    if "^" in working:
        parts = working.split("^", 1)
        working = parts[0]
        result["block_ref"] = parts[1].strip() if parts[1].strip() else None

    # Extrair heading (#)
    if "#" in working:
        parts = working.split("#", 1)
        working = parts[0]
        result["heading"] = parts[1].strip() if parts[1].strip() else None

    result["target"] = working.strip() if working.strip() else wikilink

    return result


def extract_link_context(content: str, link_text: str, window: int = 50) -> str:
    """
    Extrai trecho do conteúdo onde o link aparece.

    Parâmetros:
        content: conteúdo completo da nota
        link_text: texto do link para localizar
        window: caracteres antes/depois do link

    Retorna:
        Trecho com ... se truncado.
    """
    idx = content.find(link_text)
    if idx == -1:
        return ""

    start = max(0, idx - window)
    end = min(len(content), idx + len(link_text) + window)

    context = content[start:end].strip()

    # Adicionar elipses se truncado
    if start > 0:
        context = "..." + context
    if end < len(content):
        context = context + "..."

    # Limpar quebras de linha excessivas
    context = " ".join(context.split())

    return context


def matches_note(link_target: str, note_path: str) -> bool:
    """
    Verifica se um link target corresponde a uma nota.

    Compara usando normalize_link_target para ambos os lados.

    Parâmetros:
        link_target: target do link (ex: "minha nota", "pasta/nota")
        note_path: path da nota (ex: "pasta/minha-nota.md")

    Retorna:
        True se o link aponta para a nota.
    """
    note_path_obj = Path(note_path)
    note_stem = normalize_link_target(note_path_obj.stem)
    note_path_normalized = normalize_link_target(note_path)

    target_normalized = normalize_link_target(link_target)

    # Comparações
    return (
        target_normalized == note_stem
        or target_normalized == note_path_normalized
        or
        # Match parcial (target é só o nome, note_path é caminho completo)
        note_path_normalized.endswith("/" + target_normalized)
        or note_path_normalized.endswith("/" + target_normalized.replace("-", ""))
    )
