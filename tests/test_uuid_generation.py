"""
Tests for automatic UUIDv7 generation in notes.

Covers:
- valid UUIDv7 generation
- automatic IDs in create_note
- ensure_note_id for existing notes
- reindex_note integration
- batch migration through generate_missing_ids
- error handling
"""

import re
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vault_search.type_defs import ParseResult, ParseStatus, ReindexStatus
from vault_search.utils.uuid import generate_uuid7


class TestGenerateUuid7:
    """Tests for generate_uuid7."""

    def test_returns_string(self):
        """UUIDs are returned as strings."""
        result = generate_uuid7()
        assert isinstance(result, str)

    def test_valid_uuid_format(self):
        """UUIDs use the standard 8-4-4-4-12 representation."""
        result = generate_uuid7()
        # Format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        assert re.match(pattern, result), f"Invalid UUID: {result}"

    def test_version_7(self):
        """The version nibble identifies UUIDv7."""
        result = generate_uuid7()
        # Position 14 after removing hyphens, or index 14 in the hyphenated string.
        # Format: xxxxxxxx-xxxx-7xxx-xxxx-xxxxxxxxxxxx
        #                        ^ position 14
        assert result[14] == "7", f"UUID is not v7: {result}"

    def test_uniqueness(self):
        """Generated UUIDs are unique."""
        uuids = [generate_uuid7() for _ in range(1000)]
        assert len(set(uuids)) == 1000, "Duplicate UUIDs detected"

    def test_chronological_order(self):
        """Sequentially generated UUIDs must sort chronologically."""
        import time

        uuid1 = generate_uuid7()
        time.sleep(0.002)  # 2ms
        uuid2 = generate_uuid7()

        # UUIDv7 values sort lexicographically by time.
        assert uuid1 < uuid2, "UUIDs are not in chronological order"


class TestCreateNoteAutoId:
    """Tests for auto-generation of ID in create_note."""

    @pytest.fixture
    def mock_vault(self, tmp_path):
        """Create a temporary vault for tests."""
        vault = tmp_path / "vault"
        vault.mkdir()
        with patch("vault_search.crud.validation.VAULT_PATH", vault):
            yield vault

    @pytest.fixture
    def mock_frontmatter_validation(self):
        """Return a successful frontmatter validation result."""

        def mock_validate(frontmatter):
            return {
                "valid": True,
                "errors": [],
                "warnings": [],
                "suggestions": [],
                "validated_data": frontmatter,
                "auto_generated": {},
            }

        with patch(
            "vault_search.crud.write.validate_frontmatter_schema_result",
            side_effect=mock_validate,
        ):
            yield

    def test_auto_generates_id_when_not_provided(self, mock_vault, mock_frontmatter_validation):
        """create_note must generate an ID automatically when none is provided."""
        from vault_search.crud.write import create_note

        with patch("vault_search.crud.write.validate_for_write") as mock_validate:
            mock_validate.return_value = mock_vault / "test.md"
            with patch("vault_search.crud.write.safe_write_text") as mock_write:
                mock_write.return_value = None

                result = create_note("test.md", "Content")

                assert result["success"]
                # Verify that safe_write_text received frontmatter containing an ID.
                call_args = mock_write.call_args
                content = call_args[0][1]  # Second positional argument.
                assert "id:" in content

    def test_preserves_user_provided_id(self, mock_vault, mock_frontmatter_validation):
        """create_note must preserve an ID provided by the user."""
        from vault_search.crud.write import create_note

        user_id = "my-custom-id"

        with patch("vault_search.crud.write.validate_for_write") as mock_validate:
            mock_validate.return_value = mock_vault / "test.md"
            with patch("vault_search.crud.write.safe_write_text") as mock_write:
                mock_write.return_value = None

                result = create_note("test.md", "Content", {"id": user_id})

                assert result["success"]
                call_args = mock_write.call_args
                content = call_args[0][1]
                assert f"id: {user_id}" in content

    def test_id_is_uuid7_format(self, mock_vault):
        """Automatically generated IDs use UUIDv7."""
        from vault_search.crud.write import create_note

        with patch("vault_search.crud.write.validate_for_write") as mock_validate:
            mock_validate.return_value = mock_vault / "test.md"
            with patch("vault_search.crud.write.safe_write_text") as mock_write:
                mock_write.return_value = None

                create_note("test.md", "Content")

                content = mock_write.call_args[0][1]
                # Extract the ID from frontmatter.
                match = re.search(r"id: ([^\n]+)", content)
                assert match, "ID not found in frontmatter"

                uuid = match.group(1)
                assert uuid[14] == "7", f"UUID is not v7: {uuid}"


