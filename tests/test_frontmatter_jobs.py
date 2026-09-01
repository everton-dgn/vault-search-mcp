"""
Tests for queue asynchronous of enrichment of frontmatter.
"""

import threading
import time

from vault_search.server.frontmatter_jobs import FrontmatterEnrichmentJobManager


class _DummyIndexer:
    def __init__(self):
        self.reindexed: list[str] = []

    def reindex_note(self, path: str):
        self.reindexed.append(path)
        return {"status": "updated"}


class _DummySearcher:
    def __init__(self):
        self.invalidations = 0

    def invalidate_cache(self):
        self.invalidations += 1


class _DummyLogger:
    def warning(self, *args, **kwargs):
        return None


def _wait_job_completion(
    manager: FrontmatterEnrichmentJobManager,
    job_id: str,
    timeout: float = 2.0,
):
    start = time.time()
    while time.time() - start < timeout:
        status = manager.get_status(job_id=job_id)
        if status.get("found") and status["job"]["status"] in (
            "completed",
            "completed_with_errors",
        ):
            return status["job"]
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} not finished in the timeout")


def _successful_enrichment(path: str) -> dict:
    return {
        "success": True,
        "message": f"ok:{path}",
        "frontmatter_enriched": False,
        "frontmatter_fields_filled": 0,
    }


def test_enqueue_rejected_when_ai_disabled(monkeypatch):
    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.is_ai_enrichment_enabled", lambda: False
    )
    manager = FrontmatterEnrichmentJobManager(_DummyIndexer(), _DummySearcher(), _DummyLogger())

    result = manager.enqueue(["note.md"])

    assert result["accepted"] is False
    assert result["queued_paths"] == 0


def test_background_job_processes_paths_and_tracks_status(monkeypatch):
    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.is_ai_enrichment_enabled", lambda: True
    )

    def fake_enrich(path: str):
        if path == "ok.md":
            return {
                "success": True,
                "message": "ok",
                "frontmatter_enriched": True,
                "frontmatter_fields_filled": 2,
            }
        return {
            "success": False,
            "message": "error",
            "error_code": "required_missing",
        }

    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.enrich_note_frontmatter_required", fake_enrich
    )

    indexer = _DummyIndexer()
    searcher = _DummySearcher()
    manager = FrontmatterEnrichmentJobManager(indexer, searcher, _DummyLogger())

    enqueued = manager.enqueue(["ok.md", "failure.md"], reason="test")
    assert enqueued["accepted"] is True

    job = _wait_job_completion(manager, enqueued["job_id"])

    assert job["status"] == "completed_with_errors"
    assert job["total"] == 2
    assert job["processed"] == 2
    assert job["succeeded"] == 1
    assert job["failed"] == 1
    assert indexer.reindexed == ["ok.md"]
    assert searcher.invalidations == 1
    assert job["returned"] == 2
    assert job["truncated"] == 0

    manager.stop()


def test_active_jobs_are_never_pruned_when_capacity_is_full(monkeypatch):
    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.is_ai_enrichment_enabled", lambda: True
    )
    worker_started = threading.Event()
    release_worker = threading.Event()

    def blocking_first_job(path: str) -> dict:
        if path == "running.md":
            worker_started.set()
            assert release_worker.wait(timeout=2.0)
        return _successful_enrichment(path)

    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.enrich_note_frontmatter_required",
        blocking_first_job,
    )
    manager = FrontmatterEnrichmentJobManager(
        _DummyIndexer(),
        _DummySearcher(),
        _DummyLogger(),
        max_pending=200,
        max_history=200,
    )

    first = manager.enqueue(["running.md"])
    assert first["accepted"] is True
    assert worker_started.wait(timeout=1.0)

    accepted = [first]
    for index in range(200):
        result = manager.enqueue([f"queued-{index}.md"])
        assert result["accepted"] is True
        accepted.append(result)

    for result in accepted:
        assert manager.get_status(job_id=result["job_id"])["found"] is True

    rejected = manager.enqueue(["overflow.md"])
    assert rejected == {
        "accepted": False,
        "error_code": "queue_full",
        "reason": "Enrichment queue is full",
        "queued_paths": 0,
    }

    release_worker.set()
    manager.stop()


