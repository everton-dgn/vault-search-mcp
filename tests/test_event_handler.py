"""
Tests for the handler of events of the system of files.

Test filtering, coalescing and ignore_next_change.
"""

import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from vault_search.watching.event_handler import (
    MAX_IGNORE_TOKENS,
    VaultEventHandler,
    _check_and_clear_ignore,
    _ignore_lock,
    _ignore_next_change,
    ignore_next_change,
)


class TestIgnoreNextChange:
    """Tests for functions of ignore_next_change."""

    def setup_method(self):
        """Clears the set global before of each test."""
        with _ignore_lock:
            _ignore_next_change.clear()

    def test_ignore_adds_path(self, tmp_path):
        """ignore_next_change() registers a revision current of the file."""
        note = tmp_path / "folder" / "note.md"
        note.parent.mkdir()
        note.write_text("original")

        with patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path):
            assert ignore_next_change("folder/note.md") is True

        with _ignore_lock:
            assert "folder/note.md" in _ignore_next_change

    def test_check_and_clear_returns_true_for_same_revision(self, tmp_path):
        """The event own is ignored when a revision still is identical."""
        note = tmp_path / "folder" / "note.md"
        note.parent.mkdir()
        note.write_text("original")

        with patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("folder/note.md")
            result = _check_and_clear_ignore("folder/note.md", note)

        assert result is True
        with _ignore_lock:
            assert "folder/note.md" not in _ignore_next_change

    def test_check_and_clear_returns_false_if_absent(self):
        """_check_and_clear_ignore() returns False when the path was not marked."""
        result = _check_and_clear_ignore("folder/other.md")

        assert result is False

    def test_ignore_is_thread_safe(self, tmp_path):
        """Operations of ignore are thread-safe."""
        errors = []
        paths_added = []

        def add_and_check(thread_id):
            try:
                path = f"path_{thread_id}.md"
                note = tmp_path / path
                note.write_text(path)
                ignore_next_change(path)
                paths_added.append(path)
                time.sleep(0.001)  # Small delay to increase the chance of a race.
                _check_and_clear_ignore(path, note)
            except Exception as e:
                errors.append(e)

        with patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path):
            threads = [threading.Thread(target=add_and_check, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(errors) == 0
        with _ignore_lock:
            # All entries must have been cleared.
            assert len(_ignore_next_change) == 0

    def test_later_revision_is_not_ignored(self, tmp_path):
        """A edit later of the user invalidates the token own."""
        note = tmp_path / "note.md"
        note.write_text("revision own")

        with patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("note.md")
            note.write_text("later edit with a different size")
            assert _check_and_clear_ignore("note.md", note) is False

        with _ignore_lock:
            assert "note.md" not in _ignore_next_change

    def test_early_own_event_does_not_discard_later_edit(self, tmp_path):
        """A race event, token, edit keeps a edit human in the queue."""
        note = tmp_path / "note.md"
        note.write_text("revision own")
        pending = {}
        handler = VaultEventHandler(pending, threading.Lock())

        with patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path):
            # The watcher receives a writing own before of the token be published.
            handler._enqueue(str(note))
            pending.clear()

            ignore_next_change("note.md")
            note.write_text("edit later of the user, with other revision")
            handler._enqueue(str(note))

        assert pending["note.md"]["deleted"] is False
        with _ignore_lock:
            assert "note.md" not in _ignore_next_change

    def test_tokens_expire_and_sao_bounded(self, tmp_path):
        """The table purges expired entries and never exceeds its configured limit."""
        with (
            patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path),
            patch("vault_search.watching.event_handler.MAX_IGNORE_TOKENS", 2),
            patch(
                "vault_search.watching.event_handler.time.monotonic",
                side_effect=[0, 1, 2, 99],
            ),
        ):
            for index in range(3):
                path = f"{index}.md"
                (tmp_path / path).write_text(path)
                ignore_next_change(path)

            with _ignore_lock:
                assert len(_ignore_next_change) == 2
                assert "0.md" not in _ignore_next_change

            assert _check_and_clear_ignore("missing.md", tmp_path / "missing.md") is False

        with _ignore_lock:
            assert len(_ignore_next_change) == 0
        assert MAX_IGNORE_TOKENS >= 1


class TestVaultEventHandlerInit:
    """Tests for initialization of the VaultEventHandler."""

    def test_init_stores_references(self):
        """Initialization stores references to the pending and lock."""
        pending = {}
        lock = threading.Lock()

        handler = VaultEventHandler(pending, lock)

        assert handler._pending is pending
        assert handler._lock is lock


