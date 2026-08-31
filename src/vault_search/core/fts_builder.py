"""
Functions for creating FTS indexes and compacting LanceDB tables.
"""

import logging
from datetime import timedelta
from typing import TypedDict

from lancedb.table import Table

from vault_search.config.search import FTS_LANGUAGE

logger = logging.getLogger(__name__)

# LanceDB combines compaction and cleanup in optimize(). A long retention period
# keeps earlier versions available for recovery without relying on legacy APIs.
_RECOVERY_RETENTION = timedelta(days=365_000)


class CompactionStats(TypedDict):
    """Stable result shape for table compaction."""

    compacted: bool
    cleaned: bool
    error: str | None


def try_optimize(table: Table) -> None:
    """Attempt to optimize a LanceDB table while logging errors."""
    try:
        table.optimize(cleanup_older_than=_RECOVERY_RETENTION)
    except Exception as e:
        logger.warning(
            "Table optimization failed (error_type=%s)",
            type(e).__name__,
        )


def compact_table(table: Table) -> CompactionStats:
    """
    Compact a LanceDB table to reduce fragmentation.

    Retain earlier versions for recovery after failures.

    Returns:
        Compaction statistics.
    """
    stats: CompactionStats = {"compacted": False, "cleaned": False, "error": None}

    try:
        table.optimize(cleanup_older_than=_RECOVERY_RETENTION)
        stats["compacted"] = True
        logger.info("File compaction completed")
    except Exception as e:
        logger.warning(
            "Compaction failed (error_type=%s)",
            type(e).__name__,
        )
        stats["error"] = type(e).__name__

    return stats


def create_fts_index(table: Table) -> None:
    """
    Create an FTS index for hybrid search.

    Use ``FTS_LANGUAGE`` for stemming, for example "English".
    When ``FTS_LANGUAGE`` is ``None``, disable language-specific stemming and
    stop-word removal while retaining case and accent normalization.

    Parameters:
        table: LanceDB table.
    """
    try:
        if FTS_LANGUAGE:
            table.create_fts_index(
                "text",
                language=FTS_LANGUAGE,
                replace=True,
            )
            logger.info("FTS index created (language=%s)", FTS_LANGUAGE)
        else:
            table.create_fts_index(
                "text",
                language="English",
                stem=False,
                remove_stop_words=False,
                ascii_folding=True,
                replace=True,
            )
            logger.info("FTS index created with language-neutral analysis")
    except Exception as e:
        logger.warning(
            "Could not create FTS index (error_type=%s)",
            type(e).__name__,
        )
