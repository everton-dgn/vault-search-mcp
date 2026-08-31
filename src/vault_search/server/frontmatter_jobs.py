"""
Fila de enriquecimento de frontmatter em background.
"""

import queue
import threading
import time
import uuid
from typing import Any

from vault_search.crud.types import error_result
from vault_search.crud.write import enrich_note_frontmatter_required, is_ai_enrichment_enabled


class FrontmatterEnrichmentJobManager:
    """Gerencia jobs assíncronos de enriquecimento por nota ou lote."""

    _TERMINAL_STATUSES = frozenset({"completed", "completed_with_errors", "failed"})

    def __init__(
        self,
        indexer,
        searcher,
        logger,
        *,
        max_pending: int = 200,
        max_paths_per_job: int = 1000,
        max_history: int = 200,
        max_results: int = 100,
        join_timeout: float = 5.0,
    ):
        self._indexer = indexer
        self._searcher = searcher
        self._logger = logger
        self._max_pending = max(1, max_pending)
        self._max_paths_per_job = max(1, max_paths_per_job)
        self._max_history = max(1, max_history)
        self._max_results = max(1, max_results)
        self._join_timeout = max(0.01, join_timeout)
        self._queue: queue.Queue[tuple[str, list[str]]] = queue.Queue(maxsize=self._max_pending)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._accepting = True
        self._worker: threading.Thread | None = None

    def enqueue(self, paths: list[str], reason: str = "manual") -> dict[str, Any]:
        """Enfileira um novo job sem bloquear a thread da tool."""
        md_paths = list(
            dict.fromkeys(
                path for path in paths if isinstance(path, str) and path.lower().endswith(".md")
            )
        )
        if not md_paths:
            return {
                "accepted": False,
                "reason": "Nenhum path .md válido para enriquecimento",
                "queued_paths": 0,
            }

        if len(md_paths) > self._max_paths_per_job:
            return {
                "accepted": False,
                "error_code": "too_many_paths",
                "reason": "Quantidade de paths excede o limite por job",
                "queued_paths": 0,
                "max_paths": self._max_paths_per_job,
            }

        if not is_ai_enrichment_enabled():
            return {
                "accepted": False,
                "reason": "Enriquecimento por IA desabilitado em config",
                "queued_paths": 0,
            }

        job_id = str(uuid.uuid4())
        now = time.time()
        with self._lock:
            if not self._accepting:
                return {
                    "accepted": False,
                    "error_code": "stopped",
                    "reason": "Fila de enriquecimento encerrada",
                    "queued_paths": 0,
                }

            try:
                self._queue.put_nowait((job_id, md_paths))
            except queue.Full:
                return {
                    "accepted": False,
                    "error_code": "queue_full",
                    "reason": "Fila de enriquecimento cheia",
                    "queued_paths": 0,
                }

            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "reason": reason,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "total": len(md_paths),
                "processed": 0,
                "succeeded": 0,
                "failed": 0,
                "results": [],
                "returned": 0,
                "truncated": 0,
            }
            self._prune_terminal_jobs_locked()
            self._ensure_worker_locked()

        return {
            "accepted": True,
            "job_id": job_id,
            "status": "queued",
            "queued_paths": len(md_paths),
        }

    def get_status(self, job_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Retorna status de um job específico ou dos jobs recentes."""
        with self._lock:
            if job_id:
                job = self._jobs.get(job_id)
                if not job:
                    return {"found": False, "job_id": job_id}
                return {"found": True, "job": self._snapshot_job_locked(job)}

            recent = sorted(
                self._jobs.values(),
                key=lambda item: item["created_at"],
                reverse=True,
            )[: max(1, min(limit, 100))]
            return {
                "found": True,
                "jobs": [self._snapshot_job_locked(job) for job in recent],
                "total_jobs": len(self._jobs),
            }

    def stop(self) -> bool:
        """Para de aceitar jobs e aguarda a drenagem dentro do prazo configurado."""
        with self._lock:
            self._accepting = False
            worker = self._worker
        self._stop.set()
        if worker is None:
            return True

        worker.join(timeout=self._join_timeout)
        drained = not worker.is_alive()
        if not drained:
            self._logger.warning("frontmatter_worker_stop_timeout")
        return drained

    def _ensure_worker_locked(self) -> None:
        """Garante que a worker thread está ativa. Requer ``self._lock``."""
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="vault-frontmatter-worker",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        """Processa fila em background."""
        while not self._stop.is_set() or not self._queue.empty():
            try:
                job_id, paths = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue

            try:
                self._run_job(job_id, paths)
            except Exception as exc:
                self._logger.warning(
                    "frontmatter_job_failed error_type=%s",
                    type(exc).__name__,
                )
                self._mark_job_failed(job_id)
            finally:
                self._queue.task_done()

    def _run_job(self, job_id: str, paths: list[str]) -> None:
        """Executa enriquecimento de um job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = "running"
            job["started_at"] = time.time()

        for path in paths:
            try:
                result = enrich_note_frontmatter_required(path)
            except Exception as exc:
                self._logger.warning(
                    "frontmatter_enrichment_failed error_type=%s",
                    type(exc).__name__,
                )
                result = error_result(
                    path,
                    "Falha interna durante o enriquecimento",
                    error_code="internal_error",
                )
            entry = {
                "path": path,
                "success": bool(result.get("success")),
                "message": result.get("message", ""),
                "frontmatter_enriched": bool(result.get("frontmatter_enriched", False)),
                "frontmatter_fields_filled": int(result.get("frontmatter_fields_filled", 0)),
                "error_code": result.get("error_code"),
            }

            with self._lock:
                job = self._jobs.get(job_id)
                if not job:
                    continue
                job["processed"] += 1
                if entry["success"]:
                    job["succeeded"] += 1
                else:
                    job["failed"] += 1
                if len(job["results"]) < self._max_results:
                    job["results"].append(entry)
                    job["returned"] += 1
                else:
                    job["truncated"] += 1

            if entry["frontmatter_enriched"]:
                try:
                    self._indexer.reindex_note(path)
                    self._searcher.invalidate_cache()
                except Exception as exc:
                    self._logger.warning(
                        "frontmatter_reindex_after_enrich_failed error_type=%s",
                        type(exc).__name__,
                    )

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["finished_at"] = time.time()
            if job["failed"] > 0:
                job["status"] = "completed_with_errors"
            else:
                job["status"] = "completed"
            self._prune_terminal_jobs_locked()

    def _mark_job_failed(self, job_id: str) -> None:
        """Finaliza como falha um job interrompido por erro inesperado."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["status"] in self._TERMINAL_STATUSES:
                return
            job["status"] = "failed"
            job["finished_at"] = time.time()
            self._prune_terminal_jobs_locked()

    @staticmethod
    def _snapshot_job_locked(job: dict[str, Any]) -> dict[str, Any]:
        """Copia também a lista mutável de resultados da visão pública."""
        snapshot = dict(job)
        snapshot["results"] = [dict(result) for result in job["results"]]
        return snapshot

    def _prune_terminal_jobs_locked(self) -> None:
        """Limita apenas o histórico terminal; queued/running nunca são podados."""
        terminal = [
            item for item in self._jobs.items() if item[1]["status"] in self._TERMINAL_STATUSES
        ]
        if len(terminal) <= self._max_history:
            return
        ordered = sorted(terminal, key=lambda item: item[1]["created_at"])
        to_remove = len(terminal) - self._max_history
        for job_id, _ in ordered[:to_remove]:
            del self._jobs[job_id]
