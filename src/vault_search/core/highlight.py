"""
Motor de highlight para resultados de busca.

Extrai termos significativos da query e os destaca no texto dos resultados.
"""

import logging
import re
from collections.abc import Sequence

from vault_search.type_defs import SearchRow

logger = logging.getLogger(__name__)

# Whitelist de marcadores permitidos para highlight (previne ReDoS)
ALLOWED_HIGHLIGHT_MARKERS = frozenset(
    {
        "**",
        "*",
        "__",
        "_",
        "==",
        "~~",
        "``",
        "`",
        "<mark>",
        "</mark>",
        "<em>",
        "</em>",
        "<strong>",
        "</strong>",
        "<b>",
        "</b>",
        "<i>",
        "</i>",
        "[[",
        "]]",
        "<<",
        ">>",
    }
)

# Stopwords comuns (não serão destacadas)
HIGHLIGHT_STOPWORDS = frozenset(
    {
        # English
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "are",
        "was",
        "were",
        "been",
        "have",
        "has",
        "had",
        "but",
        "not",
        "you",
        "all",
        "can",
        "her",
        "his",
        "they",
        "will",
        "would",
        "could",
        "should",
        # Portuguese
        "que",
        "para",
        "com",
        "uma",
        "por",
        "mais",
        "como",
        "mas",
        "foi",
        "ser",
        "tem",
        "seu",
        "sua",
        "ele",
        "ela",
        "isso",
        "este",
        "esta",
    }
)

# Tamanho mínimo de termo para highlight
MIN_TERM_LENGTH = 3


def extract_highlight_terms(query: str | None) -> list[str]:
    """
    Extrai termos significativos da query para highlight.

    Filtra stopwords e termos muito curtos.

    Parâmetros:
        query: query de busca

    Retorna:
        Lista de termos para destacar.
    """
    if not query:
        return []

    return [
        term
        for term in query.split()
        if len(term) >= MIN_TERM_LENGTH and term.lower() not in HIGHLIGHT_STOPWORDS
    ]


def validate_markers(
    start_marker: str,
    end_marker: str,
) -> tuple[str, str]:
    """
    Valida marcadores contra whitelist.

    Retorna valores padrão se inválidos.

    Parâmetros:
        start_marker: marcador de início
        end_marker: marcador de fim

    Retorna:
        Tupla (start, end) validados.
    """
    if start_marker not in ALLOWED_HIGHLIGHT_MARKERS:
        logger.warning("invalid_highlight_start_marker_ignored")
        start_marker = "**"
    if end_marker not in ALLOWED_HIGHLIGHT_MARKERS:
        logger.warning("invalid_highlight_end_marker_ignored")
        end_marker = "**"
    return start_marker, end_marker


def highlight_text(
    text: str,
    query: str,
    start_marker: str = "**",
    end_marker: str = "**",
) -> str:
    """
    Destaca termos da query no texto.

    Faz highlight case-insensitive dos termos encontrados.

    Parâmetros:
        text: texto para destacar
        query: query com termos para buscar
        start_marker: marcador de início (default: **)
        end_marker: marcador de fim (default: **)

    Retorna:
        Texto com termos destacados.
    """
    if not query or not text:
        return text

    # Validar marcadores
    start_marker, end_marker = validate_markers(start_marker, end_marker)

    # Extrair termos
    terms = extract_highlight_terms(query)
    if not terms:
        return text

    # Criar pattern para todos os termos (case-insensitive)
    pattern = "|".join(re.escape(term) for term in terms)

    def replace_match(match: re.Match[str]) -> str:
        return f"{start_marker}{match.group(0)}{end_marker}"

    return re.sub(f"({pattern})", replace_match, text, flags=re.IGNORECASE)


def apply_highlight(
    results: Sequence[SearchRow],
    query: str,
    highlight: bool,
    start_marker: str = "**",
    end_marker: str = "**",
) -> list[SearchRow]:
    """
    Aplica highlight a todos os resultados.

    Parâmetros:
        results: lista de resultados
        query: query para extrair termos
        highlight: se True, aplica highlight
        start_marker: marcador de início
        end_marker: marcador de fim

    Retorna:
        Lista com textos destacados (cópias, não muta input).
    """
    if not highlight:
        return list(results)

    highlighted: list[SearchRow] = []
    for result in results:
        entry: SearchRow = result.copy()
        entry["text"] = highlight_text(
            entry.get("text", ""),
            query,
            start_marker,
            end_marker,
        )
        highlighted.append(entry)

    return highlighted
