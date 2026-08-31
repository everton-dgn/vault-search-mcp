"""
Graceful shutdown para operações de longa duração.

Garante que indexação e outras operações terminem limpo quando
o processo recebe SIGTERM ou SIGINT (Ctrl+C).

Padrões implementados:
1. DelayedKeyboardInterrupt - protege seções críticas de interrupção
2. ShutdownManager - coordena shutdown global com callbacks
3. shutdown_requested() - flag global para verificar estado

Uso:
    from vault_search.utils.shutdown import (
        DelayedKeyboardInterrupt,
        ShutdownManager,
        shutdown_requested,
    )

    # Proteger seção crítica
    with DelayedKeyboardInterrupt():
        save_important_data()

    # Verificar se deve parar
    while not shutdown_requested():
        process_next_item()

    # Registrar cleanup callback
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
# Estado global de shutdown
# =============================================================================


class _ShutdownState:
    """Estado interno do sistema de shutdown."""

    def __init__(self):
        self._shutdown_requested = threading.Event()
        self._shutdown_in_progress = threading.Event()
        self._shutdown_transition_lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []
        self._callbacks_lock = threading.Lock()
        self._original_handlers: dict[signal.Signals, SignalHandler] = {}
        self._initialized = False

    def request_shutdown(self) -> None:
        """Marca que shutdown foi solicitado."""
        self._shutdown_requested.set()

    def is_shutdown_requested(self) -> bool:
        """Verifica se shutdown foi solicitado."""
        return self._shutdown_requested.is_set()

    def start_shutdown(self) -> None:
        """Marca que shutdown está em progresso."""
        self._shutdown_in_progress.set()

    def try_start_shutdown(self) -> bool:
        """Garante que somente um caller execute os callbacks."""
        with self._shutdown_transition_lock:
            if self._shutdown_in_progress.is_set():
                return False
            self._shutdown_in_progress.set()
            return True

    def is_shutdown_in_progress(self) -> bool:
        """Verifica se shutdown está em progresso."""
        return self._shutdown_in_progress.is_set()

    def register_callback(self, callback: Callable[[], None]) -> None:
        """Registra callback para ser chamado no shutdown."""
        with self._callbacks_lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[], None]) -> None:
        """Remove callback registrado."""
        with self._callbacks_lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def run_callbacks(self) -> None:
        """Executa todos os callbacks registrados."""
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
# API pública - funções de conveniência
# =============================================================================


def shutdown_requested() -> bool:
    """
    Verifica se shutdown foi solicitado.

    Use em loops de processamento para sair graciosamente:

        while not shutdown_requested():
            process_next_item()

    Retorna:
        True se SIGTERM/SIGINT foi recebido.
    """
    return _state.is_shutdown_requested()


def request_shutdown() -> None:
    """
    Solicita shutdown programaticamente.

    Útil para testes ou shutdown iniciado por código.
    """
    _state.request_shutdown()


def wait_for_shutdown(timeout: float | None = None) -> bool:
    """
    Bloqueia até que shutdown seja solicitado.

    Parâmetros:
        timeout: segundos para esperar (None = infinito)

    Retorna:
        True se shutdown foi solicitado, False se timeout.
    """
    return _state._shutdown_requested.wait(timeout)


# =============================================================================
# ShutdownManager - coordenador de shutdown
# =============================================================================


class ShutdownManager:
    """
    Gerenciador de graceful shutdown.

    Coordena signal handlers e callbacks de cleanup.

    Uso:
        # Inicializar no startup da aplicação
        ShutdownManager.initialize()

        # Registrar cleanup
        ShutdownManager.register_callback(close_database)
        ShutdownManager.register_callback(stop_watcher)

        # No final (ou via atexit)
        ShutdownManager.shutdown()
    """

    _timeout: float = 30.0  # Timeout para shutdown

    @classmethod
    def initialize(cls, timeout: float = 30.0) -> None:
        """
        Inicializa o gerenciador de shutdown.

        Instala signal handlers para SIGTERM e SIGINT.
        Registra atexit handler para cleanup.

        Parâmetros:
            timeout: segundos máximos para aguardar callbacks (deve ser > 0)

        Raises:
            ValueError: se timeout <= 0
        """
        if timeout <= 0:
            raise ValueError(f"timeout deve ser > 0, recebido: {timeout}")

        if _state._initialized:
            logger.debug("ShutdownManager já inicializado")
            return

        cls._timeout = timeout

        # Instalar signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                old_handler = signal.signal(sig, cls._signal_handler)
                _state._original_handlers[sig] = old_handler
            except (ValueError, OSError) as e:
                # Pode falhar em threads não-main ou ambientes restritos
                logger.warning(
                    "shutdown_handler_install_failed signal=%s error_type=%s",
                    sig,
                    type(e).__name__,
                )

        # Registrar atexit
        atexit.register(cls._atexit_handler)

        _state._initialized = True
        logger.debug("ShutdownManager inicializado")

    @classmethod
    def _signal_handler(cls, signum: int, frame: FrameType | None) -> None:
        """Handler interno para sinais."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Recebido {sig_name}, iniciando graceful shutdown...")

        _state.request_shutdown()

        # Se já está em shutdown, força saída
        if _state.is_shutdown_in_progress():
            logger.warning("Segundo sinal recebido, forçando saída")
            sys.exit(128 + signum)

        # Executar shutdown em thread separada para não bloquear
        shutdown_thread = threading.Thread(target=cls.shutdown, daemon=True)
        shutdown_thread.start()

    @classmethod
    def _atexit_handler(cls) -> None:
        """Handler para atexit."""
        if not _state.is_shutdown_in_progress():
            cls.shutdown()

    @classmethod
    def shutdown(cls) -> None:
        """
        Executa shutdown gracioso.

        Chama todos os callbacks registrados em ordem LIFO.
        Aguarda até timeout para conclusão.
        """
        if not _state.try_start_shutdown():
            return

        _state.request_shutdown()

        logger.info("Executando callbacks de shutdown...")
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
        logger.info("Shutdown completo")

    @classmethod
    def register_callback(cls, callback: Callable[[], None]) -> None:
        """
        Registra callback para ser chamado no shutdown.

        Callbacks são executados em ordem LIFO (último registrado primeiro).

        Parâmetros:
            callback: função sem argumentos para cleanup
        """
        _state.register_callback(callback)

    @classmethod
    def unregister_callback(cls, callback: Callable[[], None]) -> None:
        """
        Remove callback registrado.

        Parâmetros:
            callback: função previamente registrada
        """
        _state.unregister_callback(callback)

    @classmethod
    def reset(cls) -> None:
        """
        Reseta estado do manager (para testes).

        ATENÇÃO: Não usar em produção.
        """
        global _state
        # Restaurar signal handlers originais antes de resetar
        for sig, handler in _state._original_handlers.items():
            try:
                signal.signal(sig, handler)
            except ValueError, OSError:
                pass
        _state = _ShutdownState()


