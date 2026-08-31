"""
Funções auxiliares para o servidor MCP.

Inclui validação de parâmetros, logging privado e decorators.
"""

import functools
import logging
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from vault_search.config.search import SEARCH_TOP_K_MAX, SEARCH_TOP_K_MIN
from vault_search.config.security import MAX_QUERY_LENGTH
from vault_search.server.errors import public_error

logger = logging.getLogger("vault-search-mcp")

P = ParamSpec("P")
R = TypeVar("R")


def clamp_top_k(top_k: int) -> int:
    """Garante top_k dentro de limites razoáveis."""
    return max(SEARCH_TOP_K_MIN, min(top_k, SEARCH_TOP_K_MAX))


def truncate_query(query: str) -> str:
    """Trunca query para evitar processar strings enormes."""
    if len(query) > MAX_QUERY_LENGTH:
        logger.warning(f"Query truncada de {len(query)} para {MAX_QUERY_LENGTH} chars")
        return query[:MAX_QUERY_LENGTH]
    return query


def log_query(query: str) -> str:
    """Retorna somente metadados da query, sem registrar seu conteúdo."""
    return f"[redacted length={len(query)}]"


def execute_search(
    tool_name: str,
    query: str,
    top_k: int,
    search_fn: Callable[..., list[dict[str, object]]],
    **kwargs: object,
) -> list[dict[str, object]] | str:
    """
    Executa busca com validação, logging e error handling padronizados.

    Parâmetros:
        tool_name: nome da ferramenta MCP (para logs)
        query: texto de busca
        top_k: quantidade de resultados
        search_fn: método de busca a executar
        **kwargs: argumentos adicionais para search_fn

    Retorna:
        Lista de resultados ou mensagem de erro.
    """
    if not query or not query.strip():
        return "Erro: query não pode ser vazia."
    query = truncate_query(query.strip())
    top_k = clamp_top_k(top_k)
    logger.info("%s query_length=%d top_k=%d", tool_name, len(query), top_k)
    try:
        return search_fn(query, top_k=top_k, **kwargs)
    except RuntimeError as e:
        return public_error(
            logger,
            tool_name,
            e,
            code="search_unavailable",
            message="A busca está temporariamente indisponível.",
        )
    except Exception as e:
        return public_error(logger, tool_name, e)


# =============================================================================
# Decorators para MCP Tools
# =============================================================================


def with_error_handling(tool_name: str):
    """
    Decorator que adiciona error handling padronizado a uma função MCP tool.

    Converte exceções em mensagens de erro amigáveis.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R | str]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | str:
            try:
                return func(*args, **kwargs)
            except (ValueError, FileNotFoundError) as e:
                return public_error(
                    logger,
                    tool_name,
                    e,
                    code="invalid_request",
                    message="A entrada é inválida ou o recurso não existe.",
                )
            except Exception as e:
                return public_error(logger, tool_name, e)

        return wrapper

    return decorator


# O tratamento inline continua disponível para ferramentas com lógica específica.
