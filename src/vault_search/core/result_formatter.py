"""
Formatação de resultados de busca.
"""

from collections.abc import Sequence

from vault_search.type_defs import SearchResult, SearchRow
from vault_search.utils.math import distance_to_score


def format_search_results(rows: Sequence[SearchRow]) -> list[SearchResult]:
    """
    Formata resultados do LanceDB para retorno padronizado.

    Parâmetros:
        rows: resultados do reranking

    Retorna:
        Lista de dicts com campos padronizados.
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
