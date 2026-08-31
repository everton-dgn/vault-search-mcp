"""Tests of the queue bounded and coalescing of reindexing."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from vault_search.server.reindex_queue import ReindexQueue


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_repeated_path_uses_one_worker_and_one_followup_pass():
    started = threading.Event()
    release = threading.Event()
    calls = []
    indexer = MagicMock()

    def reindex(path: str):
        calls.append(path)
        if len(calls) == 1:
            started.set()
            release.wait(2)
        return {"status": "updated"}

    indexer.reindex_note.side_effect = reindex
    scheduler = ReindexQueue(indexer, MagicMock(), MagicMock(), max_pending=10)

    assert scheduler.enqueue("note.md") == "queued"
    assert started.wait(1)
    statuses = [scheduler.enqueue("note.md") for _ in range(1000)]
    assert set(statuses) == {"coalesced"}
    assert scheduler.worker_count == 1
    assert scheduler.pending_count == 1
    release.set()
    _wait_until(lambda: scheduler.pending_count == 0)
    scheduler.stop()

    assert calls == ["note.md", "note.md"]


def test_unique_pending_paths_are_bounded():
    started = threading.Event()
    release = threading.Event()
    indexer = MagicMock()

    def reindex(path: str):
        started.set()
        release.wait(2)
        return {"status": "updated"}

    indexer.reindex_note.side_effect = reindex
    scheduler = ReindexQueue(indexer, MagicMock(), MagicMock(), max_pending=2)

    assert scheduler.enqueue("one.md") == "queued"
    assert started.wait(1)
    assert scheduler.enqueue("two.md") == "queued"
    assert scheduler.enqueue("three.md") == "queue_full"
    release.set()
    _wait_until(lambda: scheduler.pending_count == 0)
    scheduler.stop()


def test_sync_requests_coalesce():
    indexer = MagicMock()
    indexer.sync_check.return_value = {"synced": 0}
    scheduler = ReindexQueue(indexer, MagicMock(), MagicMock())

    first = scheduler.enqueue_sync()
    second = scheduler.enqueue_sync()
    _wait_until(lambda: scheduler.pending_count == 0)
    scheduler.stop()

    assert first == "queued"
    assert second in {"coalesced", "queued"}
    assert 1 <= indexer.sync_check.call_count <= 2
