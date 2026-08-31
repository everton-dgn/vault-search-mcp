"""
Structured Logging com structlog.

Estado da arte:
- JSON em produção (parseable por ELK, Loki, Datadog)
- Console colorido em desenvolvimento
- Context variables para request_id e metadados
- orjson para serialização rápida
- Cache de loggers para performance

Referências:
- https://www.structlog.org/en/stable/logging-best-practices.html
- https://betterstack.com/community/guides/logging/structlog/
"""

import logging
import os
import sys
from collections.abc import Callable
from typing import Any, BinaryIO, TextIO, cast

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from vault_search.utils.privacy import redact_mapping, redact_text

# Detectar ambiente
IS_PRODUCTION = os.environ.get("VAULT_SEARCH_ENV", "development") == "production"
IS_TTY = sys.stderr.isatty()
LOG_LEVEL = os.environ.get("VAULT_SEARCH_LOG_LEVEL", "INFO").upper()


class _DynamicStderrText:
    """Encaminha escritas ao stderr atual, inclusive sob captura de testes."""

    def write(self, value: str) -> int:
        return sys.stderr.write(value)

    def flush(self) -> None:
        sys.stderr.flush()


class _DynamicStderrBytes:
    """Versão binária do proxy de stderr para o renderer JSON."""

    def write(self, value: bytes) -> int:
        return sys.stderr.buffer.write(value)

    def flush(self) -> None:
        sys.stderr.buffer.flush()


class _DynamicStderrHandler(logging.StreamHandler[TextIO]):
    """Evita manter referência a um stream de captura já fechado."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stderr
        super().emit(record)


class PrivacyFilter(logging.Filter):
    """Aplica a mesma política de privacidade aos logs da stdlib."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.msg)
        if isinstance(record.args, dict):
            record.args = redact_mapping(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact_mapping(item) for item in record.args)

        if record.exc_info:
            record.error_type = getattr(record.exc_info[0], "__name__", "Exception")
            record.exc_info = None
        record.stack_info = None
        return True


def add_app_context(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Adiciona contexto da aplicação a todos os logs."""
    event_dict["app"] = "vault-search-mcp"
    return event_dict


def add_safe_logger_name(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Inclui o nome somente quando a factory oferece esse atributo."""
    name = getattr(logger, "name", None)
    if name:
        event_dict["logger"] = redact_text(name)
    return event_dict


def censor_sensitive_fields(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Remove recursivamente conteúdo privado e paths dos logs."""
    exc_info = event_dict.pop("exc_info", None)
    if exc_info:
        if isinstance(exc_info, tuple) and exc_info:
            error_type = getattr(exc_info[0], "__name__", "Exception")
        elif isinstance(exc_info, BaseException):
            error_type = type(exc_info).__name__
        else:
            error_type = "Exception"
        event_dict["error_type"] = error_type

    return redact_mapping(event_dict)


def configure_logging(
    json_output: bool | None = None,
    level: str | None = None,
) -> None:
    """
    Configura structured logging para a aplicação.

    Parâmetros:
        json_output: forçar JSON (None = auto-detecta baseado em TTY)
        level: nível de log (DEBUG, INFO, WARNING, ERROR)
    """
    # Auto-detectar formato se não especificado
    if json_output is None:
        # JSON em produção ou quando não é TTY (ex: Docker, systemd)
        json_output = IS_PRODUCTION or not IS_TTY

    # Nível de log
    log_level = getattr(logging, level or LOG_LEVEL, logging.INFO)

    # Processors comuns
    shared_processors: list[Processor] = [
        # Adiciona contexto de variáveis (request_id, user_id, etc.)
        structlog.contextvars.merge_contextvars,
        # Adiciona contexto da app
        add_app_context,
        # Adiciona nome do logger
        add_safe_logger_name,
        # Adiciona nível do log
        structlog.stdlib.add_log_level,
        # Identifica o módulo sem revelar o path do checkout local.
        structlog.processors.CallsiteParameterAdder(
            [
                structlog.processors.CallsiteParameter.MODULE,
                structlog.processors.CallsiteParameter.LINENO,
            ]
        ),
        # A redação deve ocorrer depois de todos os campos contextuais.
        censor_sensitive_fields,
        # Timestamp ISO 8601
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    processors: list[Processor]
    factory: Callable[..., WrappedLogger]

    if json_output:
        # Produção: JSON otimizado com orjson
        processors = shared_processors + [
            # Serializa para JSON com orjson.
            structlog.processors.JSONRenderer(serializer=_orjson_dumps),
        ]
        # Factory de alta performance (escreve bytes direto no stderr).
        # Em transporte MCP stdio, stdout deve ficar reservado ao protocolo.
        factory = structlog.BytesLoggerFactory(file=cast(BinaryIO, _DynamicStderrBytes()))
    else:
        # Desenvolvimento: console colorido e legível
        processors = shared_processors + [
            # Cores e formatação bonita
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=structlog.dev.plain_traceback,
            ),
        ]
        # Manter logs no stderr para evitar poluir stdout.
        factory = structlog.PrintLoggerFactory(file=cast(TextIO, _DynamicStderrText()))

    # Configurar structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=factory,
        cache_logger_on_first_use=True,
    )

    # Configurar logging stdlib para capturar logs de bibliotecas
    stdlib_handler = _DynamicStderrHandler()
    stdlib_handler.addFilter(PrivacyFilter())
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=[stdlib_handler],
        force=True,
    )


def _orjson_dumps(obj: Any, **kwargs: Any) -> bytes:
    """Serializa para JSON usando orjson (mais rápido)."""
    import orjson

    return orjson.dumps(obj, default=str)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Obtém um logger estruturado.

    Parâmetros:
        name: nome do logger (opcional, usa módulo chamador se não fornecido)

    Retorna:
        Logger estruturado com contexto.

    Uso:
        logger = get_logger(__name__)
        logger.info("operação concluída", duration_ms=45.2, results=10)
    """
    return structlog.get_logger(name)


# Configurar automaticamente na importação (pode ser reconfigurado depois)
configure_logging()
