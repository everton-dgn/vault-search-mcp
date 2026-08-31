"""
Obsidian vault file scanner.

Scan the vault for indexable files while filtering by extension,
ignored folders, and symlink containment.
"""

import logging
from pathlib import Path

from vault_search.config.search import IGNORED_FOLDERS, INDEXABLE_EXTENSIONS

logger = logging.getLogger(__name__)


def scan_vault(vault_path: Path) -> list[Path]:
    """
    Scan the vault and return indexable files.

    Exclude symlinks that point outside the vault and compare extensions
    case-insensitively, accepting forms such as .MD and .Md.

    Parameters:
        vault_path: Vault root path.

    Returns:
        Paths for indexable files such as .md, .pdf, and .canvas.
    """
    resolved_vault = vault_path.resolve()
    files = []
    for path in vault_path.rglob("*"):
        if not path.is_file():
            continue
        # Compare extensions case-insensitively.
        if path.suffix.lower() not in INDEXABLE_EXTENSIONS:
            continue
        if any(ignored in path.parts for ignored in IGNORED_FOLDERS):
            continue
        # Accept symlinks only when their targets remain inside the vault.
        if path.is_symlink():
            try:
                if not path.resolve().is_relative_to(resolved_vault):
                    logger.debug("scanner_skipped_symlink reason=outside_vault")
                    continue
            except OSError, ValueError:
                continue
        files.append(path)
    return files
