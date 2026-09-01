"""
Filesystem watcher for automatic vault reindexing.

Watches the configured vault and reindexes notes after filesystem changes.

Uses one worker with a coalescing queue instead of one timer thread per file.

Also keeps the SQLite catalog synchronized for list_notes.
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
from vault_search.utils.logging import configure_logging
from vault_search.utils.shutdown import shutdown_requested
from vault_search.watching.event_handler import PendingEvent, VaultEventHandler

logger = logging.getLogger(__name__)


class VaultWatcher:
    """
    Watch a vault and reindex notes automatically.

    One worker thread debounces a path-coalescing event queue.

    Example:
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
        Parameters:
            indexer: vault indexer
            on_reindex: callback after a successful reindex
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
        """Start watching if no previous generation is still alive."""
        with self._lifecycle_lock:
            observer_alive = self._observer is not None and self._observer.is_alive()
            worker_alive = self._worker is not None and self._worker.is_alive()
            if observer_alive or worker_alive:
                logger.warning("watcher_start_rejected reason=previous_generation_alive")
                return False

            # Dead references may remain after a stop timeout.
            self._observer = None
            self._worker = None
            with self._lock:
                self._pending.clear()

            vault_root = VAULT_PATH.expanduser().resolve(strict=False)

            # Validate the vault root.
            if not vault_root.exists():
                logger.error("watcher_start_failed reason=vault_not_found")
                raise FileNotFoundError("Vault was not found")

            if not vault_root.is_dir():
                logger.error("watcher_start_failed reason=vault_not_directory")
                raise NotADirectoryError("VAULT_PATH is not a directory")

            handler = VaultEventHandler(self._pending, self._lock)
            observer = Observer()
            observer.schedule(handler, str(vault_root), recursive=True)
            observer.daemon = True

            # One worker processes the queue.
            self._stop_event.clear()
            worker = threading.Thread(target=self._worker_loop, daemon=True)
            # Publish references before starting so partial failure retains any
            # live resources that must block a second start.
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
        Process pending events after their debounce window.

        Poll at WATCHER_DEBOUNCE/WATCHER_POLL_FACTOR intervals.
        """
        poll_interval = WATCHER_DEBOUNCE / WATCHER_POLL_FACTOR
        while not self._stop_event.is_set() and not shutdown_requested():
            self._stop_event.wait(timeout=poll_interval)

            # Collect events whose debounce window has elapsed.
            now = time.monotonic()
            ready: dict[str, PendingEvent] = {}
            with self._lock:
                expired_keys = [
                    k for k, v in self._pending.items() if now - v["time"] >= WATCHER_DEBOUNCE
                ]
                for k in expired_keys:
                    ready[k] = self._pending.pop(k)

            # Process events outside the lock.
            for relative_path, info in ready.items():
                try:
                    if info["deleted"]:
                        logger.info("watcher_reindex event=deleted")
                    else:
                        logger.info("watcher_reindex event=changed")

                    # Update the search index.
                    self._indexer.reindex_note(relative_path)

                    # Update the SQLite catalog.
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
        """Request shutdown and report whether every resource met the deadline."""
        with self._lifecycle_lock:
            observer = self._observer
            worker = self._worker

            # Stop the producer before signaling the worker.
            if observer is not None:
                observer.stop()
            self._stop_event.set()

            if observer is not None:
                observer.join(timeout=THREAD_JOIN_TIMEOUT)
            if worker is not None:
                worker.join(timeout=THREAD_JOIN_TIMEOUT)

            observer_alive = observer is not None and observer.is_alive()
            worker_alive = worker is not None and worker.is_alive()

            # Retain references to any generation that may still execute.
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
        """Return whether the watcher is active."""
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
