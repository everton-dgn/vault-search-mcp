"""
Tests for features of search: highlight, exclude.
"""

import pytest

from vault_search.core.highlight import (
    apply_highlight,
    extract_highlight_terms,
    highlight_text,
    validate_markers,
)

# === Highlight ===


class TestHighlightText:
    """Tests for highlight_text."""

    def test_basic_highlight(self):
        text = "Python is a programming language"
        result = highlight_text(text, "python")
        assert "**Python**" in result

    def test_highlight_case_insensitive(self):
        text = "PYTHON is excellent"
        result = highlight_text(text, "python")
        assert "**PYTHON**" in result

    def test_highlight_multiple_terms(self):
        text = "Python and JavaScript are popular languages"
        result = highlight_text(text, "python javascript")
        assert "**Python**" in result
        assert "**JavaScript**" in result

    def test_highlight_ignores_stopwords(self):
        text = "This is for you"
        result = highlight_text(text, "this for you")
        # Stopwords must not be highlighted.
        assert "**this**" not in result.lower()
        assert "**for**" not in result.lower()

    def test_highlight_ignores_terms_short(self):
        text = "A is a letter"
        result = highlight_text(text, "a is")
        # Terms shorter than three characters are not highlighted.
        assert result == text

    def test_highlight_custom_markers(self):
        text = "Python is useful"
        result = highlight_text(text, "python", "<mark>", "</mark>")
        assert "<mark>Python</mark>" in result

    def test_highlight_text_empty(self):
        result = highlight_text("", "python")
        assert result == ""

    def test_highlight_query_empty(self):
        text = "Python is legal"
        result = highlight_text(text, "")
        assert result == text


class TestExtractHighlightTerms:
    """Tests for extract_highlight_terms."""

    def test_extracts_valid_terms(self):
        terms = extract_highlight_terms("python javascript rust")
        assert "python" in terms
        assert "javascript" in terms
        assert "rust" in terms

    def test_ignores_stopwords(self):
        terms = extract_highlight_terms("the python and javascript")
        assert "the" not in terms
        assert "and" not in terms
        assert "python" in terms

    def test_ignores_terms_short(self):
        terms = extract_highlight_terms("a is py python")
        assert "a" not in terms
        assert "is" not in terms
        assert "py" not in terms
        assert "python" in terms

    def test_query_empty_returns_list_empty(self):
        terms = extract_highlight_terms("")
        assert terms == []

    def test_query_none_like(self):
        # Query that results in no valid term.
        terms = extract_highlight_terms("a is the")
        assert terms == []


class TestValidateMarkers:
    """Tests for validate_markers."""

    def test_valid_markers(self):
        start, end = validate_markers("**", "**")
        assert start == "**"
        assert end == "**"

    def test_valid_html_markers(self):
        start, end = validate_markers("<mark>", "</mark>")
        assert start == "<mark>"
        assert end == "</mark>"

    def test_invalid_start_marker_uses_default(self):
        start, end = validate_markers("INVALID", "**")
        assert start == "**"  # Fallback for default
        assert end == "**"

    def test_invalid_end_marker_uses_default(self):
        start, end = validate_markers("**", "INVALID")
        assert start == "**"
        assert end == "**"  # Fallback for default

    def test_both_invalid_use_default(self):
        start, end = validate_markers("<script>", "</script>")
        assert start == "**"
        assert end == "**"


# === Exclude ===


class TestFilterExcluded:
    """Tests for VaultSearcher._filter_excluded()."""

    @pytest.fixture
    def searcher(self):
        from vault_search.core.searcher import VaultSearcher

        return VaultSearcher()

    @pytest.fixture
    def sample_results(self):
        return [
            {"text": "Python with Django is great", "note_path": "a.md"},
            {"text": "Python with Flask is simple", "note_path": "b.md"},
            {"text": "Pure Python without a framework", "note_path": "c.md"},
            {"text": "JavaScript with React", "note_path": "d.md"},
        ]

    def test_exclude_single_term(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, ["django"])
        assert len(result) == 3
        assert not any("django" in r["text"].lower() for r in result)

    def test_exclude_multiple_terms(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, ["django", "flask"])
        assert len(result) == 2
        assert all("django" not in r["text"].lower() for r in result)
        assert all("flask" not in r["text"].lower() for r in result)

    def test_exclude_case_insensitive(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, ["DJANGO"])
        assert len(result) == 3

    def test_exclude_list_empty(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, [])
        assert len(result) == 4

    def test_exclude_none(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, None)
        assert len(result) == 4

    def test_exclude_nonexistent_term(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, ["nonexistent"])
        assert len(result) == 4

    def test_exclude_remove_all(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, ["python", "javascript"])
        assert len(result) == 0


# === Apply Highlight ===


class TestApplyHighlight:
    """Tests for apply_highlight."""

    def test_apply_highlight_list(self):
        results = [
            {"text": "Python is great", "note_path": "a.md"},
            {"text": "JavaScript is popular", "note_path": "b.md"},
        ]
        highlighted = apply_highlight(results, "python", True)
        assert "**Python**" in highlighted[0]["text"]
        # The original input must not be modified.
        assert "**" not in results[0]["text"]

    def test_apply_highlight_false(self):
        results = [{"text": "Python is great", "note_path": "a.md"}]
        highlighted = apply_highlight(results, "python", False)
        assert "**" not in highlighted[0]["text"]
