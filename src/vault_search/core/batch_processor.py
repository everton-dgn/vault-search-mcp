"""
Processamento em batch de chunks com embeddings.
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
    Grava batch de chunks no LanceDB com retry automático.

    Operações de I/O podem falhar por disk busy, locking, etc.
    O decorator retry_db faz até 3 tentativas com backoff.
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
    Gera embeddings e grava um batch de chunks no LanceDB.

    Parâmetros:
        db: conexão LanceDB
        table: tabela existente ou None para criar
        batch: lista de chunks sem vetor
        models: instância do ModelManager

    Retorna:
        Tabela LanceDB (nova ou existente).
    """
    texts = [c["text"] for c in batch]
    logger.info(f"Gerando embeddings para {len(texts)} chunks...")
    vectors = models.embed_corpus(texts)

    records: list[ChunkWithVector] = [
        {**chunk, "vector": vector} for chunk, vector in zip(batch, vectors, strict=True)
    ]

    return _store_batch_in_db(db, table, records)
