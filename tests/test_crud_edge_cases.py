"""
Tests for edge cases critical of CRUD.

Covers test gaps identified during code review.
"""

import pytest

# === Read Operations ===


class TestReadEdgeCases:
    """Tests for edge cases in operations of reading."""

    def test_read_note_frontmatter_title_as_int(self, tmp_path, monkeypatch):
        """read_note converts title int for string."""
        # Patch in the module that USES a variable, not where it is defined
        monkeypatch.setattr("vault_search.crud.read.VAULT_PATH", tmp_path)
        monkeypatch.setattr("vault_search.crud.validation.VAULT_PATH", tmp_path)

        note = tmp_path / "test.md"
        note.write_text("---\ntitle: 123\n---\nBody content")

        from vault_search.crud.read import read_note

        result = read_note("test.md")

        assert result["title"] == "123"  # int converted

    def test_read_note_frontmatter_title_as_bool(self, tmp_path, monkeypatch):
        """read_note converts title bool for string."""
        monkeypatch.setattr("vault_search.crud.read.VAULT_PATH", tmp_path)
        monkeypatch.setattr("vault_search.crud.validation.VAULT_PATH", tmp_path)

        note = tmp_path / "test.md"
        note.write_text("---\ntitle: true\n---\nBody content")

        from vault_search.crud.read import read_note

        result = read_note("test.md")

        # Boolean True becomes a string representation.
        assert isinstance(result["title"], str)

    def test_read_note_frontmatter_title_as_list(self, tmp_path, monkeypatch):
        """read_note converts title list for string."""
        monkeypatch.setattr("vault_search.crud.read.VAULT_PATH", tmp_path)
        monkeypatch.setattr("vault_search.crud.validation.VAULT_PATH", tmp_path)

        note = tmp_path / "test.md"
        note.write_text("---\ntitle:\n  - First\n  - Second\n---\nBody content")

        from vault_search.crud.read import read_note

        result = read_note("test.md")

        # A list must become a string using its first element or repr.
        assert isinstance(result["title"], str)


# === Write Operations ===


class TestWriteEdgeCases:
    """Tests for edge cases in operations of writing."""

    def test_create_note_rejects_invalid_frontmatter_type(self, tmp_path, monkeypatch):
        """create_note rejects frontmatter that is not a dictionary."""
        from vault_search.config import paths

        monkeypatch.setattr(paths, "VAULT_PATH", tmp_path)

        from vault_search.crud.write import create_note

        with pytest.raises(ValueError):
            create_note("test1.md", "body", frontmatter="not a dict")

    def test_update_frontmatter_rejects_invalid_metadata(self, tmp_path, monkeypatch):
        """update_frontmatter rejects metadata that is not dict."""
        from vault_search.config import paths

        monkeypatch.setattr(paths, "VAULT_PATH", tmp_path)

        # Create note first
        note = tmp_path / "test.md"
        note.write_text("---\ntitle: Test\n---\nBody")

        from vault_search.crud.write import update_frontmatter

        # String metadata must raise ValueError.
        with pytest.raises(ValueError, match="dictionary"):
            update_frontmatter("test.md", "not a dict")

    def test_append_note_basic(self, tmp_path, monkeypatch):
        """append_note adds content correctly."""
        # Needs monkeypatch in the module validation where VAULT_PATH is used
        from vault_search.crud import validation

        monkeypatch.setattr(validation, "VAULT_PATH", tmp_path)

        # Create note
        note = tmp_path / "test.md"
        note.write_text("Initial content")

        from vault_search.crud.write import append_note

        # Add content.
        result = append_note("test.md", "New content")

        assert result.get("success") is True
        assert "New content" in note.read_text()


# === Delete Operations ===


class TestDeleteEdgeCases:
    """Tests for edge cases in operations of delete."""

    def test_move_note_rejects_invalid_extension(self, tmp_path, monkeypatch):
        """move_note rejects an invalid destination extension."""
        from vault_search.config import paths

        monkeypatch.setattr(paths, "VAULT_PATH", tmp_path)

        # Create file
        note = tmp_path / "file.md"
        note.write_text("content")

        from vault_search.crud.delete import move_note

        # Attempt to move to an unsupported extension.
        with pytest.raises(ValueError, match="not supported"):
            move_note("file.md", "file.jpg")


# === List Operations ===


class TestListEdgeCases:
    """Tests for edge cases in list operations."""

    def test_list_notes_handles_ignored_folders(self, tmp_path, monkeypatch):
        """list_notes handles ignored folders correctly."""
        monkeypatch.setattr("vault_search.crud.read.VAULT_PATH", tmp_path)
        monkeypatch.setattr("vault_search.crud.validation.VAULT_PATH", tmp_path)
        monkeypatch.setattr("vault_search.crud.read.USE_CATALOG", False)

        # Create a structure with an ignored folder.
        trash = tmp_path / ".trash"
        trash.mkdir()
        (trash / "deleted.md").write_text("deleted content")

        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "valid.md").write_text("valid content")

        from vault_search.crud.read import list_notes

        # Listing all notes must exclude .trash.
        result = list_notes()
        paths_found = [n.get("path", "") for n in result.get("notes", [])]

        assert not any(".trash" in p for p in paths_found)
