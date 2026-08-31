"""
Funções de criação de índice FTS e compactação do LanceDB.
"""

import logging
from datetime import timedelta
from typing import TypedDict

from lancedb.table import Table

from vault_search.config.search import FTS_LANGUAGE

logger = logging.getLogger(__name__)

# LanceDB combina compactação e limpeza em optimize(). Uma retenção muito longa
# mantém versões anteriores disponíveis para rollback sem recorrer a APIs legadas.
_RECOVERY_RETENTION = timedelta(days=365_000)


class CompactionStats(TypedDict):
    """Resultado estável da compactação de uma tabela."""

    compacted: bool
    cleaned: bool
    error: str | None


def try_optimize(table: Table) -> None:
    """Tenta otimizar a tabela LanceDB, logando erros."""
    try:
        table.optimize(cleanup_older_than=_RECOVERY_RETENTION)
    except Exception as e:
        logger.warning(
            "Otimização de tabela falhou (error_type=%s)",
            type(e).__name__,
        )


def compact_table(table: Table) -> CompactionStats:
    """
    Compacta a tabela LanceDB para reduzir fragmentação.

    Mantém versões anteriores para permitir recuperação após falhas.

    Retorna:
        Dict com estatísticas da compactação.
    """
    stats: CompactionStats = {"compacted": False, "cleaned": False, "error": None}

    try:
        table.optimize(cleanup_older_than=_RECOVERY_RETENTION)
        stats["compacted"] = True
        logger.info("Compactação de arquivos concluída")
    except Exception as e:
        logger.warning(
            "Compactação falhou (error_type=%s)",
            type(e).__name__,
        )
        stats["error"] = type(e).__name__

    return stats


def create_fts_index(table: Table) -> None:
    """
    Cria índice FTS para busca híbrida.

    Usa FTS_LANGUAGE do config para stemming (ex: "Portuguese").
    Se FTS_LANGUAGE for None, usa tokenizador neutro sem stemming.

    Parâmetros:
        table: tabela LanceDB
    """
    try:
        if FTS_LANGUAGE:
            table.create_fts_index(
                "text",
                language=FTS_LANGUAGE,
                replace=True,
            )
            logger.info(f"Índice FTS criado (language={FTS_LANGUAGE}).")
        else:
            table.create_fts_index("text", replace=True)
            logger.info("Índice FTS criado (sem stemming).")
    except Exception as e:
        logger.warning(
            "Não foi possível criar índice FTS (error_type=%s)",
            type(e).__name__,
        )
