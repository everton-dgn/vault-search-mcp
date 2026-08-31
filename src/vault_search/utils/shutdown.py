"""
Graceful shutdown for long-running operations.

Ensure indexing and other operations stop cleanly when the process
receives SIGTERM or SIGINT.

Implemented patterns:
1. ``DelayedKeyboardInterrupt`` protects critical sections
2. ``ShutdownManager`` coordinates global shutdown callbacks
3. ``shutdown_requested()`` exposes the global shutdown state

Usage:
    from vault_search.utils.shutdown import (
        DelayedKeyboardInterrupt,
        ShutdownManager,
        shutdown_requested,
    )

    # Protect a critical section.
    with DelayedKeyboardInterrupt():
        save_important_data()

    # Check whether processing should stop.
    while not shutdown_requested():
        process_next_item()

    # Register a cleanup callback.
    ShutdownManager.register_callback(cleanup_resources)
"""

import atexit
import logging
import signal
import sys
import threading
from collections.abc import Callable
from contextlib import contextmanager
from types import FrameType, TracebackType
from typing import Literal

logger = logging.getLogger(__name__)

type SignalHandler = Callable[[int, FrameType | None], object] | int | None


# =============================================================================
# Global shutdown state
# =============================================================================


class _ShutdownState:
    """Internal shutdown state."""

    def __init__(self):
        self._shutdown_requested = threading.Event()
        self._shutdown_in_progress = threading.Event()
        self._shutdown_transition_lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []
        self._callbacks_lock = threading.Lock()
        self._original_handlers: dict[signal.Signals, SignalHandler] = {}
        self._initialized = False

    def request_shutdown(self) -> None:
        """Mark shutdown as requested."""
        self._shutdown_requested.set()

    def is_shutdown_requested(self) -> bool:
        """Return whether shutdown was requested."""
        return self._shutdown_requested.is_set()

    def start_shutdown(self) -> None:
        """Mark shutdown as in progress."""
        self._shutdown_in_progress.set()

    def try_start_shutdown(self) -> bool:
        """Ensure that only one caller runs the callbacks."""
        with self._shutdown_transition_lock:
            if self._shutdown_in_progress.is_set():
                return False
            self._shutdown_in_progress.set()
            return True

    def is_shutdown_in_progress(self) -> bool:
        """Return whether shutdown is in progress."""
        return self._shutdown_in_progress.is_set()

    def register_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to run during shutdown."""
        with self._callbacks_lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[], None]) -> None:
        """Remove a registered callback."""
        with self._callbacks_lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def run_callbacks(self) -> None:
        """Run every registered callback."""
        with self._callbacks_lock:
            callbacks = list(self._callbacks)

        for callback in reversed(callbacks):  # LIFO order
            try:
                callback()
            except Exception as e:
                logger.error(
                    "shutdown_callback_failed error_type=%s",
                    type(e).__name__,
                )


_state = _ShutdownState()


# =============================================================================
# Public convenience API
# =============================================================================


def shutdown_requested() -> bool:
    """
    Check whether shutdown was requested.

    Use this in processing loops to stop cleanly:

        while not shutdown_requested():
            process_next_item()

    Returns:
        ``True`` after SIGTERM or SIGINT is received.
    """
    return _state.is_shutdown_requested()


def request_shutdown() -> None:
    """
    Request shutdown programmatically.

    Useful in tests or for code-initiated shutdown.
    """
    _state.request_shutdown()


def wait_for_shutdown(timeout: float | None = None) -> bool:
    """
    Block until shutdown is requested.

    Parameters:
        timeout: Seconds to wait; ``None`` waits indefinitely.

    Returns:
        ``True`` when shutdown is requested, or ``False`` after a timeout.
    """
    return _state._shutdown_requested.wait(timeout)


# =============================================================================
# ShutdownManager coordinates shutdown
# =============================================================================


class ShutdownManager:
    """
    Graceful-shutdown manager.

    Coordinate signal handlers and cleanup callbacks.

    Usage:
        # Initialize during application startup.
        ShutdownManager.initialize()

        # Register cleanup callbacks.
        ShutdownManager.register_callback(close_database)
        ShutdownManager.register_callback(stop_watcher)

        # Run at the end or through atexit.
        ShutdownManager.shutdown()
    """

    _timeout: float = 30.0  # Shutdown timeout

    @classmethod
    def initialize(cls, timeout: float = 30.0) -> None:
        """
        Initialize the shutdown manager.

        Install signal handlers for SIGTERM and SIGINT.
        Register an atexit cleanup handler.

        Parameters:
            timeout: Maximum seconds to wait for callbacks; must be positive.

        Raises:
            ValueError: When ``timeout`` is not positive.
        """
        if timeout <= 0:
            raise ValueError(f"timeout must be greater than 0, received: {timeout}")

        if _state._initialized:
            logger.debug("ShutdownManager already initialized")
            return

        cls._timeout = timeout

        # Install signal handlers.
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                old_handler = signal.signal(sig, cls._signal_handler)
                _state._original_handlers[sig] = old_handler
            except (ValueError, OSError) as e:
                # This may fail outside the main thread or in restricted environments.
                logger.warning(
                    "shutdown_handler_install_failed signal=%s error_type=%s",
                    sig,
                    type(e).__name__,
                )

        # Register the atexit handler.
        atexit.register(cls._atexit_handler)

        _state._initialized = True
        logger.debug("ShutdownManager initialized")

    @classmethod
    def _signal_handler(cls, signum: int, frame: FrameType | None) -> None:
        """Handle operating-system signals."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}; starting graceful shutdown")

        _state.request_shutdown()

        # Force exit when shutdown is already in progress.
        if _state.is_shutdown_in_progress():
            logger.warning("Second signal received; forcing exit")
            sys.exit(128 + signum)

        # Run shutdown in a separate thread to avoid blocking the signal handler.
        shutdown_thread = threading.Thread(target=cls.shutdown, daemon=True)
        shutdown_thread.start()

    @classmethod
    def _atexit_handler(cls) -> None:
        """Handle atexit cleanup."""
        if not _state.is_shutdown_in_progress():
            cls.shutdown()

    @classmethod
    def shutdown(cls) -> None:
        """
        Perform graceful shutdown.

        Call registered callbacks in LIFO order and wait up to the timeout.
        """
        if not _state.try_start_shutdown():
            return

        _state.request_shutdown()

        logger.info("Running shutdown callbacks")
        callbacks_thread = threading.Thread(
            target=_state.run_callbacks,
            name="shutdown-callbacks",
            daemon=True,
        )
        callbacks_thread.start()
        callbacks_thread.join(cls._timeout)
        if callbacks_thread.is_alive():
            logger.error("shutdown_callbacks_timeout")
            return
        logger.info("Shutdown complete")

    @classmethod
    def register_callback(cls, callback: Callable[[], None]) -> None:
        """
        Register a callback to run during shutdown.

        Callbacks run in LIFO order.

        Parameters:
            callback: No-argument cleanup function.
        """
        _state.register_callback(callback)

    @classmethod
    def unregister_callback(cls, callback: Callable[[], None]) -> None:
        """
        Remove a registered callback.

        Parameters:
            callback: Previously registered function.
        """
        _state.unregister_callback(callback)

    @classmethod
    def reset(cls) -> None:
        """
        Reset manager state for tests.

        WARNING: Do not use in production.
        """
        global _state
        # Restore original signal handlers before resetting.
        for sig, handler in _state._original_handlers.items():
            try:
                signal.signal(sig, handler)
            except ValueError, OSError:
                pass
        _state = _ShutdownState()


