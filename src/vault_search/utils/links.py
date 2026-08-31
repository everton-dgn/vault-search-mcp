"""
Utilities for extracting and analyzing links in Markdown notes.

Supports:
- Wikilinks: [[note]] or [[note|alias]]
- Markdown links: [text](path.md) or [text](path)
- Embeds: ![[image.png]] or ![[note]]
"""

import re
from pathlib import Path
from typing import Literal, TypedDict, overload


class WikilinkParts(TypedDict):
    """Normalized parts of a wikilink."""

    target: str
    alias: str | None
    heading: str | None
    block_ref: str | None


class WikilinkInfo(WikilinkParts):
    """Extracted wikilink with its original representation."""

    raw: str


class MarkdownLinkInfo(TypedDict):
    """Extracted internal Markdown link."""

    raw: str
    text: str
    target: str


class EmbedInfo(TypedDict):
    """Extracted Obsidian embed."""

    raw: str
    target: str


class ExternalLinkInfo(TypedDict):
    """Extracted external link."""

    raw: str
    url: str
    text: str | None


class ExtractedLinks(TypedDict):
    """Collections of extracted internal links."""

    wikilinks: list[WikilinkInfo]
    markdown_links: list[MarkdownLinkInfo]
    embeds: list[EmbedInfo]


class ExtractedLinksWithExternal(ExtractedLinks):
    """Link collections including external URLs."""

    external: list[ExternalLinkInfo]


# Wikilink regex: [[note]], [[note|alias]], or [[note#heading]].
# Capture the target before ``|`` or ``#``.
# Use a negative lookbehind to exclude embeds such as ![[...]].
WIKILINK_PATTERN = re.compile(r"(?<!!)\[\[([^\]|#]+)(?:[|#][^\]]*)?]]", re.MULTILINE)

# Markdown link regex: [text](path) or [text](path.md)
# Ignore external URLs such as http://, https://, and mailto:.
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((?!https?://|mailto:|#)([^)]+)\)", re.MULTILINE)

# Embed regex: ![[file]] or ![[file|size]]
EMBED_PATTERN = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?]]", re.MULTILINE)