class TestVaultEventHandlerShouldProcess:
    """Tests for _should_process()."""

    @pytest.fixture
    def handler(self):
        return VaultEventHandler({}, threading.Lock())

    def test_markdown_file(self, handler):
        """File .md must be processed."""
        assert handler._should_process("/vault/note.md") is True

    def test_pdf_file(self, handler):
        """File .pdf must be processed."""
        assert handler._should_process("/vault/doc.pdf") is True

    def test_canvas_file(self, handler):
        """File .canvas must be processed."""
        assert handler._should_process("/vault/map.canvas") is True

    def test_txt_file(self, handler):
        """A .txt file must be processed because it is in INDEXABLE_EXTENSIONS."""
        assert handler._should_process("/vault/readme.txt") is True

    def test_image_file(self, handler):
        """File of image NOT must be processed."""
        assert handler._should_process("/vault/image.png") is False

    def test_hidden_extension(self, handler):
        """A hidden extension must not be processed."""
        assert handler._should_process("/vault/.hidden.md") is True  # Extension is .md
        assert handler._should_process("/vault/.config") is False

    def test_case_insensitive_extension(self, handler):
        """Extension case-insensitive."""
        assert handler._should_process("/vault/note.MD") is True
        assert handler._should_process("/vault/doc.PDF") is True

    def test_trash_folder(self, handler):
        """The .trash folder must be ignored."""
        assert handler._should_process("/vault/.trash/note.md") is False

    def test_obsidian_folder(self, handler):
        """The .obsidian folder must be ignored."""
        assert handler._should_process("/vault/.obsidian/plugins.md") is False

    def test_smart_env_folder(self, handler):
        """The .smart-env folder must be ignored."""
        assert handler._should_process("/vault/.smart-env/config.md") is False

    def test_nested_ignored_folder(self, handler):
        """A nested ignored folder must be ignored."""
        assert handler._should_process("/vault/project/.obsidian/plugins/note.md") is False

    def test_valid_nested_path(self, handler):
        """A valid nested folder must be processed."""
        assert handler._should_process("/vault/projects/docs/note.md") is True


class TestVaultEventHandlerEnqueue:
    """Tests for _enqueue()."""

    def setup_method(self):
        """Clears ignore set before of each test."""
        with _ignore_lock:
            _ignore_next_change.clear()

    @pytest.fixture
    def handler_with_pending(self):
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        return handler, pending

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_enqueue_adds_to_pending(self, handler_with_pending):
        """_enqueue() adds event to the pending."""
        handler, pending = handler_with_pending

        handler._enqueue("/vault/folder/note.md")

        assert "folder/note.md" in pending
        assert pending["folder/note.md"]["deleted"] is False

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_enqueue_deleted_flag(self, handler_with_pending):
        """_enqueue() with deleted=True marks as deleted."""
        handler, pending = handler_with_pending

        handler._enqueue("/vault/note.md", deleted=True)

        assert pending["note.md"]["deleted"] is True

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_enqueue_coalescence(self, handler_with_pending):
        """_enqueue() coalescing - last wins."""
        handler, pending = handler_with_pending

        handler._enqueue("/vault/note.md", deleted=False)
        time1 = pending["note.md"]["time"]

        time.sleep(0.01)
        handler._enqueue("/vault/note.md", deleted=True)
        time2 = pending["note.md"]["time"]

        # The last event overwrites the previous one.
        assert pending["note.md"]["deleted"] is True
        assert time2 > time1

    def test_enqueue_ignores_marked_path(self, handler_with_pending, tmp_path):
        """_enqueue() ignores a path marked by ignore_next_change."""
        handler, pending = handler_with_pending
        note = tmp_path / "note.md"
        note.write_text("revision own")

        with patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("note.md")
            handler._enqueue(str(note))

        assert "note.md" not in pending

    def test_enqueue_clears_ignore_after_use(self, handler_with_pending, tmp_path):
        """_enqueue() clears ignore after use."""
        handler, pending = handler_with_pending
        note = tmp_path / "note.md"
        note.write_text("revision own")

        with patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("note.md")
            handler._enqueue(str(note))  # Ignores the event and clears the marker.

            # Second time must be processed because the token was consumed.
            handler._enqueue(str(note))
        assert "note.md" in pending

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/other"))
    def test_enqueue_outside_vault(self, handler_with_pending):
        """_enqueue() ignores files outside the vault."""
        handler, pending = handler_with_pending

        handler._enqueue("/vault/note.md")  # VAULT_PATH is /other

        assert len(pending) == 0

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_enqueue_records_time(self, handler_with_pending):
        """_enqueue() registers timestamp monotonic."""
        handler, pending = handler_with_pending

        before = time.monotonic()
        handler._enqueue("/vault/note.md")
        after = time.monotonic()

        assert before <= pending["note.md"]["time"] <= after

    def test_enqueue_resolves_symlink_root(self, tmp_path):
        """_enqueue() accepts an event at the real path when VAULT_PATH is a symlink."""
        real_vault = tmp_path / "real_vault"
        real_vault.mkdir()
        symlink_vault = tmp_path / "vault_link"
        symlink_vault.symlink_to(real_vault, target_is_directory=True)

        pending = {}
        lock = threading.Lock()

        with patch("vault_search.watching.event_handler.VAULT_PATH", symlink_vault):
            handler = VaultEventHandler(pending, lock)
            handler._enqueue(str(real_vault / "notes" / "test.md"))

        assert "notes/test.md" in pending


