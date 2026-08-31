"""
Utilitários para extração de metadados de arquivos.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict


class FileMetadata(TypedDict):
    """Metadados comuns extraídos de um arquivo no vault."""

    relative_path: str
    folder: str
    title: str
    modified_at: str


def is_empty_text(text: str | None) -> bool:
    """
    Verifica se texto é vazio ou contém apenas whitespace.

    Parâmetros:
        text: texto a verificar (pode ser None)

    Retorna:
        True se vazio/None/whitespace, False caso contrário.
    """
    return not text or not text.strip()


def normalize_title(raw_title: str | list[Any] | int | None, fallback: str) -> str:
    """
    Normaliza título extraído de frontmatter.

    Handles:
    - title: "String" → "String"
    - title: ["List", "Item"] → "List"
    - title: 123 → "123"
    - title: null/missing → fallback

    Parâmetros:
        raw_title: valor bruto do campo title (pode ser qualquer tipo)
        fallback: valor de fallback se title for inválido (geralmente file.stem)

    Retorna:
        String normalizada.
    """
    if isinstance(raw_title, str):
        return raw_title if raw_title.strip() else fallback
    if isinstance(raw_title, list):
        if not raw_title:  # lista vazia
            return fallback
        first = raw_title[0]
        return str(first) if first else fallback
    if raw_title is not None:
        return str(raw_title)
    return fallback


def extract_file_metadata(file_path: Path, vault_path: Path) -> FileMetadata:
    """
    Extrai metadados comuns de um arquivo no vault.

    Parâmetros:
        file_path: caminho absoluto do arquivo
        vault_path: caminho raiz do vault

    Retorna:
        FileMetadata com relative_path, folder, title e modified_at.
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
