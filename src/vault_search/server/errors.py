"""Erros públicos estáveis sem detalhes internos do ambiente."""

from __future__ import annotations

import logging
import secrets
from typing import Any


def public_error(
    logger: logging.Logger,
    operation: str,
    error: BaseException,
    *,
    code: str = "internal_error",
    message: str = "A operação não pôde ser concluída.",
) -> str:
    """Registra metadados mínimos e devolve uma mensagem segura ao cliente."""
    error_id = secrets.token_hex(4)
    logger.error(
        "%s failed error_id=%s error_type=%s",
        operation,
        error_id,
        type(error).__name__,
    )
    return f"Erro [{code}]: {message} Referência: {error_id}."


def public_error_dict(
    logger: logging.Logger,
    operation: str,
    error: BaseException,
    *,
    code: str = "internal_error",
    message: str = "A operação não pôde ser concluída.",
) -> dict[str, Any]:
    """Versão estruturada de :func:`public_error` para resources MCP."""
    return {
        "error": public_error(
            logger,
            operation,
            error,
            code=code,
            message=message,
        ),
        "code": code,
    }
