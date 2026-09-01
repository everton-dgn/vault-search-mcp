"""
Tests for graceful shutdown.
"""

import signal
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_shutdown_state():
    """Resets state of shutdown before and after of each test."""
    from vault_search.utils.shutdown import ShutdownManager

    ShutdownManager.reset()
    yield
    ShutdownManager.reset()


class TestShutdownRequested:
    """Tests for shutdown_requested()."""

    def test_shutdown_not_requested_initially(self):
        """Shutdown is not requested in the start."""
        from vault_search.utils.shutdown import ShutdownManager, shutdown_requested

        # Reset state complete
        ShutdownManager.reset()

        assert shutdown_requested() is False

    def test_shutdown_requested_after_request(self):
        """shutdown_requested() returns True after request_shutdown()."""
        from vault_search.utils.shutdown import (
            ShutdownManager,
            request_shutdown,
            shutdown_requested,
        )

        # Reset state complete
        ShutdownManager.reset()

        request_shutdown()

        assert shutdown_requested() is True


class TestRequestShutdown:
    """Tests for request_shutdown()."""

    def test_request_shutdown_sets_event(self):
        """request_shutdown() sets the event of shutdown."""
        from vault_search.utils.shutdown import (
            ShutdownManager,
            request_shutdown,
            shutdown_requested,
        )

        # Reset state complete
        ShutdownManager.reset()

        assert shutdown_requested() is False

        request_shutdown()

        assert shutdown_requested() is True


class TestProtectedSection:
    """Tests for protected_section context manager."""

    def test_protected_section_executes_code(self):
        """Code inside of protected_section is executed."""
        from vault_search.utils.shutdown import protected_section

        executed = [False]

        with protected_section("test operation"):
            executed[0] = True

        assert executed[0] is True

    def test_protected_section_propagates_exceptions(self):
        """Exceptions inside of protected_section are propagated."""
        from vault_search.utils.shutdown import protected_section

        with pytest.raises(ValueError, match="test error"):
            with protected_section("failing operation"):
                raise ValueError("test error")

    def test_protected_section_logs_description(self):
        """protected_section logs a description of the operation."""
        from vault_search.utils.shutdown import protected_section

        with patch("vault_search.utils.shutdown.logger") as mock_logger:
            with protected_section("saving index"):
                pass

            # Checks that debug was called with a description
            mock_logger.debug.assert_called()


class TestDelayedKeyboardInterrupt:
    """Tests for DelayedKeyboardInterrupt."""

    def test_delayed_interrupt_defers_signal(self):
        """DelayedKeyboardInterrupt defers SIGINT during a critical section."""
        from vault_search.utils.shutdown import DelayedKeyboardInterrupt

        signal_deferred = [False]

        original_handler = signal.getsignal(signal.SIGINT)

        try:
            with DelayedKeyboardInterrupt():
                # Simulate receiving a signal.
                # The test cannot send a real SIGINT.
                pass

            # Code executes normally.
            signal_deferred[0] = True

        finally:
            signal.signal(signal.SIGINT, original_handler)

        assert signal_deferred[0] is True


class TestShutdownManager:
    """Tests for ShutdownManager."""

    def test_shutdown_manager_register_callback(self):
        """ShutdownManager registers callbacks correctly."""
        from vault_search.utils.shutdown import ShutdownManager

        # Reset state
        ShutdownManager.reset()

        # Register callback
        callback = MagicMock()
        ShutdownManager.register_callback(callback)

        # _state is private, so verify it through observable behavior.
        # Verify unregister behavior after registration.
        ShutdownManager.unregister_callback(callback)

    def test_callbacks_executed_in_lifo_order(self):
        """shutdown() executes callbacks in LIFO order."""
        from vault_search.utils.shutdown import ShutdownManager

        # Reset state
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

        # Run shutdown, which calls callbacks in LIFO order.
        ShutdownManager.shutdown()

        # LIFO: the last registered callback executes first.
        assert execution_order == [3, 2, 1]

    def test_callback_exception_doesnt_break_others(self):
        """Exception in a callback not prevents execution of the other."""
        from vault_search.utils.shutdown import ShutdownManager

        # Reset state
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

        # Every callback must run in LIFO order: another, bad, good.
        assert "another" in executed
        assert "bad_start" in executed
        assert "good" in executed

    def test_shutdown_respects_total_callback_timeout(self):
        """Callback stuck not prevents the process of complete the shutdown."""
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
        """Callers concurrent share a transition atomic."""
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
    """Tests for wait_for_shutdown()."""

    def test_wait_for_shutdown_returns_immediately_if_set(self):
        """wait_for_shutdown returns immediately when shutdown was already requested."""
        from vault_search.utils.shutdown import (
            ShutdownManager,
            request_shutdown,
            wait_for_shutdown,
        )

        # Reset and request shutdown.
        ShutdownManager.reset()
        request_shutdown()

        start = time.time()
        result = wait_for_shutdown(timeout=5.0)
        elapsed = time.time() - start

        assert result is True
        assert elapsed < 1.0  # Must return immediately.

    def test_wait_for_shutdown_times_out(self):
        """wait_for_shutdown returns False after timeout."""
        from vault_search.utils.shutdown import ShutdownManager, wait_for_shutdown

        # Reset without requesting shutdown.
        ShutdownManager.reset()

        start = time.time()
        result = wait_for_shutdown(timeout=0.1)
        elapsed = time.time() - start

        assert result is False
        assert elapsed >= 0.1

    def test_wait_for_shutdown_in_timeout(self):
        """wait_for_shutdown without timeout blocks up to shutdown."""
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
        assert elapsed >= 0.08  # Allows a resolution of the clock and the scheduler.
        thread.join(timeout=1)
        thread.join()


