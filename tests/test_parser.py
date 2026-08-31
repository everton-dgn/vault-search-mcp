"""
Unit tests for parser.py — frontmatter, tags, headers, parse_note.

Fast tests that do not require ML models or LanceDB.
"""

from vault_search.parsers.frontmatter import (
    extract_frontmatter_fields,
    extract_tags,
    parse_frontmatter,
)
from vault_search.parsers.markdown import parse_note, split_by_headers

# === parse_frontmatter ===


class TestParseFrontmatter:
    def test_without_frontmatter(self):
        meta, body = parse_frontmatter("# Title\n\nText.")
        assert meta == {}
        assert "Title" in body

    def test_frontmatter_valid(self, sample_markdown_with_frontmatter):
        meta, body = parse_frontmatter(sample_markdown_with_frontmatter)
        assert meta["title"] == "My Note"
        assert "python" in meta["tags"]
        assert "Content" in body

    def test_frontmatter_scalar_returns_dict_empty(self, sample_markdown_scalar_frontmatter):
        """YAML scalar (string) must not be accepted as metadata."""
        meta, body = parse_frontmatter(sample_markdown_scalar_frontmatter)
        assert meta == {}
        assert "Body of the note" in body

    def test_frontmatter_list_returns_dict_empty(self, sample_markdown_list_frontmatter):
        """YAML list must not be accepted as metadata."""
        meta, body = parse_frontmatter(sample_markdown_list_frontmatter)
        assert meta == {}
        assert "Body of the note" in body

    def test_frontmatter_int_returns_dict_empty(self):
        meta, body = parse_frontmatter("---\n42\n---\nBody.")
        assert meta == {}

    def test_bom_removed(self):
        content = "\ufeff---\ntitle: test\n---\nBody."
        meta, body = parse_frontmatter(content)
        assert meta.get("title") == "test"

    def test_yaml_invalid(self):
        content = "---\n: invalid: yaml: [broken\n---\nBody."
        meta, body = parse_frontmatter(content)
        assert meta == {}

    def test_frontmatter_empty(self):
        content = "---\n---\nBody."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert "Body" in body

    def test_text_before_frontmatter(self):
        content = "Text before\n---\ntitle: test\n---\nBody."
        meta, body = parse_frontmatter(content)
        assert meta == {}


# === extract_tags ===


class TestExtractTags:
    def test_tags_list(self):
        assert extract_tags({"tags": ["python", "obsidian"]}) == ["python", "obsidian"]

    def test_tags_string_csv(self):
        assert extract_tags({"tags": "python, obsidian"}) == ["python", "obsidian"]

    def test_missing_tags(self):
        assert extract_tags({}) == []

    def test_invalid_tags_type(self):
        assert extract_tags({"tags": 42}) == []

    def test_tags_with_space(self):
        assert extract_tags({"tags": [" python ", " obs "]}) == ["python", "obs"]

    def test_tags_with_empty(self):
        assert extract_tags({"tags": ["python", "", "  "]}) == ["python"]


# === extract_frontmatter_fields ===


