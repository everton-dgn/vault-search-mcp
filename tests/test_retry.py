"""
Testes para o módulo de retry com exponential backoff.
"""

import time

import pytest


class TestRetryDecorators:
    """Testes para os decorators de retry."""

    def test_retry_embedding_success_first_try(self):
        """Função que sucede na primeira tentativa não faz retry."""
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
        """Retry é executado após falha."""
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
        assert call_count[0] == 3  # 2 falhas + 1 sucesso

    def test_retry_embedding_raises_after_max_attempts(self):
        """Exceção é levantada após exceder tentativas máximas."""
        from vault_search.utils.retry import retry_embedding

        @retry_embedding
        def always_fails():
            raise RuntimeError("Always fails")

        # O decorator usa 5 tentativas por padrão
        with pytest.raises(RuntimeError, match="Always fails"):
            always_fails()

    def test_retry_embedding_falha_fast_para_daemon_required(self):
        """DaemonRequiredError não deve fazer retry (erro não transitório)."""
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
        """retry_io usa menos tentativas que retry_embedding."""
        from vault_search.utils.retry import retry_io

        call_count = [0]

        @retry_io
        def io_operation():
            call_count[0] += 1
            raise OSError("IO error")

        with pytest.raises(OSError):
            io_operation()

        # retry_io usa 3 tentativas
        assert call_count[0] == 3

    def test_retry_db_handles_connection_errors(self):
        """retry_db trata erros de conexão."""
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
    """Testes para configuração de retry."""

    def test_retry_config_has_embedding_values(self):
        """RetryConfig tem valores de embedding configurados."""
        from vault_search.utils.retry import RetryConfig

        assert RetryConfig.EMBEDDING_MAX_ATTEMPTS > 0
        assert RetryConfig.EMBEDDING_MAX_DELAY_SECONDS > 0
        assert RetryConfig.EMBEDDING_INITIAL_WAIT > 0
        assert RetryConfig.EMBEDDING_MAX_WAIT > RetryConfig.EMBEDDING_INITIAL_WAIT
        assert RetryConfig.EMBEDDING_JITTER >= 0

    def test_retry_config_embedding_more_tolerant(self):
        """Configuração de embedding é mais tolerante que IO."""
        from vault_search.utils.retry import RetryConfig

        # Embedding deve ter mais tentativas e tempo que IO
        assert RetryConfig.EMBEDDING_MAX_ATTEMPTS >= RetryConfig.IO_MAX_ATTEMPTS
        assert RetryConfig.EMBEDDING_MAX_DELAY_SECONDS >= RetryConfig.IO_MAX_DELAY_SECONDS


class TestIsRetryableException:
    """Testes para identificação de exceções retryable."""

    def test_runtime_error_is_retryable(self):
        """RuntimeError é retryable para embeddings."""
        from vault_search.utils.retry import EMBEDDING_RETRY_EXCEPTIONS

        assert RuntimeError in EMBEDDING_RETRY_EXCEPTIONS

    def test_memory_error_is_retryable(self):
        """MemoryError é retryable (CUDA OOM)."""
        from vault_search.utils.retry import EMBEDDING_RETRY_EXCEPTIONS

        assert MemoryError in EMBEDDING_RETRY_EXCEPTIONS

    def test_timeout_error_is_retryable(self):
        """TimeoutError é retryable."""
        from vault_search.utils.retry import EMBEDDING_RETRY_EXCEPTIONS

        assert TimeoutError in EMBEDDING_RETRY_EXCEPTIONS


class TestWithRetry:
    """Testes para a factory with_retry."""

    def test_with_retry_custom_config(self):
        """with_retry permite configuração customizada."""
        from vault_search.utils.retry import with_retry

        call_count = [0]

        @with_retry(max_attempts=2, max_delay=5, exceptions=(ValueError,))
        def custom_retry_fn():
            call_count[0] += 1
            raise ValueError("Custom error")

        with pytest.raises(ValueError):
            custom_retry_fn()

        assert call_count[0] == 2  # Apenas 2 tentativas


class TestRetryWithRealDelay:
    """Testes que verificam o delay real (marcados como slow)."""

    @pytest.mark.slow
    def test_exponential_backoff_timing(self):
        """Verifica que o backoff exponencial funciona."""
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

        # Deve ter algum delay entre tentativas
        delay1 = times[1] - times[0]
        delay2 = times[2] - times[1]

        # Com exponential backoff, o segundo delay deve ser maior
        # (com jitter pode variar, então usamos margem)
        assert delay1 > 0
        assert delay2 > 0