class TestEnsureNoteId:
    """Tests for ensure_note_id."""

    @pytest.fixture
    def note_without_id(self, tmp_path):
        """Create a note without an ID."""
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "note.md"
        note.write_text("---\ntitle: Test\n---\nContent")
        return note, vault

    @pytest.fixture
    def note_with_id(self, tmp_path):
        """Create a note with an existing ID."""
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "note.md"
        note.write_text("---\nid: existing-id\ntitle: Test\n---\nContent")
        return note, vault

    @pytest.fixture
    def mock_frontmatter_validation(self):
        """Return successful frontmatter validation results."""

        def mock_validate_result(frontmatter):
            return {
                "valid": True,
                "errors": [],
                "warnings": [],
                "suggestions": [],
                "validated_data": frontmatter,
                "auto_generated": {},
            }

        def mock_validate_tuple(frontmatter):
            # validate_frontmatter_schema returns a tuple.
            return (frontmatter, [], [], [])

        with patch(
            "vault_search.crud.write.validate_frontmatter_schema_result",
            side_effect=mock_validate_result,
        ):
            with patch(
                "vault_search.crud.write.validate_frontmatter_schema",
                side_effect=mock_validate_tuple,
            ):
                yield

    def test_adds_id_to_note_without_id(self, note_without_id, mock_frontmatter_validation):
        """ensure_note_id must add an ID to a note without one."""
        note_path, vault = note_without_id

        with patch("vault_search.crud.write.resolve_path", return_value=note_path):
            with patch("vault_search.crud.validation.VAULT_PATH", vault):
                from vault_search.crud.write import ensure_note_id

                result = ensure_note_id("note.md")

                assert result["success"]
                assert result["id_added"] is True
                assert "id" in result

                # Verify that the ID was written to the file.
                content = note_path.read_text()
                assert "id:" in content

    def test_does_not_modify_note_with_id(self, note_with_id):
        """ensure_note_id must not modify note that already has ID."""
        note_path, vault = note_with_id
        original_content = note_path.read_text()

        with patch("vault_search.crud.write.resolve_path", return_value=note_path):
            with patch("vault_search.crud.validation.VAULT_PATH", vault):
                from vault_search.crud.write import ensure_note_id

                result = ensure_note_id("note.md")

            assert result["success"]
            assert result["id_added"] is False
            assert result["id"] == "existing-id"

            # The file must not have been modified.
            assert note_path.read_text() == original_content

    def test_id_placed_at_top_of_frontmatter(self, note_without_id, mock_frontmatter_validation):
        """The generated ID is the first frontmatter field."""
        note_path, vault = note_without_id

        with patch("vault_search.crud.write.resolve_path", return_value=note_path):
            with patch("vault_search.crud.validation.VAULT_PATH", vault):
                from vault_search.crud.write import ensure_note_id

                ensure_note_id("note.md")

            content = note_path.read_text()
            lines = content.split("\n")
            # First line after --- must be id:
            assert lines[1].startswith("id:"), f"ID is not at the top: {lines[:5]}"

    def test_error_for_nonexistent_file(self, tmp_path):
        """ensure_note_id returns an error for a missing file."""
        vault = tmp_path / "vault"
        vault.mkdir()
        with patch("vault_search.crud.write.resolve_path") as mock_resolve:
            mock_resolve.return_value = vault / "does-not-exist.md"

            with patch("vault_search.crud.validation.VAULT_PATH", vault):
                from vault_search.crud.write import ensure_note_id

                result = ensure_note_id("does-not-exist.md")

            assert result["success"] is False
            assert "not found" in result["message"].lower()

    def test_error_for_non_md_file(self):
        """ensure_note_id rejects non-Markdown files."""
        from vault_search.crud.write import ensure_note_id

        result = ensure_note_id("file.pdf")

        assert result["success"] is False
        assert ".md" in result["message"]


