"""
Parser for Obsidian Canvas files.

Canvas uses the JSON Canvas 1.0 specification:
- nodes: Canvas cards (text, file, link, group)
- edges: connections between nodes

For semantic search, extract:
- Text from ``text`` nodes containing Markdown
- labels from group nodes
- edge labels
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
    Process a .canvas file into chunks with metadata.

    Parameters:
        canvas_path: Absolute .canvas file path.
        vault_path: Vault root path.

    Returns:
        ``ChunkRecord`` entries ready for LanceDB.
    """
    # Read metadata first to validate existence and capture mtime.
    try:
        meta = extract_file_metadata(canvas_path, vault_path)
    except (OSError, ValueError) as e:
        if raise_on_error:
            raise
        logger.warning(
            "Failed to access canvas (error_type=%s)",
            type(e).__name__,
        )
        return []

    try:
        raw = canvas_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        if raise_on_error:
            raise
        logger.warning(
            "Failed to read canvas (error_type=%s)",
            type(e).__name__,
        )
        return []

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        if raise_on_error:
            raise
        logger.warning(
            "Canvas contains invalid JSON (error_type=%s)",
            type(e).__name__,
        )
        return []

    if not isinstance(data, dict):
        if raise_on_error:
            raise ValueError("invalid canvas structure")
        logger.warning("Canvas has an unexpected structure")
        return []

    chunks: list[ChunkRecord] = []

    # Extract text nodes.
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

    # Extract edge labels.
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
