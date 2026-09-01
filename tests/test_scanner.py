"""
Unit tests for scanner.py vault scanning.

Fast tests that do not require ML models or LanceDB.
"""

from vault_search.core.scanner import scan_vault


class TestScanVault:
    def test_finds_notes_md(self, tmp_vault):
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "simple.md" in names
        assert "with_meta.md" in names
        assert "project1.md" in names

    def test_finds_canvas(self, tmp_vault):
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "diagram.canvas" in names

    def test_finds_pdf(self, tmp_vault):
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "document.pdf" in names

    def test_finds_txt(self, tmp_vault):
        """File .txt must be indexable."""
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "readme.txt" in names

    def test_ignores_non_indexable(self, tmp_vault):
        """Files not indexable (.jpg, .png, etc) must be ignored."""
        (tmp_vault / "image.jpg").write_bytes(b"fake image")
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "image.jpg" not in names

    def test_ignores_ignored_folders(self, tmp_vault):
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "config.md" not in names  # inside of .obsidian

    def test_extension_case_insensitive(self, tmp_vault):
        """A note with an uppercase .MD extension is found."""
        (tmp_vault / "upper.MD").write_text("# Upper", encoding="utf-8")
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "upper.MD" in names

    def test_ignores_frontmatter_invalid_gracefully(self, tmp_vault):
        """meta_invalid.md has YAML list — must be found by the scanner."""
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "meta_invalid.md" in names

    def test_symlink_outside_vault(self, tmp_vault, tmp_path):
        """A symlink pointing outside the vault must be ignored."""
        external = tmp_path / "external.md"
        external.write_text("# External", encoding="utf-8")
        link = tmp_vault / "external_link.md"
        link.symlink_to(external)

        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "external_link.md" not in names

    def test_symlink_inside_vault(self, tmp_vault):
        """A symlink pointing inside the vault must be included."""
        target = tmp_vault / "simple.md"
        link = tmp_vault / "internal_link.md"
        link.symlink_to(target)

        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "internal_link.md" in names

    def test_vault_empty(self, tmp_path):
        """A vault without indexable files must return an empty list."""
        vault = tmp_path / "empty_vault"
        vault.mkdir()
        (vault / "image.jpg").write_bytes(b"fake image")

        files = scan_vault(vault)
        assert files == []

    def test_broken_symlink(self, tmp_vault):
        """A symlink pointing to a nonexistent file must be ignored."""
        link = tmp_vault / "broken_link.md"
        link.symlink_to(tmp_vault / "does_not_exist.md")

        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "broken_link.md" not in names

    def test_multiple_subfolders(self, tmp_vault):
        """The scanner must find notes in nested folders recursively."""
        deep = tmp_vault / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "deep.md").write_text("# Deep", encoding="utf-8")

        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "deep.md" in names

    def test_ignores_multiple_folders(self, tmp_vault):
        """All ignored folders must be excluded."""
        for folder in [".smart-env", ".trash"]:
            d = tmp_vault / folder
            d.mkdir(exist_ok=True)
            (d / "note.md").write_text("# Ignore", encoding="utf-8")

        files = scan_vault(tmp_vault)
        paths = [str(f) for f in files]
        for folder in [".smart-env", ".trash"]:
            assert not any(folder in p for p in paths)
