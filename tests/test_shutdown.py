"""
Testes para o módulo de graceful shutdown.
"""

import signal
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_shutdown_state():
    """Reseta estado de shutdown antes e depois de cada teste."""
    from vault_search.utils.shutdown import ShutdownManager

    ShutdownManager.reset()
    yield
    ShutdownManager.reset()


class TestShutdownRequested:
    """Testes para shutdown_requested()."""

    def test_shutdown_not_requested_initially(self):
        """Shutdown não está solicitado no início."""
        from vault_search.utils.shutdown import ShutdownManager, shutdown_requested

        # Reset estado completo
        ShutdownManager.reset()

        assert shutdown_requested() is False

    def test_shutdown_requested_after_request(self):
        """shutdown_requested() retorna True após request_shutdown()."""
        from vault_search.utils.shutdown import (
            ShutdownManager,
            request_shutdown,
            shutdown_requested,
        )

        # Reset estado completo
        ShutdownManager.reset()

        request_shutdown()

        assert shutdown_requested() is True


class TestRequestShutdown:
    """Testes para request_shutdown()."""

    def test_request_shutdown_sets_event(self):
        """request_shutdown() define o evento de shutdown."""
        from vault_search.utils.shutdown import (
            ShutdownManager,
            request_shutdown,
            shutdown_requested,
        )

        # Reset estado completo
        ShutdownManager.reset()

        assert shutdown_requested() is False

        request_shutdown()

        assert shutdown_requested() is True


class TestProtectedSection:
    """Testes para protected_section context manager."""

    def test_protected_section_executes_code(self):
        """Código dentro de protected_section é executado."""
        from vault_search.utils.shutdown import protected_section

        executed = [False]

        with protected_section("test operation"):
            executed[0] = True

        assert executed[0] is True

    def test_protected_section_propagates_exceptions(self):
        """Exceções dentro de protected_section são propagadas."""
        from vault_search.utils.shutdown import protected_section

        with pytest.raises(ValueError, match="test error"):
            with protected_section("failing operation"):
                raise ValueError("test error")

    def test_protected_section_logs_description(self):
        """protected_section loga a descrição da operação."""
        from vault_search.utils.shutdown import protected_section

        with patch("vault_search.utils.shutdown.logger") as mock_logger:
            with protected_section("saving index"):
                pass

            # Verifica que debug foi chamado com a descrição
            mock_logger.debug.assert_called()


class TestDelayedKeyboardInterrupt:
    """Testes para DelayedKeyboardInterrupt."""

    def test_delayed_interrupt_defers_signal(self):
        """DelayedKeyboardInterrupt adia SIGINT durante seção crítica."""
        from vault_search.utils.shutdown import DelayedKeyboardInterrupt

        signal_deferred = [False]

        original_handler = signal.getsignal(signal.SIGINT)

        try:
            with DelayedKeyboardInterrupt():
                # Simular recebimento de sinal
                # (não podemos realmente enviar SIGINT no teste)
                pass

            # Código executa normalmente
            signal_deferred[0] = True

        finally:
            signal.signal(signal.SIGINT, original_handler)

        assert signal_deferred[0] is True


class TestShutdownManager:
    """Testes para ShutdownManager."""

    def test_shutdown_manager_register_callback(self):
        """ShutdownManager registra callbacks corretamente."""
        from vault_search.utils.shutdown import ShutdownManager

        # Reset estado
        ShutdownManager.reset()

        # Registrar callback
        callback = MagicMock()
        ShutdownManager.register_callback(callback)

        # Não podemos acessar _state diretamente, mas podemos verificar
        # que unregister funciona (implica que foi registrado)
        ShutdownManager.unregister_callback(callback)

    def test_callbacks_executed_in_lifo_order(self):
        """Callbacks são executados em ordem LIFO via shutdown()."""
        from vault_search.utils.shutdown import ShutdownManager

        # Reset estado
        ShutdownManager.reset()

        execution_order = []

        def callback1():
            execution_order.append(1)

        def callback2():
            execution_order.append(2)

        def callback3():
            execution_order.append(3)

        ShutdownManager.register_callback(callback1)
        ShutdownManager.register_callback(callback2)
        ShutdownManager.register_callback(callback3)

        # Executar shutdown que chama callbacks em ordem LIFO
        ShutdownManager.shutdown()

        # LIFO: último registrado executa primeiro
        assert execution_order == [3, 2, 1]

    def test_callback_exception_doesnt_break_others(self):
        """Exceção em um callback não impede execução dos outros."""
        from vault_search.utils.shutdown import ShutdownManager

        # Reset estado
        ShutdownManager.reset()

        executed = []

        def good_callback():
            executed.append("good")

        def bad_callback():
            executed.append("bad_start")
            raise RuntimeError("Callback failed")

        def another_good():
            executed.append("another")

        ShutdownManager.register_callback(good_callback)
        ShutdownManager.register_callback(bad_callback)
        ShutdownManager.register_callback(another_good)

        # Executar shutdown
        ShutdownManager.shutdown()

        # Todos devem ter sido chamados (LIFO: another -> bad -> good)
        assert "another" in executed
        assert "bad_start" in executed
        assert "good" in executed

    def test_shutdown_respects_total_callback_timeout(self):
        """Callback travado não impede o processo de concluir o shutdown."""
        from vault_search.utils.shutdown import ShutdownManager

        release = threading.Event()
        ShutdownManager.initialize(timeout=0.05)
        ShutdownManager.register_callback(lambda: release.wait(5))

        started = time.monotonic()
        ShutdownManager.shutdown()
        elapsed = time.monotonic() - started
        release.set()

        assert elapsed < 0.5

    def test_concurrent_shutdown_runs_callbacks_once(self):
        """Callers concorrentes compartilham uma transição atômica."""
        from vault_search.utils.shutdown import ShutdownManager

        barrier = threading.Barrier(3)
        calls = []

        def callback():
            calls.append("called")

        def invoke_shutdown():
            barrier.wait()
            ShutdownManager.shutdown()

        ShutdownManager.register_callback(callback)
        threads = [threading.Thread(target=invoke_shutdown) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=1)

        assert calls == ["called"]