def extract_wikilinks(text: str | None) -> list[str]:
    """
    Extract wikilinks from Markdown text.

    Parameters:
        text: Markdown content.

    Returns:
        Unique targets without duplicates, lowercase for comparison.
    """
    if not text:
        return []

    matches = WIKILINK_PATTERN.findall(text)
    # Remove extra spaces and lowercase values for comparison.
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
    Extract internal Markdown links from text.

    Ignore external links such as http://, https://, and mailto:.

    Parameters:
        text: Markdown content.

    Returns:
        Unique paths.
    """
    if not text:
        return []

    matches = MARKDOWN_LINK_PATTERN.findall(text)
    seen = set()
    result = []
    for _, path in matches:
        # Remove anchors such as #section.
        clean_path = path.split("#")[0].strip()
        if clean_path and clean_path.lower() not in seen:
            seen.add(clean_path.lower())
            result.append(clean_path)
    return result


def extract_embeds(text: str | None) -> list[str]:
    """
    Extract embeds such as ![[file]] from Markdown text.

    Parameters:
        text: Markdown content.

    Returns:
        Unique embedded files.
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
    Extract every supported link type from Markdown text.

    Parameters:
        text: Markdown content.
        include_external: Include HTTPS URLs when true.

    Returns:
        A dictionary containing:
        - ``wikilinks``: raw value, target, and parsed components
        - ``markdown_links``: raw value, text, and target
        - ``embeds``: raw value and target
        - ``external``: URL entries when ``include_external`` is true
    """
    result: ExtractedLinks = {
        "wikilinks": [],
        "markdown_links": [],
        "embeds": [],
    }
    external_links: list[ExternalLinkInfo] = []

    if not text:
        return result

    # Wikilinks with their complete structure.
    # Capture all content between [[...]].
    # Use a negative lookbehind to exclude embeds such as ![[...]].
    wikilink_full_pattern = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
    seen_wikilinks = set()

    for match in wikilink_full_pattern.finditer(text):
        raw = match.group(0)  # [[...]]
        inner = match.group(1)  # Content inside [[]]

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

        # Embeds may include |size; extract only the target.
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

    # Internal Markdown links: [text](path).
    # Ignore external URLs.
    md_link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    seen_md_links = set()

    for match in md_link_pattern.finditer(text):
        raw = match.group(0)
        link_text = match.group(1)
        href = match.group(2).strip()

        # Check whether the link is external.
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
            # Remove anchors from internal links before normalization.
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

    # Standalone URLs when include_external is enabled.
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
    Normalize a link target for consistent matching.

    Transformations:
    - Lowercase
    - Remove extensions such as .md, .canvas, and .pdf
    - Replace spaces with hyphens
    - Strip whitespace

    Parameters:
        target: link name or path

    Returns:
        Normalized target.

    Examples:
        "My Project" → "my-project"
        "docs/API.md" → "docs/api"
        "  note  " → "note"
    """
    normalized = target.lower().strip()

    # Remove an extension when present.
    for ext in (".md", ".canvas", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        if normalized.endswith(ext):
            normalized = normalized[: -len(ext)]
            break

    # Replace spaces with hyphens.
    normalized = normalized.replace(" ", "-")

    return normalized


def parse_wikilink_parts(wikilink: str) -> WikilinkParts:
    """
    Parse the components of a complete wikilink.

    Supported formats:
    - [[Note]]
    - [[Note|alias]]
    - [[Note#Heading]]
    - [[Note#Heading|alias]]
    - [[Note^block]]
    - [[folder/Note]]

    Parameters:
        wikilink: Wikilink content without brackets.

    Returns:
        A dictionary with ``target``, ``alias``, ``heading``, and ``block_ref``.
    """
    result: WikilinkParts = {
        "target": wikilink,
        "alias": None,
        "heading": None,
        "block_ref": None,
    }

    working = wikilink

    # Extract the trailing alias (|).
    if "|" in working:
        parts = working.split("|", 1)
        working = parts[0]
        result["alias"] = parts[1].strip() if parts[1].strip() else None

    # Extract the block reference (^).
    if "^" in working:
        parts = working.split("^", 1)
        working = parts[0]
        result["block_ref"] = parts[1].strip() if parts[1].strip() else None

    # Extract the heading (#).
    if "#" in working:
        parts = working.split("#", 1)
        working = parts[0]
        result["heading"] = parts[1].strip() if parts[1].strip() else None

    result["target"] = working.strip() if working.strip() else wikilink

    return result


def extract_link_context(content: str, link_text: str, window: int = 50) -> str:
    """
    Extract the content segment where a link appears.

    Parameters:
        content: Complete note content.
        link_text: Link text to locate.
        window: Number of characters before and after the link.

    Returns:
        A segment containing ellipses when truncated.
    """
    idx = content.find(link_text)
    if idx == -1:
        return ""

    start = max(0, idx - window)
    end = min(len(content), idx + len(link_text) + window)

    context = content[start:end].strip()

    # Add ellipses when truncated.
    if start > 0:
        context = "..." + context
    if end < len(content):
        context = context + "..."

    # Collapse excessive line breaks.
    context = " ".join(context.split())

    return context


def matches_note(link_target: str, note_path: str) -> bool:
    """
    Check whether a link target matches a note.

    Compare both values with ``normalize_link_target``.

    Parameters:
        link_target: Link target, for example "my note" or "folder/note".
        note_path: Note path, for example "folder/my-note.md".

    Returns:
        ``True`` when the link points to the note.
    """
    note_path_obj = Path(note_path)
    note_stem = normalize_link_target(note_path_obj.stem)
    note_path_normalized = normalize_link_target(note_path)

    target_normalized = normalize_link_target(link_target)

    # Comparisons.
    return (
        target_normalized == note_stem
        or target_normalized == note_path_normalized
        or
        # Partial match when the target is a name and note_path is a full path.
        note_path_normalized.endswith("/" + target_normalized)
        or note_path_normalized.endswith("/" + target_normalized.replace("-", ""))
    )
