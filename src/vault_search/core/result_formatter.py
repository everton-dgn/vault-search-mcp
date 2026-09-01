"""
Search-result formatting.
"""

from collections.abc import Sequence

from vault_search.type_defs import SearchResult, SearchRow
from vault_search.utils.math import distance_to_score


def format_search_results(rows: Sequence[SearchRow]) -> list[SearchResult]:
    """
    Format LanceDB results into the public response shape.

    Parameters:
        rows: Reranked rows.

    Returns:
        Dictionaries with standardized fields.
    """
    if not rows:
        return []

    formatted: list[SearchResult] = []
    for row in rows:
        entry: SearchResult = {
            "note_path": row.get("note_path", ""),
            "note_title": row.get("note_title", ""),
            "folder": row.get("folder", ""),
            "headers": row.get("headers", ""),
            "tags": row.get("tags", ""),
            "text": row.get("text", ""),
        }

        if "rerank_score" in row:
            entry["score"] = row["rerank_score"]
        elif "_distance" in row:
            entry["score"] = distance_to_score(row["_distance"])

        formatted.append(entry)

    return formatted
