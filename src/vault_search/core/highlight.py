"""
Highlight engine for search results.

Extract meaningful query terms and highlight them in result text.
"""

import logging
import re
from collections.abc import Sequence

from vault_search.type_defs import SearchRow

logger = logging.getLogger(__name__)

# Allowlist of highlight markers that prevents unbounded regular expressions.
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

# Common stop words that are not highlighted.
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

# Minimum highlighted-term length.
MIN_TERM_LENGTH = 3


def extract_highlight_terms(query: str | None) -> list[str]:
    """
    Extract meaningful query terms for highlighting.

    Filter stop words and very short terms.

    Parameters:
        query: Search query.

    Returns:
        Terms to highlight.
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
    Validate markers against the allowlist.

    Return default markers when supplied values are invalid.

    Parameters:
        start_marker: Opening marker.
        end_marker: Closing marker.

    Returns:
        Validated ``(start, end)`` markers.
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
    Highlight query terms in text.

    Match terms case-insensitively.

    Parameters:
        text: Text to highlight.
        query: Query containing terms to find.
        start_marker: Opening marker; defaults to ``**``.
        end_marker: Closing marker; defaults to ``**``.

    Returns:
        Text with highlighted terms.
    """
    if not query or not text:
        return text

    # Validate markers.
    start_marker, end_marker = validate_markers(start_marker, end_marker)

    # Extract terms.
    terms = extract_highlight_terms(query)
    if not terms:
        return text

    # Build one case-insensitive pattern for every term.
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
    Apply highlighting to every result.

    Parameters:
        results: Search results.
        query: Query used to extract terms.
        highlight: Whether to apply highlighting.
        start_marker: Opening marker.
        end_marker: Closing marker.

    Returns:
        Copied results with highlighted text; the input is not mutated.
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
