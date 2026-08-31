"""Fila coalescente e limitada para atualização assíncrona do índice."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any


class ReindexQueue:
    """Executa uma reindexação por vez e coalesce alterações do mesmo path."""

    _SYNC_KEY = "\0sync"

    def __init__(
        self,
        indexer: Any,
        searcher: Any,
        logger: logging.Logger,
        *,
        max_pending: int = 1000,
        join_timeout: float = 5.0,
    ):
        self._indexer = indexer
        self._searcher = searcher
        self._logger = logger
        self._max_pending = max(1, max_pending)
        self._join_timeout = max(0.1, join_timeout)
        self._queue: queue.Queue[str] = queue.Queue()
        self._versions: dict[str, int] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._accepting = True

    def enqueue(self, path: str) -> str:
        """Agenda um path e devolve um estado fechado para a API pública."""
        return self._enqueue_key(path)

    def enqueue_sync(self) -> str:
        """Agenda uma única reconciliação completa, útil após mutações em lote."""
        return self._enqueue_key(self._SYNC_KEY)

    def _enqueue_key(self, key: str) -> str:
        with self._lock:
            if not self._accepting:
                return "stopped"
            if key in self._versions:
                self._versions[key] += 1
                return "coalesced"
            if len(self._versions) >= self._max_pending:
                return "queue_full"
            self._versions[key] = 1
            self._queue.put_nowait(key)
            self._ensure_worker_locked()
            return "queued"

    def _ensure_worker_locked(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="vault-reindex-worker",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                key = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            with self._lock:
                observed_version = self._versions.get(key)
            if observed_version is None:
                self._queue.task_done()
                continue

            try:
                if key == self._SYNC_KEY:
                    self._indexer.sync_check(auto_sync=True)
                else:
                    result = self._indexer.reindex_note(key)
                    status = result.get("status", "") if isinstance(result, dict) else ""
                    if status.startswith(("rejected", "error")):
                        self._logger.warning(
                            "background_reindex_failed status=%s",
                            status,
                        )
                self._searcher.invalidate_cache()
            except Exception as error:
                self._logger.warning(
                    "background_reindex_failed error_type=%s",
                    type(error).__name__,
                )
            finally:
                with self._lock:
                    current_version = self._versions.get(key)
                    if current_version == observed_version:
                        self._versions.pop(key, None)
                    elif current_version is not None:
                        self._queue.put_nowait(key)
                self._queue.task_done()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._versions)

    @property
    def worker_count(self) -> int:
        worker = self._worker
        return int(worker is not None and worker.is_alive())

    def stop(self) -> None:
        """Para de aceitar itens e drena a fila dentro de um prazo."""
        with self._lock:
            self._accepting = False
            worker = self._worker
        self._stop.set()
        if worker is not None:
            worker.join(timeout=self._join_timeout)
            if worker.is_alive():
                self._logger.warning("reindex_worker_stop_timeout")