class TestExtractFrontmatterFields:
    def test_fields_emptys(self):
        fields = extract_frontmatter_fields({})
        assert fields == {}

    def test_created_at(self):
        fields = extract_frontmatter_fields({"created_at": "2026-01-15"})
        assert fields["created_at"] == "2026-01-15"

    def test_alternative_created_field(self):
        fields = extract_frontmatter_fields({"created": "2026-01-15"})
        assert fields["created_at"] == "2026-01-15"

    def test_date_as_created(self):
        fields = extract_frontmatter_fields({"date": "2026-01-15"})
        assert fields["created_at"] == "2026-01-15"

    def test_description(self):
        fields = extract_frontmatter_fields({"description": "An important note"})
        assert fields["description"] == "An important note"

    def test_summary_as_description(self):
        fields = extract_frontmatter_fields({"summary": "Note summary"})
        assert fields["description"] == "Note summary"

    def test_status(self):
        fields = extract_frontmatter_fields({"status": "Draft"})
        assert fields["status"] == "draft"

    def test_note_type(self):
        fields = extract_frontmatter_fields({"note_type": "Meeting"})
        assert fields["note_type"] == "meeting"

    def test_type_as_note_type(self):
        fields = extract_frontmatter_fields({"type": "daily"})
        assert fields["note_type"] == "daily"

    def test_category_string(self):
        fields = extract_frontmatter_fields({"category": "Work"})
        assert fields["category"] == "work"

    def test_category_list(self):
        fields = extract_frontmatter_fields({"categories": ["work", "project"]})
        assert "work" in fields["category"]
        assert "project" in fields["category"]

    def test_project(self):
        fields = extract_frontmatter_fields({"project": "vault-search-mcp"})
        assert fields["project"] == "vault-search-mcp"

    def test_source_url(self):
        fields = extract_frontmatter_fields({"source": "https://example.com/doc"})
        assert fields["source"] == "https://example.com/doc"

    def test_url_as_source(self):
        fields = extract_frontmatter_fields({"url": "https://example.com"})
        assert fields["source"] == "https://example.com"

    def test_fields_multiple(self):
        metadata = {
            "created_at": "2026-01-15",
            "status": "published",
            "type": "weekly",
            "category": "personal",
            "project": "notes",
        }
        fields = extract_frontmatter_fields(metadata)
        assert fields["created_at"] == "2026-01-15"
        assert fields["status"] == "published"
        assert fields["note_type"] == "weekly"
        assert fields["category"] == "personal"
        assert fields["project"] == "notes"

    def test_ignores_fields_emptys(self):
        fields = extract_frontmatter_fields({"status": ""})
        assert "status" not in fields

    def test_ignores_invalid_types(self):
        fields = extract_frontmatter_fields({"status": 42})
        assert "status" not in fields

    def test_truncated_description(self):
        long_desc = "a" * 1000
        fields = extract_frontmatter_fields({"description": long_desc})
        assert len(fields["description"]) == 500


# === split_by_headers ===


class TestSplitByHeaders:
    def test_without_headers(self):
        sections = split_by_headers("Text simple without headers.")
        assert len(sections) == 1
        assert sections[0]["headers"] == []
        assert "Text simple" in sections[0]["content"]

    def test_hierarchical_headers(self):
        text = "# H1\n\nH1 text\n\n## H2\n\nH2 text\n\n### H3\n\nH3 text"
        sections = split_by_headers(text)
        assert len(sections) >= 3
        # H3 must have hierarchy complete
        h3_section = [s for s in sections if "H3" in s.get("content", "")]
        assert len(h3_section) >= 1

    def test_header_includes_line_in_content(self):
        text = "# Title\n\nText."
        sections = split_by_headers(text)
        assert any("# Title" in s["content"] for s in sections)

    def test_text_empty(self):
        sections = split_by_headers("")
        assert sections == []

    def test_only_whitespace(self):
        sections = split_by_headers("   \n  \n  ")
        assert sections == []


# === parse_note ===


