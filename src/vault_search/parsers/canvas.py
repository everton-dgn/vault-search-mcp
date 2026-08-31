"""
Parser de arquivos Canvas (.canvas) do Obsidian.

O formato Canvas usa a especificação JSON Canvas 1.0:
- nodes: cards no canvas (text, file, link, group)
- edges: conexões entre nodes

Para busca semântica, extraímos:
- text de nodes tipo "text" (conteúdo Markdown)
- label de nodes tipo "group"
- label de edges
"""

import json
import logging
from pathlib import Path

from vault_search.type_defs import ChunkRecord
from vault_search.utils.chunking import chunk_and_collect
from vault_search.utils.metadata import extract_file_metadata

logger = logging.getLogger(__name__)


def parse_canvas(
    canvas_path: Path,
    vault_path: Path,
    *,
    raise_on_error: bool = False,
) -> list[ChunkRecord]:
    """
    Processa um arquivo .canvas e retorna lista de chunks com metadados.

    Parâmetros:
        canvas_path: caminho absoluto do arquivo .canvas
        vault_path: caminho raiz do vault

    Retorna:
        Lista de ChunkRecord prontos para inserção no LanceDB.
    """
    # Extrair metadados primeiro (valida existência e captura mtime)
    try:
        meta = extract_file_metadata(canvas_path, vault_path)
    except (OSError, ValueError) as e:
        if raise_on_error:
            raise
        logger.warning(
            "Falha ao acessar canvas (error_type=%s)",
            type(e).__name__,
        )
        return []

    try:
        raw = canvas_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        if raise_on_error:
            raise
        logger.warning(
            "Falha ao ler canvas (error_type=%s)",
            type(e).__name__,
        )
        return []

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        if raise_on_error:
            raise
        logger.warning(
            "Canvas com JSON inválido (error_type=%s)",
            type(e).__name__,
        )
        return []

    if not isinstance(data, dict):
        if raise_on_error:
            raise ValueError("estrutura de canvas inválida")
        logger.warning("Canvas com estrutura inesperada")
        return []

    chunks: list[ChunkRecord] = []

    # Extrair text nodes
    for node in data.get("nodes", []):
        if not isinstance(node, dict):
            continue

        node_type = node.get("type")
        node_id = node.get("id", "?")

        if node_type == "text":
            raw_text = node.get("text", "")
            text = str(raw_text).strip() if raw_text else ""
            if not text:
                continue
            chunk_and_collect(text, f"Text node: {node_id}", meta, chunks)

        elif node_type == "group":
            raw_label = node.get("label", "")
            label = str(raw_label).strip() if raw_label else ""
            if not label:
                continue
            chunk_and_collect(label, f"Group: {label}", meta, chunks)

    # Extrair edge labels
    for edge in data.get("edges", []):
        if not isinstance(edge, dict):
            continue
        raw_label = edge.get("label", "")
        label = str(raw_label).strip() if raw_label else ""
        if not label:
            continue
        from_node = edge.get("fromNode", "?")
        to_node = edge.get("toNode", "?")
        chunk_and_collect(label, f"Edge: {from_node} → {to_node}", meta, chunks)

    return chunks