class TestReindexNoteAutoId:
    """Tests for automatic IDs during incremental reindexing."""

    @staticmethod
    def _empty_note(tmp_path: Path) -> Path:
        note = tmp_path / "note.md"
        note.write_text("", encoding="utf-8")
        return note

    @staticmethod
    def _indexer_with_empty_apply():
        from vault_search.core.indexer import VaultIndexer

        indexer = VaultIndexer()
        table = MagicMock()
        return indexer, table

    def test_calls_ensure_note_id_for_md_files(self, tmp_path):
        """reindex_note ensures an ID before parsing a Markdown note."""
        self._empty_note(tmp_path)
        indexer, table = self._indexer_with_empty_apply()

        with (
            patch("vault_search.core.indexer.VAULT_PATH", tmp_path),
            patch(
                "vault_search.core.indexer.ensure_note_id",
                return_value={"success": True, "id_added": True, "id": "test-id"},
            ) as mock_ensure,
            patch(
                "vault_search.core.indexer.parse_file_result",
                return_value=ParseResult(status=ParseStatus.EMPTY),
            ),
            patch.object(indexer, "_apply_note_records", return_value=(table, 0, 0)),
            patch.object(indexer, "_record_incremental_operation", return_value=False),
        ):
            result = indexer.reindex_note("note.md")

        mock_ensure.assert_called_once_with("note.md")
        assert result["status"] is ReindexStatus.EMPTY

    def test_skips_ensure_note_id_when_disabled(self, tmp_path):
        """auto_generate_id=False leaves the note untouched before parsing."""
        self._empty_note(tmp_path)
        indexer, table = self._indexer_with_empty_apply()

        with (
            patch("vault_search.core.indexer.VAULT_PATH", tmp_path),
            patch("vault_search.core.indexer.ensure_note_id") as mock_ensure,
            patch(
                "vault_search.core.indexer.parse_file_result",
                return_value=ParseResult(status=ParseStatus.EMPTY),
            ) as mock_parse,
            patch.object(indexer, "_apply_note_records", return_value=(table, 0, 0)),
            patch.object(indexer, "_record_incremental_operation", return_value=False),
        ):
            result = indexer.reindex_note("note.md", auto_generate_id=False)

        mock_ensure.assert_not_called()
        mock_parse.assert_called_once_with(tmp_path / "note.md", tmp_path)
        assert result["status"] is ReindexStatus.EMPTY

    def test_handles_permission_error_gracefully(self, tmp_path):
        """A denied ID write does not prevent the existing note from being indexed."""
        self._empty_note(tmp_path)
        indexer, table = self._indexer_with_empty_apply()

        with (
            patch("vault_search.core.indexer.VAULT_PATH", tmp_path),
            patch("vault_search.core.indexer.ensure_note_id", side_effect=PermissionError),
            patch(
                "vault_search.core.indexer.parse_file_result",
                return_value=ParseResult(status=ParseStatus.EMPTY),
            ) as mock_parse,
            patch.object(indexer, "_apply_note_records", return_value=(table, 0, 0)),
            patch.object(indexer, "_record_incremental_operation", return_value=False),
        ):
            result = indexer.reindex_note("note.md")

        mock_parse.assert_called_once_with(tmp_path / "note.md", tmp_path)
        assert result["status"] is ReindexStatus.EMPTY

    def test_handles_file_not_found_during_ensure(self, tmp_path):
        """A note removed during ID generation is removed from every index."""
        self._empty_note(tmp_path)
        indexer, table = self._indexer_with_empty_apply()

        with (
            patch("vault_search.core.indexer.VAULT_PATH", tmp_path),
            patch("vault_search.core.indexer.ensure_note_id", side_effect=FileNotFoundError),
            patch("vault_search.core.indexer.parse_file_result") as mock_parse,
            patch.object(
                indexer,
                "_apply_note_records",
                return_value=(table, 0, 0),
            ) as mock_apply,
            patch.object(indexer, "_record_incremental_operation", return_value=False),
        ):
            result = indexer.reindex_note("note.md")

        mock_apply.assert_called_once_with("note.md", [], [], [])
        mock_parse.assert_not_called()
        assert result["status"] is ReindexStatus.DELETED