# =============================================================================
# DelayedKeyboardInterrupt protects critical sections
# =============================================================================


class DelayedKeyboardInterrupt:
    """
    Delay interruptions while a critical section is running.

    Capture SIGINT and SIGTERM during the protected block and replay
    the last signal when leaving the block.

    This keeps operations such as saving data or closing connections
    from being interrupted halfway through.

    Usage:
        with DelayedKeyboardInterrupt():
            # This block will not be interrupted.
            save_critical_data()
            close_database_connection()

        # The signal is processed here when one was received.

    Note:
        - Works only on the main thread.
        - Multiple signals are coalesced and only the last one is replayed.
    """

    def __init__(self):
        self._signal: signal.Signals | None = None
        self._frame: FrameType | None = None
        self._old_handlers: dict[signal.Signals, SignalHandler] = {}

    def __enter__(self) -> DelayedKeyboardInterrupt:
        # Signal handling works only on the main thread.
        if threading.current_thread() is not threading.main_thread():
            logger.warning(
                "DelayedKeyboardInterrupt used outside the main thread; protection disabled",
                extra={"thread_name": threading.current_thread().name},
            )
            return self

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                self._old_handlers[sig] = signal.signal(sig, self._handler)
        except ValueError, OSError:
            # The environment does not support signal handling.
            pass

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        # Restore original handlers.
        for sig, handler in self._old_handlers.items():
            try:
                signal.signal(sig, handler)
            except ValueError, OSError:
                pass

        # Replay the captured signal.
        if self._signal is not None:
            old_handler = self._old_handlers.get(self._signal)
            if old_handler and callable(old_handler):
                old_handler(self._signal, self._frame)
            elif old_handler == signal.SIG_DFL:
                # Recreate KeyboardInterrupt for the default SIGINT handler.
                if self._signal == signal.SIGINT:
                    raise KeyboardInterrupt()

        return False  # Do not suppress exceptions.

    def _handler(self, signum: int, frame: FrameType | None) -> None:
        """Capture a signal for later processing."""
        self._signal = signal.Signals(signum)
        self._frame = frame
        logger.debug(f"Signal {self._signal.name} delayed until the critical section ends")


# =============================================================================
# Convenience context managers
# =============================================================================


@contextmanager
def protected_section(description: str = "critical operation"):
    """
    Protect a section and log its boundaries.

    Parameters:
        description: Operation description used in logs.

    Usage:
        with protected_section("saving index"):
            index.save()
    """
    logger.debug(f"Starting protected section: {description}")
    try:
        with DelayedKeyboardInterrupt():
            yield
    finally:
        logger.debug(f"Finishing protected section: {description}")


@contextmanager
def interruptible_loop():
    """
    Check shutdown state on every loop iteration.

    Usage:
        with interruptible_loop() as should_continue:
            for item in items:
                if not should_continue():
                    break
                process(item)
    """

    def should_continue() -> bool:
        return not shutdown_requested()

    yield should_continue
