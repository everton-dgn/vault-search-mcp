"""
File watcher para reindexação automática do vault.

Monitora o vault Obsidian e reindexar notas quando são
criadas, modificadas ou deletadas.

Usa uma ÚNICA thread worker com fila coalescente ao invés de
uma thread Timer por arquivo, evitando exaustão de recursos
em bursts de edições (ex: rename de pasta com 100 notas).

Também mantém o catálogo SQLite atualizado para list_notes().
"""

import logging
import threading
import time
from collections.abc import Callable

from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from vault_search.config.paths import VAULT_PATH
from vault_search.config.watcher import (
    THREAD_JOIN_TIMEOUT,
    WATCHER_DEBOUNCE,
    WATCHER_POLL_FACTOR,
)
from vault_search.core.indexer import VaultIndexer
from vault_search.crud.catalog import get_catalog
from vault_search.server.event_handler import PendingEvent, VaultEventHandler
from vault_search.utils.logging import configure_logging
from vault_search.utils.shutdown import shutdown_requested

logger = logging.getLogger(__name__)


class VaultWatcher:
    """
    Monitora o vault e reindexar notas automaticamente.

    Usa uma única worker thread com fila coalescente para debounce.
    Evita criar uma thread Timer por arquivo (que causaria exaustão
    de threads em bursts como rename de pastas).

    Uso:
        watcher = VaultWatcher(indexer, on_reindex=searcher.invalidate_cache)
        watcher.start()
        watcher.stop()
    """

    def __init__(
        self,
        indexer: VaultIndexer,
        on_reindex: Callable[[], None] | None = None,
    ):
        """
        Parâmetros:
            indexer: instância do indexer
            on_reindex: callback pós-reindexação (ex: searcher.invalidate_cache)
        """
        self._indexer = indexer
        self._on_reindex = on_reindex
        self._observer: BaseObserver | None = None
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pending: dict[str, PendingEvent] = {}
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()

    def start(self) -> bool:
        """Inicia monitoramento, se nenhuma geração anterior ainda estiver viva."""
        with self._lifecycle_lock:
            observer_alive = self._observer is not None and self._observer.is_alive()
            worker_alive = self._worker is not None and self._worker.is_alive()
            if observer_alive or worker_alive:
                logger.warning("watcher_start_rejected reason=previous_generation_alive")
                return False

            # Referências mortas podem sobrar após um stop que expirou.
            self._observer = None
            self._worker = None
            with self._lock:
                self._pending.clear()

            vault_root = VAULT_PATH.expanduser().resolve(strict=False)

            # Validar que o vault existe
            if not vault_root.exists():
                logger.error("watcher_start_failed reason=vault_not_found")
                raise FileNotFoundError("Vault não encontrado")

            if not vault_root.is_dir():
                logger.error("watcher_start_failed reason=vault_not_directory")
                raise NotADirectoryError("VAULT_PATH não é um diretório")

            handler = VaultEventHandler(self._pending, self._lock)
            observer = Observer()
            observer.schedule(handler, str(vault_root), recursive=True)
            observer.daemon = True

            # Worker thread única para processar fila
            self._stop_event.clear()
            worker = threading.Thread(target=self._worker_loop, daemon=True)
            # Publicar referências antes de iniciar: uma falha parcial ainda
            # pode deixar recursos vivos que precisam bloquear novo start.
            self._observer = observer
            self._worker = worker
            try:
                observer.start()
                worker.start()
            except Exception:
                self._stop_event.set()
                if observer.is_alive():
                    observer.stop()
                    observer.join(timeout=THREAD_JOIN_TIMEOUT)
                if worker.is_alive():
                    worker.join(timeout=THREAD_JOIN_TIMEOUT)
                self._observer = observer if observer.is_alive() else None
                self._worker = worker if worker.is_alive() else None
                raise

        logger.info("file_watcher_started")
        return True

    def _worker_loop(self) -> None:
        """
        Loop do worker que processa eventos pendentes com debounce.

        Acorda a cada WATCHER_DEBOUNCE/WATCHER_POLL_FACTOR segundos e
        processa eventos que já passaram do tempo de debounce.
        """
        poll_interval = WATCHER_DEBOUNCE / WATCHER_POLL_FACTOR
        while not self._stop_event.is_set() and not shutdown_requested():
            self._stop_event.wait(timeout=poll_interval)

            # Coletar eventos prontos (que passaram do debounce)
            now = time.monotonic()
            ready: dict[str, PendingEvent] = {}
            with self._lock:
                expired_keys = [
                    k for k, v in self._pending.items() if now - v["time"] >= WATCHER_DEBOUNCE
                ]
                for k in expired_keys:
                    ready[k] = self._pending.pop(k)

            # Processar fora do lock
            for relative_path, info in ready.items():
                try:
                    if info["deleted"]:
                        logger.info("watcher_reindex event=deleted")
                    else:
                        logger.info("watcher_reindex event=changed")

                    # Atualizar índice vetorial
                    self._indexer.reindex_note(relative_path)

                    # Atualizar catálogo SQLite
                    try:
                        catalog = get_catalog()
                        if info["deleted"]:
                            catalog.delete(relative_path)
                        else:
                            catalog.upsert(relative_path)
                    except Exception as ce:
                        logger.warning(
                            "watcher_catalog_update_failed error_type=%s",
                            type(ce).__name__,
                        )

                    if self._on_reindex:
                        self._on_reindex()
                except Exception as e:
                    logger.error(
                        "watcher_reindex_failed error_type=%s",
                        type(e).__name__,
                    )

    def stop(self) -> bool:
        """Solicita parada e informa se todos os recursos terminaram no prazo."""
        with self._lifecycle_lock:
            observer = self._observer
            worker = self._worker

            # Parar o produtor antes de sinalizar a worker.
            if observer is not None:
                observer.stop()
            self._stop_event.set()

            if observer is not None:
                observer.join(timeout=THREAD_JOIN_TIMEOUT)
            if worker is not None:
                worker.join(timeout=THREAD_JOIN_TIMEOUT)

            observer_alive = observer is not None and observer.is_alive()
            worker_alive = worker is not None and worker.is_alive()

            # Nunca perca a referência de uma geração que ainda pode executar.
            self._observer = observer if observer_alive else None
            self._worker = worker if worker_alive else None
            stopped = not observer_alive and not worker_alive

            if stopped:
                with self._lock:
                    self._pending.clear()

        if stopped:
            logger.info("file_watcher_stopped")
        else:
            logger.error("watcher_stop_timeout")
        return stopped

    @property
    def is_running(self) -> bool:
        """Retorna True se o watcher está ativo."""
        with self._lifecycle_lock:
            observer_alive = self._observer is not None and self._observer.is_alive()
            worker_alive = self._worker is not None and self._worker.is_alive()
            return observer_alive or worker_alive


if __name__ == "__main__":
    configure_logging()

    indexer = VaultIndexer()
    watcher = VaultWatcher(indexer)
    watcher.start()

    logger.info("watcher_standalone_started")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
        logger.info("watcher_standalone_stopped")
