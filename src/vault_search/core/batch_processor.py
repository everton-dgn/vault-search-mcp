"""
Batch processing for chunks and embeddings.
"""

import logging

from lancedb.db import DBConnection
from lancedb.table import Table

from vault_search.config.paths import LANCEDB_TABLE
from vault_search.core.models import ModelManager
from vault_search.type_defs import ChunkRecord, ChunkWithVector
from vault_search.utils.retry import retry_db

logger = logging.getLogger(__name__)


@retry_db
def _store_batch_in_db(
    db: DBConnection,
    table: Table | None,
    batch: list[ChunkWithVector],
) -> Table:
    """
    Write a chunk batch to LanceDB with automatic retry.

    I/O operations may fail because of a busy disk or lock contention.
    The ``retry_db`` decorator makes up to three attempts with backoff.
    """
    if table is None:
        return db.create_table(LANCEDB_TABLE, data=batch)
    else:
        table.add(batch)
        return table


def embed_and_store_batch(
    db: DBConnection,
    table: Table | None,
    batch: list[ChunkRecord],
    models: ModelManager,
) -> Table:
    """
    Generate embeddings and write a chunk batch to LanceDB.

    Parameters:
        db: LanceDB connection.
        table: Existing table, or ``None`` to create one.
        batch: Chunks without vectors.
        models: ``ModelManager`` instance.

    Returns:
        The new or existing LanceDB table.
    """
    texts = [c["text"] for c in batch]
    logger.info("Generating embeddings for %d chunks", len(texts))
    vectors = models.embed_corpus(texts)

    records: list[ChunkWithVector] = [
        {**chunk, "vector": vector} for chunk, vector in zip(batch, vectors, strict=True)
    ]

    return _store_batch_in_db(db, table, records)