class TestParseNote:
    def test_simple_note(self, tmp_vault):
        note = tmp_vault / "simple.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_path"] == "simple.md"
        assert chunks[0]["folder"] == ""
        assert chunks[0]["note_title"] == "simple"

    def test_note_with_frontmatter(self, tmp_vault):
        note = tmp_vault / "with_meta.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_title"] == "Note with Meta"
        assert "test" in chunks[0]["tags"]
        assert "python" in chunks[0]["tags"]

    def test_note_in_subfolder(self, tmp_vault):
        note = tmp_vault / "projects" / "project1.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert chunks[0]["folder"] == "projects"
        assert chunks[0]["note_path"] == "projects/project1.md"

    def test_note_with_meta_invalid(self, tmp_vault):
        """A frontmatter list must produce empty metadata."""
        note = tmp_vault / "meta_invalid.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["tags"] == ""  # without tags
        assert chunks[0]["note_title"] == "meta_invalid"  # stem of the file

    def test_modified_at_present(self, tmp_vault):
        note = tmp_vault / "simple.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert chunks[0]["modified_at"] != ""
        # Format ISO
        assert "T" in chunks[0]["modified_at"]

    def test_chunks_without_text_empty(self, tmp_vault):
        """No chunk may contain empty text."""
        note = tmp_vault / "simple.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        for chunk in chunks:
            assert chunk["text"].strip() != ""

    def test_note_so_frontmatter(self, tmp_vault):
        """A note with frontmatter but no body returns an empty metadata tuple."""
        note = tmp_vault / "so_meta.md"
        note.write_text("---\ntitle: Empty\ntags: test\n---\n", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert chunks == []

    def test_note_so_whitespace(self, tmp_vault):
        """A whitespace-only note returns an empty metadata tuple."""
        note = tmp_vault / "whitespace.md"
        note.write_text("   \n  \n  \n", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert chunks == []

    def test_note_subfolder_profunda(self, tmp_vault):
        """Note in subfolder deep must have folder correct."""
        deep = tmp_vault / "a" / "b" / "c"
        deep.mkdir(parents=True)
        note = deep / "deep.md"
        note.write_text("# Deep\n\nDeep text.", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert chunks[0]["folder"] == "a/b/c"
        assert chunks[0]["note_path"] == "a/b/c/deep.md"


# === Edge cases: frontmatter and headers ===


class TestParserEdgeCases:
    def test_frontmatter_with_multiple_separators(self):
        """Multiple --- in the body must not confuse the parser."""
        content = "---\ntitle: test\n---\n# Body\n\n---\n\nText after hr."
        meta, body = parse_frontmatter(content)
        assert meta.get("title") == "test"
        assert "---" in body or "Text after hr" in body

    def test_frontmatter_with_unicode(self):
        """Frontmatter supports Unicode values."""
        content = "---\ntitle: Café Résumé\ntags:\n  - 日本語\n---\nBody."
        meta, body = parse_frontmatter(content)
        assert meta.get("title") == "Café Résumé"
        assert "日本語" in meta.get("tags", [])

    def test_deep_heading_level(self):
        """Headers h1 through h4 are split when MARKDOWN_HEADER_LEVELS is 4."""
        text = "# H1\n\n## H2\n\nH2 text.\n\n### H3\n\nH3 text.\n\n#### H4\n\nH4 text."
        sections = split_by_headers(text)
        assert len(sections) >= 4

    def test_tags_as_none(self):
        """A null YAML tags field returns an empty list."""
        assert extract_tags({"tags": None}) == []

    def test_nested_tags_list(self):
        """Nested tag lists must be flattened or ignored."""
        result = extract_tags({"tags": [["nested"]]})
        # Handle malformed nested values without crashing.
        assert isinstance(result, list)

    def test_boolean_tags(self):
        """Boolean tags must return an empty list."""
        assert extract_tags({"tags": True}) == []

    def test_parse_note_file_nonexistent(self, tmp_vault):
        """parse_note returns an empty metadata tuple when the file is missing."""
        path = tmp_vault / "does_not_exist.md"
        chunks, links, aliases = parse_note(path, tmp_vault)
        assert chunks == []

    def test_title_as_int(self, tmp_vault):
        """Frontmatter converts title: 123 to a string."""
        note = tmp_vault / "title_int.md"
        note.write_text("---\ntitle: 123\n---\n# Body\n\nText.", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_title"] == "123"
        assert isinstance(chunks[0]["note_title"], str)

    def test_title_as_list(self, tmp_vault):
        """Frontmatter converts title: [a, b] to a string."""
        note = tmp_vault / "title_list.md"
        note.write_text("---\ntitle:\n  - a\n  - b\n---\n# Body\n\nText.", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert isinstance(chunks[0]["note_title"], str)

    def test_title_as_none(self, tmp_vault):
        """Frontmatter with title: null uses the file stem."""
        note = tmp_vault / "title_null.md"
        note.write_text("---\ntitle: null\n---\n# Body\n\nText.", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_title"] == "title_null"
