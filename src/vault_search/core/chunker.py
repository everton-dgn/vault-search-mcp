"""
Hierarchical text chunking with overlap.

Split long text into chunks at natural paragraph, line, sentence,
and word boundaries with controlled overlap.
"""

from vault_search.config.chunking import CHUNK_SEPARATORS


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    Split text into overlapping chunks.

    Apply overlap only at the top level. This prevents overlap from
    accumulating across recursion levels and exceeding ``chunk_size``.

    Parameters:
        text: Text to split.
        chunk_size: Maximum chunk size, including overlap.
        overlap: Overlap between chunks.

    Returns:
        Text chunks.
    """
    if len(text) <= chunk_size:
        return [text]

    # Perform the hierarchical split without overlap.
    chunks = _chunk_with_separators(text, chunk_size, CHUNK_SEPARATORS, 0)

    # Apply overlap only here at the top level.
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prefix = _get_overlap_prefix(chunks[i - 1], overlap)
            # Ensure the prefix and chunk do not exceed chunk_size.
            max_prefix = max(0, chunk_size - len(chunks[i]))
            if len(prefix) > max_prefix:
                prefix = prefix[-max_prefix:] if max_prefix > 0 else ""
            overlapped.append(prefix + chunks[i])
        return overlapped

    return chunks


def _chunk_with_separators(
    text: str, chunk_size: int, separators: list[str], sep_idx: int
) -> list[str]:
    """
    Recursively implement hierarchical chunking without overlap.

    Split on the current separator. Recursively apply the next separator
    to parts that still exceed ``chunk_size``.

    Parameters:
        text: Text to split.
        chunk_size: Maximum size.
        separators: Hierarchical separator list.
        sep_idx: Current separator index.
    """
    if len(text) <= chunk_size:
        return [text]

    # Fall back to hard cuts after exhausting all separators.
    if sep_idx >= len(separators):
        chunks = []
        for i in range(0, len(text), chunk_size):
            chunks.append(text[i : i + chunk_size])
        return chunks

    sep = separators[sep_idx]
    parts = text.split(sep)

    if len(parts) <= 1:
        # Try the next separator when this one does not split the text.
        return _chunk_with_separators(text, chunk_size, separators, sep_idx + 1)

    # Group parts into chunks that respect chunk_size.
    raw_chunks = []
    current = ""

    for part in parts:
        candidate = current + sep + part if current else part
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                raw_chunks.append(current)
            current = part

    if current:
        raw_chunks.append(current)

    # Recursively split any raw chunk that still exceeds chunk_size.
    final_chunks = []
    for rc in raw_chunks:
        if len(rc) > chunk_size:
            sub = _chunk_with_separators(rc, chunk_size, separators, sep_idx + 1)
            final_chunks.extend(sub)
        else:
            final_chunks.append(rc)

    return final_chunks


def _get_overlap_prefix(text: str, overlap: int) -> str:
    """
    Extract approximately the last ``overlap`` characters while respecting
    word boundaries.

    Parameters:
        text: Previous chunk text.
        overlap: Target number of characters.

    Returns:
        A suffix that respects a word boundary.
    """
    if len(text) <= overlap:
        return text

    candidate = text[-overlap:]
    # Find the first space to avoid cutting a word.
    space_idx = candidate.find(" ")
    if space_idx > 0 and space_idx < len(candidate) - 1:
        return candidate[space_idx + 1 :]
    return candidate
