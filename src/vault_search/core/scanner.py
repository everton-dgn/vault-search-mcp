"""
Scanner de arquivos do vault Obsidian.

Escaneia o vault e retorna lista de arquivos indexáveis,
filtrando por extensão, pastas ignoradas e consistência de symlinks.
"""

import logging
from pathlib import Path

from vault_search.config.search import IGNORED_FOLDERS, INDEXABLE_EXTENSIONS

logger = logging.getLogger(__name__)


def scan_vault(vault_path: Path) -> list[Path]:
    """
    Escaneia o vault e retorna lista de arquivos indexáveis.

    Filtra symlinks que apontam para fora do vault e compara
    extensões case-insensitive (.MD, .Md são aceitos).

    Parâmetros:
        vault_path: caminho raiz do vault

    Retorna:
        Lista de Paths para arquivos indexáveis (.md, .pdf, .canvas).
    """
    resolved_vault = vault_path.resolve()
    files = []
    for path in vault_path.rglob("*"):
        if not path.is_file():
            continue
        # Extensão case-insensitive
        if path.suffix.lower() not in INDEXABLE_EXTENSIONS:
            continue
        if any(ignored in path.parts for ignored in IGNORED_FOLDERS):
            continue
        # Symlinks: verificar se target está dentro do vault
        if path.is_symlink():
            try:
                if not path.resolve().is_relative_to(resolved_vault):
                    logger.debug("scanner_skipped_symlink reason=outside_vault")
                    continue
            except OSError, ValueError:
                continue
        files.append(path)
    return files
