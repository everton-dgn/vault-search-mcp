"""
Utilitários de retry com exponential backoff para operações falíveis.

Usa tenacity para retry automático de operações que podem falhar
por timeout, OOM, ou outros erros transientes.

Uso:
    from vault_search.utils.retry import retry_embedding, retry_io

    @retry_embedding
    def generate_embeddings(texts):
        return model.encode(texts)

    @retry_io
    def read_file(path):
        return path.read_text()
"""

import logging
from collections.abc import Callable

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential_jitter,
)

from vault_search.core.exceptions import DaemonRequiredError

logger = logging.getLogger(__name__)

# =============================================================================
# Exceções que disparam retry
# =============================================================================

# Exceções de embedding/ML que são transientes
EMBEDDING_RETRY_EXCEPTIONS = (
    RuntimeError,  # CUDA OOM, model loading errors
    MemoryError,  # Python OOM
    TimeoutError,  # Inference timeout
    OSError,  # File descriptor limits, etc
)

# Exceções de I/O que são transientes
IO_RETRY_EXCEPTIONS = (
    IOError,
    OSError,
    TimeoutError,
    ConnectionError,
)

# Exceções de banco de dados
DB_RETRY_EXCEPTIONS = (
    IOError,
    OSError,
    TimeoutError,
)


# =============================================================================
# Configurações de retry
# =============================================================================


class RetryConfig:
    """Configurações centralizadas de retry."""

    # Embedding operations (mais tolerante, operações caras)
    EMBEDDING_MAX_ATTEMPTS = 5
    EMBEDDING_MAX_DELAY_SECONDS = 60
    EMBEDDING_INITIAL_WAIT = 1
    EMBEDDING_MAX_WAIT = 30
    EMBEDDING_JITTER = 2

    # I/O operations (menos tolerante, operações rápidas)
    IO_MAX_ATTEMPTS = 3
    IO_MAX_DELAY_SECONDS = 10
    IO_INITIAL_WAIT = 0.5
    IO_MAX_WAIT = 5
    IO_JITTER = 1

    # Database operations
    DB_MAX_ATTEMPTS = 3
    DB_MAX_DELAY_SECONDS = 15
    DB_INITIAL_WAIT = 0.5
    DB_MAX_WAIT = 10
    DB_JITTER = 1


# =============================================================================
# Decoradores de retry
# =============================================================================


def _is_retryable_embedding_exception(exc: BaseException) -> bool:
    """
    Retorna True apenas para falhas transitórias de embedding.

    Erros de configuração (ex: daemon obrigatório indisponível) falham fast.
    """
    if isinstance(exc, DaemonRequiredError):
        return False
    return isinstance(exc, EMBEDDING_RETRY_EXCEPTIONS)


def retry_embedding[T](func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator para retry de operações de embedding/ML.

    Retry automático com exponential backoff + jitter para:
    - CUDA out of memory
    - Model loading errors
    - Inference timeouts

    Configuração:
    - Máximo 5 tentativas OU 60 segundos
    - Backoff exponencial: 1s, 2s, 4s, 8s... (max 30s)
    - Jitter de até 2s para evitar thundering herd

    Exemplo:
        @retry_embedding
        def encode_batch(texts):
            return model.encode(texts)
    """
    return retry(
        retry=retry_if_exception(_is_retryable_embedding_exception),
        stop=(
            stop_after_attempt(RetryConfig.EMBEDDING_MAX_ATTEMPTS)
            | stop_after_delay(RetryConfig.EMBEDDING_MAX_DELAY_SECONDS)
        ),
        wait=wait_exponential_jitter(
            initial=RetryConfig.EMBEDDING_INITIAL_WAIT,
            max=RetryConfig.EMBEDDING_MAX_WAIT,
            jitter=RetryConfig.EMBEDDING_JITTER,
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )(func)


def retry_io[T](func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator para retry de operações de I/O.

    Retry automático para:
    - File read/write errors
    - Permission errors transientes
    - Network filesystem timeouts

    Configuração:
    - Máximo 3 tentativas OU 10 segundos
    - Backoff exponencial: 0.5s, 1s, 2s... (max 5s)
    - Jitter de até 1s

    Exemplo:
        @retry_io
        def read_note(path):
            return path.read_text()
    """
    return retry(
        retry=retry_if_exception_type(IO_RETRY_EXCEPTIONS),
        stop=(
            stop_after_attempt(RetryConfig.IO_MAX_ATTEMPTS)
            | stop_after_delay(RetryConfig.IO_MAX_DELAY_SECONDS)
        ),
        wait=wait_exponential_jitter(
            initial=RetryConfig.IO_INITIAL_WAIT,
            max=RetryConfig.IO_MAX_WAIT,
            jitter=RetryConfig.IO_JITTER,
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )(func)


def retry_db[T](func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator para retry de operações de banco de dados.

    Retry automático para:
    - LanceDB connection errors
    - SQLite busy/locked
    - Disk I/O errors

    Configuração:
    - Máximo 3 tentativas OU 15 segundos
    - Backoff exponencial: 0.5s, 1s, 2s... (max 10s)
    - Jitter de até 1s

    Exemplo:
        @retry_db
        def query_index(vector):
            return table.search(vector).to_list()
    """
    return retry(
        retry=retry_if_exception_type(DB_RETRY_EXCEPTIONS),
        stop=(
            stop_after_attempt(RetryConfig.DB_MAX_ATTEMPTS)
            | stop_after_delay(RetryConfig.DB_MAX_DELAY_SECONDS)
        ),
        wait=wait_exponential_jitter(
            initial=RetryConfig.DB_INITIAL_WAIT,
            max=RetryConfig.DB_MAX_WAIT,
            jitter=RetryConfig.DB_JITTER,
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )(func)


def with_retry[T](
    max_attempts: int = 3,
    max_delay: int = 30,
    initial_wait: float = 1.0,
    max_wait: float = 10.0,
    jitter: float = 1.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator factory para retry customizado.

    Permite configuração granular de retry para casos específicos.

    Parâmetros:
        max_attempts: máximo de tentativas
        max_delay: máximo de segundos antes de desistir
        initial_wait: segundos de espera inicial
        max_wait: máximo de segundos entre tentativas
        jitter: segundos de jitter aleatório
        exceptions: tupla de exceções que disparam retry

    Exemplo:
        @with_retry(max_attempts=5, exceptions=(TimeoutError,))
        def slow_operation():
            ...
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        return retry(
            retry=retry_if_exception_type(exceptions),
            stop=(stop_after_attempt(max_attempts) | stop_after_delay(max_delay)),
            wait=wait_exponential_jitter(
                initial=initial_wait,
                max=max_wait,
                jitter=jitter,
            ),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )(func)

    return decorator


# =============================================================================
# Helpers
# =============================================================================


def is_retryable_exception(exc: Exception) -> bool:
    """
    Verifica se uma exceção é retryable.

    Útil para decidir se deve fazer retry manual em contextos
    onde o decorator não é aplicável.

    Parâmetros:
        exc: exceção a verificar

    Retorna:
        True se a exceção é candidata a retry.
    """
    all_retryable = EMBEDDING_RETRY_EXCEPTIONS + IO_RETRY_EXCEPTIONS + DB_RETRY_EXCEPTIONS
    if isinstance(exc, DaemonRequiredError):
        return False
    return isinstance(exc, all_retryable)