def test_paths_are_deduplicated_and_closed_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.is_ai_enrichment_enabled", lambda: True
    )
    processed: list[str] = []

    def record_path(path: str) -> dict:
        processed.append(path)
        return _successful_enrichment(path)

    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.enrich_note_frontmatter_required",
        record_path,
    )
    manager = FrontmatterEnrichmentJobManager(
        _DummyIndexer(),
        _DummySearcher(),
        _DummyLogger(),
        max_paths_per_job=2,
    )

    accepted = manager.enqueue(["a.md", "a.md", "ignored.txt", "b.MD"])
    assert accepted["accepted"] is True
    assert accepted["queued_paths"] == 2
    _wait_job_completion(manager, accepted["job_id"])
    assert processed == ["a.md", "b.MD"]

    rejected = manager.enqueue(["a.md", "b.md", "c.md"])
    assert rejected == {
        "accepted": False,
        "error_code": "too_many_paths",
        "reason": "Path count exceeds the per-job limit",
        "queued_paths": 0,
        "max_paths": 2,
    }

    manager.stop()


def test_results_are_bounded_and_terminal_snapshot_is_stable(monkeypatch):
    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.is_ai_enrichment_enabled", lambda: True
    )
    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.enrich_note_frontmatter_required",
        _successful_enrichment,
    )
    manager = FrontmatterEnrichmentJobManager(
        _DummyIndexer(),
        _DummySearcher(),
        _DummyLogger(),
        max_results=2,
    )

    enqueued = manager.enqueue(["a.md", "b.md", "c.md", "d.md"])
    terminal = _wait_job_completion(manager, enqueued["job_id"])
    assert terminal["status"] == "completed"
    assert terminal["processed"] == 4
    assert len(terminal["results"]) == 2
    assert terminal["returned"] == 2
    assert terminal["truncated"] == 2

    terminal["results"].clear()
    second_snapshot = manager.get_status(job_id=enqueued["job_id"])["job"]
    assert second_snapshot["status"] == "completed"
    assert len(second_snapshot["results"]) == 2
    assert second_snapshot["returned"] == 2
    assert second_snapshot["truncated"] == 2

    manager.stop()


def test_terminal_history_is_limited_without_pruning_active_jobs(monkeypatch):
    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.is_ai_enrichment_enabled", lambda: True
    )
    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.enrich_note_frontmatter_required",
        _successful_enrichment,
    )
    manager = FrontmatterEnrichmentJobManager(
        _DummyIndexer(),
        _DummySearcher(),
        _DummyLogger(),
        max_history=2,
    )

    ids: list[str] = []
    for index in range(3):
        enqueued = manager.enqueue([f"note-{index}.md"])
        ids.append(enqueued["job_id"])
        _wait_job_completion(manager, enqueued["job_id"])

    assert manager.get_status(job_id=ids[0])["found"] is False
    assert manager.get_status(job_id=ids[1])["found"] is True
    assert manager.get_status(job_id=ids[2])["found"] is True

    manager.stop()


def test_stop_returns_within_deadline_and_stops_accepting(monkeypatch):
    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.is_ai_enrichment_enabled", lambda: True
    )
    worker_started = threading.Event()
    release_worker = threading.Event()

    def blocking_enrichment(path: str) -> dict:
        worker_started.set()
        assert release_worker.wait(timeout=2.0)
        return _successful_enrichment(path)

    monkeypatch.setattr(
        "vault_search.server.frontmatter_jobs.enrich_note_frontmatter_required",
        blocking_enrichment,
    )
    manager = FrontmatterEnrichmentJobManager(
        _DummyIndexer(),
        _DummySearcher(),
        _DummyLogger(),
        join_timeout=0.05,
    )
    enqueued = manager.enqueue(["blocked.md"])
    assert worker_started.wait(timeout=1.0)

    started_at = time.monotonic()
    manager.stop()
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.3
    assert manager.get_status(job_id=enqueued["job_id"])["found"] is True
    assert manager.enqueue(["after-stop.md"])["error_code"] == "stopped"

    release_worker.set()
    manager.stop()