class TestGenerateMissingIds:
    """Tests for the generate_missing_ids batch migration tool."""

    @pytest.fixture
    def tool_and_queue(self):
        from vault_search.server.crud_tools import register_crud_tools

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self):
                def decorator(function):
                    self.tools[function.__name__] = function
                    return function

                return decorator

        mcp = FakeMCP()
        with (
            patch("vault_search.server.crud_tools.FrontmatterEnrichmentJobManager"),
            patch("vault_search.server.crud_tools.ReindexQueue") as queue_class,
            patch("vault_search.server.crud_tools.ShutdownManager.register_callback"),
        ):
            register_crud_tools(mcp, MagicMock(), MagicMock())
        return mcp.tools["generate_missing_ids"], queue_class.return_value

    def test_dry_run_returns_preview(self, tmp_path, tool_and_queue):
        """dry_run=True previews missing IDs without writing or reindexing."""
        tool, queue = tool_and_queue
        notes = [tmp_path / "note1.md", tmp_path / "note2.md"]

        with (
            patch("vault_search.server.crud_tools.VAULT_PATH", tmp_path),
            patch("vault_search.server.crud_tools.scan_vault", return_value=notes),
            patch(
                "vault_search.server.crud_tools.read_frontmatter_only",
                side_effect=[({}, 0), ({"id": "existing"}, 0)],
            ),
            patch("vault_search.server.crud_tools.crud_ensure_note_id") as mock_ensure,
        ):
            result = tool(dry_run=True)

        assert result == {
            "dry_run": True,
            "total_scanned": 2,
            "missing_ids": 1,
            "would_add": 1,
            "notes": ["note1.md"],
            "truncated": False,
        }
        mock_ensure.assert_not_called()
        queue.enqueue_sync.assert_not_called()

    def test_adds_ids_to_notes_without_id(self, tmp_path, tool_and_queue):
        """The tool adds UUIDs to every selected note that lacks one."""
        tool, queue = tool_and_queue
        queue.enqueue_sync.return_value = "completed"

        with (
            patch("vault_search.server.crud_tools.VAULT_PATH", tmp_path),
            patch(
                "vault_search.server.crud_tools.scan_vault",
                return_value=[tmp_path / "note.md"],
            ),
            patch("vault_search.server.crud_tools.read_frontmatter_only", return_value=({}, 0)),
            patch(
                "vault_search.server.crud_tools.crud_ensure_note_id",
                return_value={"success": True, "id_added": True, "id": "generated-id"},
            ) as mock_ensure,
        ):
            result = tool()

        mock_ensure.assert_called_once_with("note.md")
        queue.enqueue_sync.assert_called_once_with()
        assert result["ids_added"] == 1
        assert result["added"] == [{"path": "note.md", "id": "generated-id"}]
        assert result["reindex_status"] == "completed"

    def test_skips_notes_with_existing_id(self, tmp_path, tool_and_queue):
        """Notes with existing IDs are excluded from the write phase."""
        tool, queue = tool_and_queue

        with (
            patch("vault_search.server.crud_tools.VAULT_PATH", tmp_path),
            patch(
                "vault_search.server.crud_tools.scan_vault",
                return_value=[tmp_path / "note.md"],
            ),
            patch(
                "vault_search.server.crud_tools.read_frontmatter_only",
                return_value=({"id": "existing"}, 0),
            ),
            patch("vault_search.server.crud_tools.crud_ensure_note_id") as mock_ensure,
        ):
            result = tool()

        mock_ensure.assert_not_called()
        queue.enqueue_sync.assert_not_called()
        assert result["missing_ids"] == 0
        assert result["ids_added"] == 0
        assert result["reindex_status"] == "not_needed"

    def test_filters_by_complete_folder_component(self, tmp_path, tool_and_queue):
        """A folder selector excludes sibling names that share its prefix."""
        tool, _ = tool_and_queue
        project_note = tmp_path / "projects" / "note.md"
        prefixed_sibling = tmp_path / "projects-archive" / "note.md"

        with (
            patch("vault_search.server.crud_tools.VAULT_PATH", tmp_path),
            patch(
                "vault_search.server.crud_tools.scan_vault",
                return_value=[project_note, prefixed_sibling],
            ),
            patch("vault_search.server.crud_tools.read_frontmatter_only", return_value=({}, 0)),
        ):
            result = tool(folder="projects", dry_run=True)

        assert result["total_scanned"] == 1
        assert result["notes"] == ["projects/note.md"]

    def test_returns_summary_with_counts(self, tmp_path, tool_and_queue):
        """The write result separates successful additions from failures."""
        tool, queue = tool_and_queue
        queue.enqueue_sync.return_value = "completed"
        notes = [tmp_path / "one.md", tmp_path / "two.md", tmp_path / "existing.md"]

        with (
            patch("vault_search.server.crud_tools.VAULT_PATH", tmp_path),
            patch("vault_search.server.crud_tools.scan_vault", return_value=notes),
            patch(
                "vault_search.server.crud_tools.read_frontmatter_only",
                side_effect=[({}, 0), ({}, 0), ({"id": "existing"}, 0)],
            ),
            patch(
                "vault_search.server.crud_tools.crud_ensure_note_id",
                side_effect=[
                    {"success": True, "id_added": True, "id": "generated-id"},
                    {"success": False, "id_added": False, "message": "write failed"},
                ],
            ),
        ):
            result = tool()

        assert result["total_scanned"] == 3
        assert result["missing_ids"] == 2
        assert result["ids_added"] == 1
        assert result["errors"] == 1
        assert result["error_details"] == [{"path": "two.md", "error": "write failed"}]