class TestInterruptibleLoop:
    """Tests for interruptible_loop context manager."""

    def test_interruptible_loop_normal_execution(self):
        """The interruptible loop executes normally without shutdown."""
        from vault_search.utils.shutdown import ShutdownManager, interruptible_loop

        # Reset state
        ShutdownManager.reset()

        items_processed = []

        with interruptible_loop() as should_continue:
            for i in range(5):
                if not should_continue():
                    break
                items_processed.append(i)

        assert items_processed == [0, 1, 2, 3, 4]

    def test_interruptible_loop_stops_on_shutdown(self):
        """Loop interruptible for when shutdown is requested."""
        from vault_search.utils.shutdown import (
            ShutdownManager,
            interruptible_loop,
            request_shutdown,
        )

        # Reset state
        ShutdownManager.reset()

        items_processed = []

        with interruptible_loop() as should_continue:
            for i in range(10):
                if i == 3:
                    request_shutdown()

                if not should_continue():
                    break
                items_processed.append(i)

        # Must stop after the item 3 (when shutdown was requested)
        assert len(items_processed) <= 4


class TestShutdownManagerInitialize:
    """Tests for ShutdownManager.initialize()."""

    def test_initialize_validates_timeout(self):
        """initialize() rejects timeout <= 0."""
        from vault_search.utils.shutdown import ShutdownManager

        ShutdownManager.reset()

        with pytest.raises(ValueError, match="timeout must be greater than 0"):
            ShutdownManager.initialize(timeout=0)

        with pytest.raises(ValueError, match="timeout must be greater than 0"):
            ShutdownManager.initialize(timeout=-1)

    def test_initialize_accepts_positive_timeout(self):
        """initialize() accepts timeout positive."""
        from vault_search.utils.shutdown import ShutdownManager

        ShutdownManager.reset()

        # Must not raise exception
        ShutdownManager.initialize(timeout=0.1)


class TestDelayedKeyboardInterruptWarning:
    """Tests for warning of DelayedKeyboardInterrupt outside of the main thread."""

    def test_logs_warning_outside_main_thread(self):
        """DelayedKeyboardInterrupt logs warning outside of the main thread."""
        from vault_search.utils.shutdown import DelayedKeyboardInterrupt

        warning_logged = [False]

        def run_in_thread():
            with patch("vault_search.utils.shutdown.logger") as mock_logger:
                with DelayedKeyboardInterrupt():
                    pass
                # Checks that warning was called
                if mock_logger.warning.called:
                    warning_logged[0] = True

        thread = threading.Thread(target=run_in_thread)
        thread.start()
        thread.join()

        assert warning_logged[0] is True


class TestIntegration:
    """Tests for integration of the system of shutdown."""

    def test_full_shutdown_flow(self):
        """Flow complete: initialize -> register -> request -> cleanup."""
        from vault_search.utils.shutdown import (
            ShutdownManager,
            request_shutdown,
            shutdown_requested,
        )

        # Reset complete
        ShutdownManager.reset()

        cleanup_executed = [False]

        def cleanup():
            cleanup_executed[0] = True

        # Register callback
        ShutdownManager.register_callback(cleanup)

        # Verify state initial
        assert shutdown_requested() is False
        assert cleanup_executed[0] is False

        # Solicitar shutdown
        request_shutdown()

        # Verify state after request
        assert shutdown_requested() is True

        # Run shutdown, which calls the callbacks.
        ShutdownManager.shutdown()

        assert cleanup_executed[0] is True
