"""
YAML frontmatter parser and tag extraction.

Read only through the closing frontmatter delimiter instead of loading
entire files into memory.
"""

import re
from pathlib import Path
from typing import Any

import yaml

# Maximum frontmatter read size. Larger frontmatter is treated as invalid.
FRONTMATTER_MAX_BYTES = 64 * 1024

# Chunk size for incremental reads.
FRONTMATTER_CHUNK_SIZE = 4 * 1024

# Detect a standalone ``---`` at the beginning of a line.
_FRONTMATTER_RE = re.compile(r"^---\s*$", re.MULTILINE)

# PyYAML may produce scalars, dates, and nested collections. Keep the open type
# inside this parsing boundary and normalize extractor output.
type Frontmatter = dict[str, Any]


def parse_frontmatter(content: str) -> tuple[Frontmatter, str]:
    """
    Extract YAML frontmatter from the beginning of a Markdown file.

    Accept only dictionaries; scalar and list YAML values return an empty mapping.

    Parameters:
        content: Complete Markdown file content.

    Returns:
        A ``(metadata, body_without_frontmatter)`` tuple. When frontmatter is
        absent, return ``({}, content)``.
    """
    content = content.lstrip("\ufeff")  # Remove a leading BOM.

    matches = list(_FRONTMATTER_RE.finditer(content))
    if len(matches) < 2:
        return {}, content

    # The first delimiter must be at the start, allowing leading whitespace.
    first = matches[0]
    if first.start() != 0 and content[: first.start()].strip():
        return {}, content

    second = matches[1]
    yaml_text = content[first.end() : second.start()]
    body = content[second.end() :].strip()

    try:
        metadata = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError:
        metadata = {}

    # PyYAML may return a string, integer, or list; only a dictionary is valid.
    if not isinstance(metadata, dict):
        return {}, body

    return metadata, body


def extract_tags(metadata: Frontmatter) -> list[str]:
    """
    Extract tags from common Obsidian frontmatter formats.

    Parameters:
        metadata: Frontmatter dictionary.

    Returns:
        Tags as strings.
    """
    tags = metadata.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    elif isinstance(tags, list):
        tags = [str(t).strip() for t in tags]
    else:
        tags = []
    return [t for t in tags if t]


def extract_frontmatter_fields(metadata: Frontmatter) -> dict[str, str]:
    """
    Extract structured frontmatter fields for indexing.

    Supported fields:
    - ``id``: unique UUID v7
    - ``created_at`` / ``created`` / ``date``: creation date
    - ``updated_at`` / ``updated`` / ``modified``: last-update date
    - ``description`` / ``summary`` / ``excerpt``: note description
    - status: draft, review, published, archived
    - note_type / type: daily, weekly, monthly, yearly, meeting, idea, task, person
    - category / categories: work, personal, reference, project
    - ``project``: project name
    - ``source`` / ``url`` / ``link``: source URL or reference

    Parameters:
        metadata: Frontmatter dictionary.

    Returns:
        Non-empty extracted fields.
    """
    fields: dict[str, str] = {}

    # id: unique note UUID
    if note_id := metadata.get("id"):
        fields["id"] = str(note_id)

    # created_at: common aliases
    created = metadata.get("created_at") or metadata.get("created") or metadata.get("date")
    if created:
        # YAML may return a datetime; normalize to a bounded ISO string.
        fields["created_at"] = str(created)[:19]

    # updated_at: common aliases
    updated = metadata.get("updated_at") or metadata.get("updated") or metadata.get("modified")
    if updated:
        fields["updated_at"] = str(updated)[:19]  # Bound to ISO datetime length

    # description: common aliases
    description = metadata.get("description") or metadata.get("summary") or metadata.get("excerpt")
    if description and isinstance(description, str):
        fields["description"] = description[:500]  # Bound field size.

    # status
    status = metadata.get("status")
    if status and isinstance(status, str):
        fields["status"] = status.lower().strip()

    # ``type`` is a common Obsidian alias for note_type.
    note_type = metadata.get("note_type") or metadata.get("type")
    if note_type and isinstance(note_type, str):
        fields["note_type"] = note_type.lower().strip()

    # category may be a string or a list.
    category = metadata.get("category") or metadata.get("categories")
    if category:
        if isinstance(category, list):
            category = ", ".join(str(c).strip() for c in category if c)
        elif isinstance(category, str):
            category = category.strip()
        else:
            category = str(category)
        if category:
            fields["category"] = category.lower()

    # project
    project = metadata.get("project")
    if project and isinstance(project, str):
        fields["project"] = project.strip()

    # source: URL or reference
    source = metadata.get("source") or metadata.get("url") or metadata.get("link")
    if source and isinstance(source, str):
        fields["source"] = source.strip()[:500]  # Bound URL size.

    return fields


def read_frontmatter_only(file_path: Path) -> tuple[Frontmatter, int]:
    """
    Read frontmatter without loading the entire file.

    Read incrementally and stop at the second ``---`` delimiter. This bounds
    memory use when only metadata is needed from large files.

    Parameters:
        file_path: Markdown file path.

    Returns:
        A ``(frontmatter, bytes_read)`` tuple. Invalid or absent frontmatter
        returns an empty mapping with the measured byte count.
    """
    bytes_read = 0
    buffer = ""

    try:
        with open(file_path, encoding="utf-8") as f:
            # Read the first chunk.
            chunk = f.read(FRONTMATTER_CHUNK_SIZE)
            if not chunk:
                return {}, 0

            buffer = chunk.lstrip("\ufeff")  # Remove BOM
            bytes_read = len(chunk.encode("utf-8"))

            # Check whether the file starts with the delimiter.
            if not buffer.lstrip().startswith("---"):
                return {}, bytes_read

            # Search incrementally for the closing delimiter.
            while True:
                matches = list(_FRONTMATTER_RE.finditer(buffer))

                if len(matches) >= 2:
                    # Found both opening and closing delimiters.
                    first = matches[0]
                    second = matches[1]

                    # Validate that the first delimiter is at the beginning.
                    if first.start() != 0 and buffer[: first.start()].strip():
                        return {}, bytes_read

                    yaml_text = buffer[first.end() : second.start()]
                    try:
                        metadata = yaml.safe_load(yaml_text) or {}
                    except yaml.YAMLError:
                        return {}, bytes_read

                    if not isinstance(metadata, dict):
                        return {}, bytes_read

                    return metadata, bytes_read

                # Enforce the safety limit.
                if bytes_read >= FRONTMATTER_MAX_BYTES:
                    return {}, bytes_read

                # Read the next chunk.
                chunk = f.read(FRONTMATTER_CHUNK_SIZE)
                if not chunk:
                    # EOF before a closing delimiter.
                    return {}, bytes_read

                buffer += chunk
                bytes_read += len(chunk.encode("utf-8"))

    except OSError, UnicodeDecodeError:
        return {}, bytes_read