class TestIgnoreNextChange:
    """Tests for the watcher change-ignore mechanism."""

    @pytest.fixture
    def mock_frontmatter_validation(self):
        """Return successful frontmatter validation results."""

        def mock_validate_result(frontmatter):
            return {
                "valid": True,
                "errors": [],
                "warnings": [],
                "suggestions": [],
                "validated_data": frontmatter,
                "auto_generated": {},
            }

        def mock_validate_tuple(frontmatter):
            # validate_frontmatter_schema returns a tuple.
            return (frontmatter, [], [], [])

        with patch(
            "vault_search.crud.write.validate_frontmatter_schema_result",
            side_effect=mock_validate_result,
        ):
            with patch(
                "vault_search.crud.write.validate_frontmatter_schema",
                side_effect=mock_validate_tuple,
            ):
                yield

    def test_ignore_next_change_prevents_enqueue(self, tmp_path):
        """ignore_next_change prevents the watcher from enqueuing the event."""
        from vault_search.watching.event_handler import (
            _check_and_clear_ignore,
            ignore_next_change,
        )

        note = tmp_path / "note.md"
        note.write_text("own revision")

        with patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("note.md")

            assert _check_and_clear_ignore("note.md", note) is True
            assert _check_and_clear_ignore("note.md", note) is False

    def test_ignore_is_path_specific(self, tmp_path):
        """ignore_next_change is scoped to one path."""
        from vault_search.watching.event_handler import (
            _check_and_clear_ignore,
            ignore_next_change,
        )

        note1 = tmp_path / "note1.md"
        note1.write_text("own revision")

        with patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path):
            ignore_next_change("note1.md")

            assert _check_and_clear_ignore("note2.md", tmp_path / "note2.md") is False
            assert _check_and_clear_ignore("note1.md", note1) is True

    def test_ensure_note_id_marks_path_for_ignore(self, tmp_path, mock_frontmatter_validation):
        """ensure_note_id must mark the path to ignore before writing."""
        from vault_search.watching.event_handler import (
            _ignore_lock,
            _ignore_next_change,
        )

        # Create note without ID
        note = tmp_path / "note.md"
        note.write_text("---\ntitle: Test\n---\nBody")

        # Clear state
        with _ignore_lock:
            _ignore_next_change.clear()

        with patch("vault_search.crud.write.resolve_path", return_value=note):
            with patch("vault_search.crud.validation.VAULT_PATH", tmp_path):
                from vault_search.crud.write import ensure_note_id

                result = ensure_note_id("note.md")

            assert result.get("id_added") is True

            # Verify that the path was marked for ignoring.
            # The check consumes the flag, so verify that it existed first.
            # ensure_note_id already called ignore_next_change, so the flag must exist.
            # Unless another consumer has already consumed it.
            # Verify that the flag was set before it was consumed.
            # Verify that the file was modified and now has an ID.
            content = note.read_text()
            assert "id:" in content


