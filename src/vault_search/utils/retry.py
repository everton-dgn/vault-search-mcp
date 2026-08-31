"""
Retry utilities with exponential backoff for fallible operations.

Use tenacity to retry operations that may fail because of timeouts,
out-of-memory conditions, or other transient errors.

Usage:
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
# Exceptions that trigger retries
# =============================================================================

# Transient embedding and ML exceptions
EMBEDDING_RETRY_EXCEPTIONS = (
    RuntimeError,  # CUDA OOM, model loading errors
    MemoryError,  # Python OOM
    TimeoutError,  # Inference timeout
    OSError,  # File descriptor limits, etc
)

# Transient I/O exceptions
IO_RETRY_EXCEPTIONS = (
    IOError,
    OSError,
    TimeoutError,
    ConnectionError,
)

# Database exceptions
DB_RETRY_EXCEPTIONS = (
    IOError,
    OSError,
    TimeoutError,
)


# =============================================================================
# Retry settings
# =============================================================================


class RetryConfig:
    """Centralized retry settings."""

    # Embedding operations are expensive and receive a larger retry budget.
    EMBEDDING_MAX_ATTEMPTS = 5
    EMBEDDING_MAX_DELAY_SECONDS = 60
    EMBEDDING_INITIAL_WAIT = 1
    EMBEDDING_MAX_WAIT = 30
    EMBEDDING_JITTER = 2

    # I/O operations are faster and receive a smaller retry budget.
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
# Retry decorators
# =============================================================================


def _is_retryable_embedding_exception(exc: BaseException) -> bool:
    """
    Return ``True`` only for transient embedding failures.

    Configuration errors, such as an unavailable required daemon, fail immediately.
    """
    if isinstance(exc, DaemonRequiredError):
        return False
    return isinstance(exc, EMBEDDING_RETRY_EXCEPTIONS)


def retry_embedding[T](func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorate embedding and ML operations with retry behavior.

    Retry automatically with exponential backoff and jitter for:
    - CUDA out of memory
    - Model loading errors
    - Inference timeouts

    Configuration:
    - At most 5 attempts or 60 seconds
    - Exponential backoff of 1s, 2s, 4s, 8s, up to 30s
    - Up to 2s of jitter to avoid a thundering herd

    Example:
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
    Decorate I/O operations with retry behavior.

    Retry automatically for:
    - File read/write errors
    - Transient permission errors
    - Network filesystem timeouts

    Configuration:
    - At most 3 attempts or 10 seconds
    - Exponential backoff of 0.5s, 1s, 2s, up to 5s
    - Up to 1s of jitter

    Example:
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
    Decorate database operations with retry behavior.

    Retry automatically for:
    - LanceDB connection errors
    - SQLite busy/locked
    - Disk I/O errors

    Configuration:
    - At most 3 attempts or 15 seconds
    - Exponential backoff of 0.5s, 1s, 2s, up to 10s
    - Up to 1s of jitter

    Example:
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
    Create a decorator with custom retry behavior.

    Allow per-use-case retry configuration.

    Parameters:
        max_attempts: Maximum number of attempts.
        max_delay: Maximum seconds before giving up.
        initial_wait: Initial wait in seconds.
        max_wait: Maximum seconds between attempts.
        jitter: Random jitter in seconds.
        exceptions: Exception types that trigger a retry.

    Example:
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
    Check whether an exception is retryable.

    Useful when deciding whether to retry manually in contexts where
    the decorator cannot be applied.

    Parameters:
        exc: Exception to inspect.

    Returns:
        ``True`` when the exception is a retry candidate.
    """
    all_retryable = EMBEDDING_RETRY_EXCEPTIONS + IO_RETRY_EXCEPTIONS + DB_RETRY_EXCEPTIONS
    if isinstance(exc, DaemonRequiredError):
        return False
    return isinstance(exc, all_retryable)
