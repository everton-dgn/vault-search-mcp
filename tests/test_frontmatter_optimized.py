"""
Tests for optimized frontmatter reading.
"""

from pathlib import Path

from vault_search.parsers.frontmatter import read_frontmatter_only


class TestReadFrontmatterOnly:
    """Tests for read_frontmatter_only."""

    def test_file_with_frontmatter(self, tmp_path: Path):
        content = """---
title: Test Note
tags: [python, testing]
---

# Body content
Some text here.
"""
        path = tmp_path / "with-frontmatter.md"
        path.write_text(content)

        metadata, bytes_read = read_frontmatter_only(path)

        assert metadata["title"] == "Test Note"
        assert metadata["tags"] == ["python", "testing"]
        assert bytes_read > 0
        # Small files can be read in a single chunk.
        assert bytes_read <= len(content.encode("utf-8"))

    def test_file_without_frontmatter(self, tmp_path: Path):
        content = """# Just a heading

Normal markdown content.
"""
        path = tmp_path / "without-frontmatter.md"
        path.write_text(content)

        metadata, bytes_read = read_frontmatter_only(path)

        assert metadata == {}
        assert bytes_read > 0

    def test_file_with_bom(self, tmp_path: Path):
        content = "\ufeff---\ntitle: BOM Test\n---\nBody"
        path = tmp_path / "with-bom.md"
        path.write_text(content, encoding="utf-8")

        metadata, _ = read_frontmatter_only(path)

        assert metadata["title"] == "BOM Test"

    def test_file_empty(self, tmp_path: Path):
        path = tmp_path / "empty.md"
        path.write_text("")

        metadata, bytes_read = read_frontmatter_only(path)

        assert metadata == {}
        assert bytes_read == 0

    def test_frontmatter_without_closing(self, tmp_path: Path):
        """Open frontmatter without a closing delimiter."""
        content = """---
title: Incomplete
tags: [test]

Body without closing delimiter.
"""
        path = tmp_path / "unclosed-frontmatter.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        assert metadata == {}  # Is not frontmatter valid

    def test_frontmatter_yaml_invalid(self, tmp_path: Path):
        content = """---
title: [Invalid YAML
missing: bracket
---

Body
"""
        path = tmp_path / "invalid-yaml.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        assert metadata == {}  # YAML invalid

    def test_frontmatter_not_dict(self, tmp_path: Path):
        """YAML valid but is not dict."""
        content = """---
- item1
- item2
---

Body
"""
        path = tmp_path / "list-frontmatter.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        assert metadata == {}  # List is not valid

    def test_dash_in_middle_of_file(self, tmp_path: Path):
        """--- in the middle of the body must not confuse."""
        content = """---
title: Test
---

Some text
---
More text after dashes
"""
        path = tmp_path / "body-dashes.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        assert metadata["title"] == "Test"

    def test_frontmatter_large_multiple_chunks(self, tmp_path: Path):
        """Frontmatter larger that a chunk."""
        # Create frontmatter with many lines.
        lines = ["---"]
        for i in range(100):
            lines.append(f"key{i}: value{i}")
        lines.append("---")
        lines.append("Body")
        content = "\n".join(lines)

        path = tmp_path / "large-frontmatter.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        assert "key0" in metadata
        assert "key99" in metadata
        assert metadata["key50"] == "value50"

    def test_file_nonexistent(self, tmp_path: Path):
        path = tmp_path / "missing.md"
        metadata, bytes_read = read_frontmatter_only(path)

        assert metadata == {}
        assert bytes_read == 0

    def test_whitespace_before_of_frontmatter(self, tmp_path: Path):
        """Whitespace before of --- invalidates frontmatter."""
        content = """   ---
title: Test
---

Body
"""
        path = tmp_path / "leading-whitespace.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        # Spaces before the first delimiter make the frontmatter invalid.
        assert metadata == {}

    def test_unicode_in_frontmatter(self, tmp_path: Path):
        content = """---
title: Title with Accents
tags: [Portuguese, 日本語, emojis 🎉]
---

Body with unicode: café ☕
"""
        path = tmp_path / "unicode.md"
        path.write_text(content, encoding="utf-8")

        metadata, _ = read_frontmatter_only(path)

        assert metadata["title"] == "Title with Accents"
        assert "Portuguese" in metadata["tags"]


class TestReadFrontmatterPerformance:
    """Tests for that a reading is actually partial."""

    def test_does_not_read_large_body(self, tmp_path: Path):
        """Must not read the entire body when it is large."""
        # Frontmatter small + body very large
        large_body = "x" * (100 * 1024)  # 100KB of body
        content = f"""---
title: Small Frontmatter
---

{large_body}
"""
        path = tmp_path / "large-body.md"
        path.write_text(content)

        metadata, bytes_read = read_frontmatter_only(path)

        assert metadata["title"] == "Small Frontmatter"
        # Must have read very less that the file total
        total_size = len(content.encode("utf-8"))
        assert bytes_read < total_size / 2  # Less than half of the file.