# =============================================================================
# DelayedKeyboardInterrupt - protege seções críticas
# =============================================================================


class DelayedKeyboardInterrupt:
    """
    Context manager que adia interrupções durante seções críticas.

    Sinais SIGINT e SIGTERM são capturados e "enfileirados" durante
    o bloco protegido. Ao sair do bloco, o sinal é re-enviado.

    Isso garante que operações como salvar dados ou fechar conexões
    não sejam interrompidas no meio.

    Uso:
        with DelayedKeyboardInterrupt():
            # Este bloco não será interrompido
            save_critical_data()
            close_database_connection()

        # Aqui o sinal será processado se foi recebido

    Nota:
        - Funciona apenas na thread principal
        - Sinais múltiplos são coalescidos (apenas o último é re-enviado)
    """

    def __init__(self):
        self._signal: signal.Signals | None = None
        self._frame: FrameType | None = None
        self._old_handlers: dict[signal.Signals, SignalHandler] = {}

    def __enter__(self) -> DelayedKeyboardInterrupt:
        # Só funciona na thread principal
        if threading.current_thread() is not threading.main_thread():
            logger.warning(
                "DelayedKeyboardInterrupt usado fora da main thread - proteção desabilitada",
                extra={"thread_name": threading.current_thread().name},
            )
            return self

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                self._old_handlers[sig] = signal.signal(sig, self._handler)
        except ValueError, OSError:
            # Ambiente não suporta signal handling
            pass

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        # Restaurar handlers originais
        for sig, handler in self._old_handlers.items():
            try:
                signal.signal(sig, handler)
            except ValueError, OSError:
                pass

        # Re-enviar sinal capturado
        if self._signal is not None:
            old_handler = self._old_handlers.get(self._signal)
            if old_handler and callable(old_handler):
                old_handler(self._signal, self._frame)
            elif old_handler == signal.SIG_DFL:
                # Default handler - re-raise como KeyboardInterrupt
                if self._signal == signal.SIGINT:
                    raise KeyboardInterrupt()

        return False  # Não suprimir exceções

    def _handler(self, signum: int, frame: FrameType | None) -> None:
        """Captura sinal para processamento posterior."""
        self._signal = signal.Signals(signum)
        self._frame = frame
        logger.debug(f"Sinal {self._signal.name} adiado até fim da seção crítica")


# =============================================================================
# Context managers de conveniência
# =============================================================================


@contextmanager
def protected_section(description: str = "operação crítica"):
    """
    Context manager para seções protegidas com logging.

    Parâmetros:
        description: descrição da operação para logs

    Uso:
        with protected_section("salvando índice"):
            index.save()
    """
    logger.debug(f"Iniciando seção protegida: {description}")
    try:
        with DelayedKeyboardInterrupt():
            yield
    finally:
        logger.debug(f"Finalizando seção protegida: {description}")


@contextmanager
def interruptible_loop():
    """
    Context manager que verifica shutdown a cada iteração.

    Uso:
        with interruptible_loop() as should_continue:
            for item in items:
                if not should_continue():
                    break
                process(item)
    """

    def should_continue() -> bool:
        return not shutdown_requested()

    yield should_continue