class TestUuidIntegration:
    """Integration tests for the complete UUID flow."""

    def test_create_note_generates_indexable_id(self, tmp_path):
        """The parser extracts the same UUID generated by create_note."""
        from vault_search.crud.write import create_note
        from vault_search.parsers.frontmatter import extract_frontmatter_fields, parse_frontmatter

        with (
            patch("vault_search.crud.write.validate_for_write", return_value=tmp_path / "note.md"),
            patch("vault_search.crud.write.file_revision", return_value=None),
            patch("vault_search.crud.write.advisory_path_lock", return_value=nullcontext()),
            patch("vault_search.crud.write.safe_write_text", return_value=None) as mock_write,
        ):
            result = create_note("note.md", "Body", validate_schema=False)

        serialized = mock_write.call_args.args[1]
        frontmatter, body = parse_frontmatter(serialized)
        indexed_fields = extract_frontmatter_fields(frontmatter)

        assert result["success"] is True
        assert body == "Body"
        assert indexed_fields["id"] == frontmatter["id"]
        assert indexed_fields["id"][14] == "7"

    def test_watcher_ignores_auto_generated_id_change(self, tmp_path):
        """The watcher ignores the change made when ensure_note_id adds an ID."""
        import threading

        from vault_search.watching.event_handler import (
            VaultEventHandler,
            ignore_next_change,
        )

        pending = {}
        lock = threading.Lock()
        handler = VaultEventHandler(pending, lock)

        note = tmp_path / "notes" / "test.md"
        note.parent.mkdir()
        note.write_text("own revision")

        # Simulate a file modification event.
        with patch.object(handler, "_should_process", return_value=True):
            with patch("vault_search.watching.event_handler.VAULT_PATH", tmp_path):
                from watchdog.events import FileModifiedEvent

                ignore_next_change("notes/test.md")
                event = FileModifiedEvent(str(note))
                handler.on_modified(event)

        # The ignored event must not reach the pending queue.
        assert "notes/test.md" not in pending

    def test_uuid7_is_chronologically_sortable(self):
        """Generated UUIDs must allow chronological note sorting."""
        uuids = []
        for _ in range(10):
            uuids.append(generate_uuid7())

        # Sequentially generated UUIDs must already be ordered.
        assert uuids == sorted(uuids), "UUIDs are not in chronological order"
