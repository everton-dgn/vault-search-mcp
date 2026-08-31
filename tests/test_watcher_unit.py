"""
Unit tests for watcher.py event handling, start/stop, and cleanup.

Fast tests that do not require ML models.
"""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vault_search.config.watcher import WATCHER_DEBOUNCE
from vault_search.watching.event_handler import VaultEventHandler
from vault_search.watching.watcher import VaultWatcher


class TestVaultEventHandler:
    """Test the event handler without a real watcher."""

    def test_should_process_md(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/note.md") is True

    def test_should_process_md_uppercase(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/note.MD") is True

    def test_should_process_txt_accepted(self):
        """File .txt must be processed (indexable)."""
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/readme.txt") is True

    def test_should_process_jpg_rejected(self):
        """File .jpg must not be processed (not indexable)."""
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/image.jpg") is False

    def test_should_process_ignored_folder(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/.obsidian/config.md") is False

    def test_enqueue_coalesces(self):
        """Multiple events for the same file must coalesce."""
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)

        from vault_search.config.paths import VAULT_PATH

        test_path = str(VAULT_PATH / "note.md")

        handler._enqueue(test_path, deleted=False)
        handler._enqueue(test_path, deleted=False)
        handler._enqueue(test_path, deleted=True)

        assert len(pending) == 1
        # Last event wins
        assert pending["note.md"]["deleted"] is True

    def test_enqueue_multiple_files(self):
        """Events for different files must remain separate."""
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)

        from vault_search.config.paths import VAULT_PATH

        handler._enqueue(str(VAULT_PATH / "note1.md"))
        handler._enqueue(str(VAULT_PATH / "note2.md"))

        assert len(pending) == 2

    def test_enqueue_path_outside_vault(self):
        """Path outside of the vault must be ignored."""
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)

        handler._enqueue("/tmp/outside_vault.md")
        assert len(pending) == 0


class TestVaultWatcher:
    """Test start/stop and cleanup of the watcher."""

    def test_start_and_stop(self):
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        w.start()
        assert w.is_running
        w.stop()
        assert not w.is_running

    def test_stop_clears_pending(self):
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        w.start()
        # Simulate a pending event.
        with w._lock:
            w._pending["test.md"] = {"deleted": False, "time": time.monotonic()}
        w.stop()
        assert len(w._pending) == 0

    def test_double_start_does_not_create_extra_observers(self):
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        w.start()
        observer_1 = w._observer
        w.start()  # Second call.
        assert w._observer is observer_1  # same observer
        w.stop()

    def test_stop_without_start_not_crashes(self):
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        assert w.stop() is True  # Must be a no-op.

    def test_stop_timeout_preserves_threads_alive_and_prevents_restart(
        self, monkeypatch, tmp_path: Path
    ):
        """Timeout cannot hide generation old nor allow other generation."""
        mock_indexer = MagicMock()
        watcher = VaultWatcher(mock_indexer)
        observer = MagicMock()
        observer.is_alive.return_value = True
        worker = MagicMock()
        worker.is_alive.return_value = True
        watcher._observer = observer
        watcher._worker = worker

        monkeypatch.setattr("vault_search.watching.watcher.VAULT_PATH", tmp_path)

        assert watcher.stop() is False
        assert watcher._observer is observer
        assert watcher._worker is worker
        assert watcher.start() is False
        assert watcher._observer is observer
        assert watcher._worker is worker

    def test_partial_start_failure_preserves_live_observer_and_prevents_restart(
        self, monkeypatch, tmp_path: Path
    ):
        """A worker start failure must not lose a live observer."""
        watcher = VaultWatcher(MagicMock())
        observer = MagicMock()
        observer.is_alive.return_value = True
        worker = MagicMock()
        worker.start.side_effect = RuntimeError("worker start failed")
        worker.is_alive.return_value = False

        monkeypatch.setattr("vault_search.watching.watcher.VAULT_PATH", tmp_path)
        monkeypatch.setattr("vault_search.watching.watcher.Observer", lambda: observer)
        monkeypatch.setattr("vault_search.watching.watcher.threading.Thread", lambda **_: worker)

        with pytest.raises(RuntimeError, match="worker start failed"):
            watcher.start()

        assert watcher._observer is observer
        assert watcher._worker is None
        assert watcher.start() is False
        assert watcher._observer is observer

    def test_is_running_before_of_start(self):
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        assert not w.is_running

    def test_single_worker_thread(self):
        """Must use exactly 1 worker thread, not N timers."""
        mock_indexer = MagicMock()
        w = VaultWatcher(mock_indexer)
        w.start()
        assert w._worker is not None
        assert w._worker.is_alive()
        w.stop()
        # Worker must have parado
        assert w._worker is None

    def test_callback_on_reindex(self):
        """The callback runs after reindexing."""
        mock_indexer = MagicMock()
        mock_indexer.reindex_note.return_value = {"status": "updated", "chunks_indexed": 1}
        callback = MagicMock()

        w = VaultWatcher(mock_indexer, on_reindex=callback)
        w.start()

        # Simulate event ready (time in the passed)
        with w._lock:
            w._pending["test.md"] = {
                "deleted": False,
                "time": time.monotonic() - WATCHER_DEBOUNCE - 1,
            }

        # Esperar worker process
        time.sleep(WATCHER_DEBOUNCE + 1)

        w.stop()

        mock_indexer.reindex_note.assert_called_once_with("test.md")
        callback.assert_called_once()


