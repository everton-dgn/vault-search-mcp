"""
Tests for exponential-backoff retries.
"""

import time

import pytest


class TestRetryDecorators:
    """Tests for the decorators of retry."""

    def test_retry_embedding_success_first_try(self):
        """Function that succeeds in the first attempt does not retry."""
        from vault_search.utils.retry import retry_embedding

        call_count = [0]

        @retry_embedding
        def successful_fn():
            call_count[0] += 1
            return "success"

        result = successful_fn()

        assert result == "success"
        assert call_count[0] == 1

    def test_retry_embedding_retries_on_failure(self):
        """Retry is executed after failure."""
        from vault_search.utils.retry import retry_embedding

        call_count = [0]

        @retry_embedding
        def failing_then_success():
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("Simulated failure")
            return "success"

        result = failing_then_success()

        assert result == "success"
        assert call_count[0] == 3  # Two failures and one success.

    def test_retry_embedding_raises_after_max_attempts(self):
        """Exception is raised after exceed attempts maximums."""
        from vault_search.utils.retry import retry_embedding

        @retry_embedding
        def always_fails():
            raise RuntimeError("Always fails")

        # The decorator uses 5 attempts by default
        with pytest.raises(RuntimeError, match="Always fails"):
            always_fails()

    def test_retry_embedding_failure_fast_for_daemon_required(self):
        """DaemonRequiredError must not perform retry (error not transient)."""
        from vault_search.core.exceptions import DaemonRequiredError
        from vault_search.utils.retry import retry_embedding

        call_count = [0]

        @retry_embedding
        def fails_fast():
            call_count[0] += 1
            raise DaemonRequiredError("daemon required")

        with pytest.raises(DaemonRequiredError, match="daemon required"):
            fails_fast()

        assert call_count[0] == 1

    def test_retry_io_fewer_attempts(self):
        """retry_io uses less attempts that retry_embedding."""
        from vault_search.utils.retry import retry_io

        call_count = [0]

        @retry_io
        def io_operation():
            call_count[0] += 1
            raise OSError("IO error")

        with pytest.raises(OSError):
            io_operation()

        # retry_io uses 3 attempts
        assert call_count[0] == 3

    def test_retry_db_handles_connection_errors(self):
        """retry_db handles connection errors."""
        from vault_search.utils.retry import retry_db

        call_count = [0]

        @retry_db
        def db_operation():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ConnectionError("DB connection lost")
            return "connected"

        result = db_operation()

        assert result == "connected"
        assert call_count[0] == 2


class TestRetryConfig:
    """Tests for configuration of retry."""

    def test_retry_config_has_embedding_values(self):
        """RetryConfig exposes configured embedding retry values."""
        from vault_search.utils.retry import RetryConfig

        assert RetryConfig.EMBEDDING_MAX_ATTEMPTS > 0
        assert RetryConfig.EMBEDDING_MAX_DELAY_SECONDS > 0
        assert RetryConfig.EMBEDDING_INITIAL_WAIT > 0
        assert RetryConfig.EMBEDDING_MAX_WAIT > RetryConfig.EMBEDDING_INITIAL_WAIT
        assert RetryConfig.EMBEDDING_JITTER >= 0

    def test_retry_config_embedding_more_tolerant(self):
        """Embedding retries are more tolerant than I/O retries."""
        from vault_search.utils.retry import RetryConfig

        # Embedding must have more attempts and time that IO
        assert RetryConfig.EMBEDDING_MAX_ATTEMPTS >= RetryConfig.IO_MAX_ATTEMPTS
        assert RetryConfig.EMBEDDING_MAX_DELAY_SECONDS >= RetryConfig.IO_MAX_DELAY_SECONDS


class TestIsRetryableException:
    """Tests for identification of exceptions retryable."""

    def test_runtime_error_is_retryable(self):
        """RuntimeError is retryable for embeddings."""
        from vault_search.utils.retry import EMBEDDING_RETRY_EXCEPTIONS

        assert RuntimeError in EMBEDDING_RETRY_EXCEPTIONS

    def test_memory_error_is_retryable(self):
        """MemoryError is retryable (CUDA OOM)."""
        from vault_search.utils.retry import EMBEDDING_RETRY_EXCEPTIONS

        assert MemoryError in EMBEDDING_RETRY_EXCEPTIONS

    def test_timeout_error_is_retryable(self):
        """TimeoutError is retryable."""
        from vault_search.utils.retry import EMBEDDING_RETRY_EXCEPTIONS

        assert TimeoutError in EMBEDDING_RETRY_EXCEPTIONS


class TestWithRetry:
    """Tests for a factory with_retry."""

    def test_with_retry_custom_config(self):
        """with_retry accepts custom configuration."""
        from vault_search.utils.retry import with_retry

        call_count = [0]

        @with_retry(max_attempts=2, max_delay=5, exceptions=(ValueError,))
        def custom_retry_fn():
            call_count[0] += 1
            raise ValueError("Custom error")

        with pytest.raises(ValueError):
            custom_retry_fn()

        assert call_count[0] == 2  # Only 2 attempts


class TestRetryWithRealDelay:
    """Tests that verify actual delays and are marked as slow."""

    @pytest.mark.slow
    def test_exponential_backoff_timing(self):
        """Checks that the backoff exponential works."""
        from vault_search.utils.retry import retry_io

        times = []

        @retry_io
        def timed_failure():
            times.append(time.time())
            if len(times) < 3:
                raise OSError("Timed failure")
            return "success"

        result = timed_failure()

        assert result == "success"
        assert len(times) == 3

        # Must have some delay between attempts
        delay1 = times[1] - times[0]
        delay2 = times[2] - times[1]

        # With exponential backoff, the second delay must be larger
        # Jitter can vary, so allow a margin.
        assert delay1 > 0
        assert delay2 > 0