class TestVaultEventHandlerOnCreated:
    """Tests for on_created()."""

    @pytest.fixture
    def handler_with_pending(self):
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        return handler, pending

    def setup_method(self):
        with _ignore_lock:
            _ignore_next_change.clear()

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_created_file(self, handler_with_pending):
        """on_created() processes file valid."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/new.md"

        handler.on_created(event)

        assert "new.md" in pending
        assert pending["new.md"]["deleted"] is False

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_created_directory(self, handler_with_pending):
        """on_created() ignores directories."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = True
        event.src_path = "/vault/folder"

        handler.on_created(event)

        assert len(pending) == 0

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_created_invalid_extension(self, handler_with_pending):
        """on_created() ignores an invalid extension."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/image.png"

        handler.on_created(event)

        assert len(pending) == 0


class TestVaultEventHandlerOnModified:
    """Tests for on_modified()."""

    @pytest.fixture
    def handler_with_pending(self):
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        return handler, pending

    def setup_method(self):
        with _ignore_lock:
            _ignore_next_change.clear()

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_modified_file(self, handler_with_pending):
        """on_modified() processes file valid."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/note.md"

        handler.on_modified(event)

        assert "note.md" in pending
        assert pending["note.md"]["deleted"] is False

    def test_on_modified_with_ignore(self, handler_with_pending, tmp_path):
        """on_modified() respects ignore_next_change."""
        handler, pending = handler_with_pending
        note = tmp_path / "note.md"
        note.write_text("revision own")

        event = Mock()
        event.is_directory = False
        event.src_path = str(note)

        with patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("note.md")
            handler.on_modified(event)

        assert "note.md" not in pending


class TestVaultEventHandlerOnDeleted:
    """Tests for on_deleted()."""

    @pytest.fixture
    def handler_with_pending(self):
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        return handler, pending

    def setup_method(self):
        with _ignore_lock:
            _ignore_next_change.clear()

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_deleted_file(self, handler_with_pending):
        """on_deleted() processes file valid with deleted=True."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/deleted.md"

        handler.on_deleted(event)

        assert "deleted.md" in pending
        assert pending["deleted.md"]["deleted"] is True


class TestVaultEventHandlerOnMoved:
    """Tests for on_moved()."""

    @pytest.fixture
    def handler_with_pending(self):
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        return handler, pending

    def setup_method(self):
        with _ignore_lock:
            _ignore_next_change.clear()

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_moved_both_valid(self, handler_with_pending):
        """on_moved() processes src as deleted and dest as created."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/old.md"
        event.dest_path = "/vault/new.md"

        handler.on_moved(event)

        assert "old.md" in pending
        assert pending["old.md"]["deleted"] is True
        assert "new.md" in pending
        assert pending["new.md"]["deleted"] is False

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_moved_src_invalid(self, handler_with_pending):
        """on_moved() ignores an invalid source."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/image.png"
        event.dest_path = "/vault/new.md"

        handler.on_moved(event)

        assert "image.png" not in pending
        assert "new.md" in pending

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_moved_dest_invalid(self, handler_with_pending):
        """on_moved() ignores an invalid destination extension."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/note.md"
        event.dest_path = "/vault/file.json"  # .json is not indexable

        handler.on_moved(event)

        assert "note.md" in pending
        assert pending["note.md"]["deleted"] is True
        assert "file.json" not in pending

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_moved_to_trash(self, handler_with_pending):
        """on_moved() for .trash processes src as deleted."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = False
        event.src_path = "/vault/note.md"
        event.dest_path = "/vault/.trash/note.md"

        handler.on_moved(event)

        assert "note.md" in pending
        assert pending["note.md"]["deleted"] is True
        assert ".trash/note.md" not in pending

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_on_moved_directory(self, handler_with_pending):
        """on_moved() ignores directories."""
        handler, pending = handler_with_pending

        event = Mock()
        event.is_directory = True
        event.src_path = "/vault/folder"
        event.dest_path = "/vault/other"

        handler.on_moved(event)

        assert len(pending) == 0


class TestVaultEventHandlerConcurrency:
    """Tests for concurrency of the handler."""

    def setup_method(self):
        with _ignore_lock:
            _ignore_next_change.clear()

    @patch("vault_search.watching.event_handler.VAULT_PATH", Path("/vault"))
    def test_concurrent_events(self):
        """Handler is thread-safe for events concurrent."""
        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)
        errors = []

        def emit_events(thread_id):
            try:
                for i in range(10):
                    event = Mock()
                    event.is_directory = False
                    event.src_path = f"/vault/note_{thread_id}_{i}.md"
                    handler.on_modified(event)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=emit_events, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Five threads times ten events produce 50 entries.
        assert len(pending) == 50
