"""
Utilities for extracting file metadata.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict


class FileMetadata(TypedDict):
    """Common metadata extracted from a vault file."""

    relative_path: str
    folder: str
    title: str
    modified_at: str


def is_empty_text(text: str | None) -> bool:
    """
    Check whether text is empty or contains only whitespace.

    Parameters:
        text: Text to inspect; may be ``None``.

    Returns:
        ``True`` for empty, ``None``, or whitespace-only text.
    """
    return not text or not text.strip()


def normalize_title(raw_title: str | list[Any] | int | None, fallback: str) -> str:
    """
    Normalize a title extracted from frontmatter.

    Handles:
    - title: "String" → "String"
    - title: ["List", "Item"] → "List"
    - title: 123 → "123"
    - title: null/missing → fallback

    Parameters:
        raw_title: Raw ``title`` field value of any supported type.
        fallback: Value used when ``title`` is invalid, usually ``file.stem``.

    Returns:
        The normalized string.
    """
    if isinstance(raw_title, str):
        return raw_title if raw_title.strip() else fallback
    if isinstance(raw_title, list):
        if not raw_title:  # Empty list
            return fallback
        first = raw_title[0]
        return str(first) if first else fallback
    if raw_title is not None:
        return str(raw_title)
    return fallback


def extract_file_metadata(file_path: Path, vault_path: Path) -> FileMetadata:
    """
    Extract common metadata from a vault file.

    Parameters:
        file_path: Absolute file path.
        vault_path: Vault root path.

    Returns:
        ``FileMetadata`` containing ``relative_path``, ``folder``, ``title``,
        and ``modified_at``.
    """
    relative_path = str(file_path.relative_to(vault_path))
    folder = str(file_path.parent.relative_to(vault_path))
    if folder == ".":
        folder = ""
    title = file_path.stem
    modified_at = datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
    return {
        "relative_path": relative_path,
        "folder": folder,
        "title": title,
        "modified_at": modified_at,
    }