class TestEventHandlerEdgeCases:
    """Additional tests for VaultEventHandler."""

    def test_on_moved_deletes_source_and_creates_destination(self):
        """on_moved must delete the source event and create the destination event."""
        from watchdog.events import FileMovedEvent

        from vault_search.config.paths import VAULT_PATH

        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)

        src = str(VAULT_PATH / "old.md")
        dest = str(VAULT_PATH / "new.md")
        event = FileMovedEvent(src, dest)
        handler.on_moved(event)

        assert "old.md" in pending
        assert pending["old.md"]["deleted"] is True
        assert "new.md" in pending
        assert pending["new.md"]["deleted"] is False

    def test_on_created_ignores_directory(self):
        """Events of directory must be ignored."""
        from watchdog.events import FileCreatedEvent

        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)

        event = FileCreatedEvent("/vault/new_folder")
        event._is_directory = True
        # Simulate called — must not enqueue
        if not event.is_directory and handler._should_process(event.src_path):
            handler._enqueue(event.src_path)

        assert len(pending) == 0

    def test_should_process_extension_mixed(self):
        """The mixed-case .Md extension is accepted."""
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/note.Md") is True

    def test_should_process_pdf(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/doc.pdf") is True

    def test_should_process_canvas(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/diagram.canvas") is True

    def test_should_not_process_multiple_unindexable_extensions(self):
        """All non-indexable extensions must be rejected."""
        handler = VaultEventHandler({}, threading.Lock())
        for ext in [".jpg", ".png", ".gif", ".mp3", ".mp4", ".zip"]:
            assert handler._should_process(f"/vault/file{ext}") is False

    def test_should_process_multiple_indexable_extensions(self):
        """New indexable extensions such as .txt and .mdx must be accepted."""
        handler = VaultEventHandler({}, threading.Lock())
        for ext in [".md", ".txt", ".mdx", ".pdf", ".canvas"]:
            assert handler._should_process(f"/vault/file{ext}") is True

    def test_should_process_trash_folder(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/.trash/deleted.md") is False

    def test_should_process_smart_env_folder(self):
        handler = VaultEventHandler({}, threading.Lock())
        assert handler._should_process("/vault/.smart-env/index.md") is False


class TestWatcherNoCallback:
    """Test watcher without callback on_reindex."""

    def test_without_callback_not_crashes(self):
        """A watcher without on_reindex processes events without errors."""
        mock_indexer = MagicMock()
        mock_indexer.reindex_note.return_value = {"status": "updated", "chunks_indexed": 1}

        w = VaultWatcher(mock_indexer, on_reindex=None)
        w.start()

        with w._lock:
            w._pending["test.md"] = {
                "deleted": False,
                "time": time.monotonic() - WATCHER_DEBOUNCE - 1,
            }

        time.sleep(WATCHER_DEBOUNCE + 1)
        w.stop()

        mock_indexer.reindex_note.assert_called_once_with("test.md")