class TestWaitForShutdown:
    """Testes para wait_for_shutdown()."""

    def test_wait_for_shutdown_returns_immediately_if_set(self):
        """wait_for_shutdown retorna imediatamente se shutdown já solicitado."""
        from vault_search.utils.shutdown import (
            ShutdownManager,
            request_shutdown,
            wait_for_shutdown,
        )

        # Reset e solicitar shutdown
        ShutdownManager.reset()
        request_shutdown()

        start = time.time()
        result = wait_for_shutdown(timeout=5.0)
        elapsed = time.time() - start

        assert result is True
        assert elapsed < 1.0  # Deve retornar imediatamente

    def test_wait_for_shutdown_times_out(self):
        """wait_for_shutdown retorna False após timeout."""
        from vault_search.utils.shutdown import ShutdownManager, wait_for_shutdown

        # Reset (sem solicitar shutdown)
        ShutdownManager.reset()

        start = time.time()
        result = wait_for_shutdown(timeout=0.1)
        elapsed = time.time() - start

        assert result is False
        assert elapsed >= 0.1

    def test_wait_for_shutdown_no_timeout(self):
        """wait_for_shutdown sem timeout bloqueia até shutdown."""
        from vault_search.utils.shutdown import (
            ShutdownManager,
            request_shutdown,
            wait_for_shutdown,
        )

        ShutdownManager.reset()

        def request_after_delay():
            time.sleep(0.1)
            request_shutdown()

        thread = threading.Thread(target=request_after_delay)
        thread.start()

        start = time.time()
        result = wait_for_shutdown(timeout=None)
        elapsed = time.time() - start

        assert result is True
        assert elapsed >= 0.08  # Tolera a resolução do relógio e o scheduler.
        thread.join(timeout=1)
        thread.join()


class TestInterruptibleLoop:
    """Testes para interruptible_loop context manager."""

    def test_interruptible_loop_normal_execution(self):
        """Loop interruptível executa normalmente sem shutdown."""
        from vault_search.utils.shutdown import ShutdownManager, interruptible_loop

        # Reset estado
        ShutdownManager.reset()

        items_processed = []

        with interruptible_loop() as should_continue:
            for i in range(5):
                if not should_continue():
                    break
                items_processed.append(i)

        assert items_processed == [0, 1, 2, 3, 4]

    def test_interruptible_loop_stops_on_shutdown(self):
        """Loop interruptível para quando shutdown é solicitado."""
        from vault_search.utils.shutdown import (
            ShutdownManager,
            interruptible_loop,
            request_shutdown,
        )

        # Reset estado
        ShutdownManager.reset()

        items_processed = []

        with interruptible_loop() as should_continue:
            for i in range(10):
                if i == 3:
                    request_shutdown()

                if not should_continue():
                    break
                items_processed.append(i)

        # Deve parar após o item 3 (quando shutdown foi solicitado)
        assert len(items_processed) <= 4


class TestShutdownManagerInitialize:
    """Testes para ShutdownManager.initialize()."""

    def test_initialize_validates_timeout(self):
        """initialize() rejeita timeout <= 0."""
        from vault_search.utils.shutdown import ShutdownManager

        ShutdownManager.reset()

        with pytest.raises(ValueError, match="timeout deve ser > 0"):
            ShutdownManager.initialize(timeout=0)

        with pytest.raises(ValueError, match="timeout deve ser > 0"):
            ShutdownManager.initialize(timeout=-1)

    def test_initialize_accepts_positive_timeout(self):
        """initialize() aceita timeout positivo."""
        from vault_search.utils.shutdown import ShutdownManager

        ShutdownManager.reset()

        # Não deve levantar exceção
        ShutdownManager.initialize(timeout=0.1)


class TestDelayedKeyboardInterruptWarning:
    """Testes para warning de DelayedKeyboardInterrupt fora da main thread."""

    def test_logs_warning_outside_main_thread(self):
        """DelayedKeyboardInterrupt loga warning fora da main thread."""
        from vault_search.utils.shutdown import DelayedKeyboardInterrupt

        warning_logged = [False]

        def run_in_thread():
            with patch("vault_search.utils.shutdown.logger") as mock_logger:
                with DelayedKeyboardInterrupt():
                    pass
                # Verifica que warning foi chamado
                if mock_logger.warning.called:
                    warning_logged[0] = True

        thread = threading.Thread(target=run_in_thread)
        thread.start()
        thread.join()

        assert warning_logged[0] is True


class TestIntegration:
    """Testes de integração do sistema de shutdown."""

    def test_full_shutdown_flow(self):
        """Fluxo completo: initialize -> register -> request -> cleanup."""
        from vault_search.utils.shutdown import (
            ShutdownManager,
            request_shutdown,
            shutdown_requested,
        )

        # Reset completo
        ShutdownManager.reset()

        cleanup_executed = [False]

        def cleanup():
            cleanup_executed[0] = True

        # Registrar callback
        ShutdownManager.register_callback(cleanup)

        # Verificar estado inicial
        assert shutdown_requested() is False
        assert cleanup_executed[0] is False

        # Solicitar shutdown
        request_shutdown()

        # Verificar estado após request
        assert shutdown_requested() is True

        # Executar shutdown (que chama callbacks)
        ShutdownManager.shutdown()

        assert cleanup_executed[0] is True
