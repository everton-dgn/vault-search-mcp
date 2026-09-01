"""
Tests for the highlighting module.

Test extraction of terms, validation of markers and application of highlight.
"""

from vault_search.core.highlight import (
    ALLOWED_HIGHLIGHT_MARKERS,
    apply_highlight,
    extract_highlight_terms,
    highlight_text,
    validate_markers,
)


class TestExtractHighlightTerms:
    """Tests for extract_highlight_terms()."""

    def test_empty_query(self):
        """An empty query returns an empty list."""
        assert extract_highlight_terms("") == []
        assert extract_highlight_terms(None) == []

    def test_single_term(self):
        """A single term is extracted."""
        assert extract_highlight_terms("python") == ["python"]

    def test_multiple_terms(self):
        """Multiple terms are extracted."""
        terms = extract_highlight_terms("python machine learning")
        assert "python" in terms
        assert "machine" in terms
        assert "learning" in terms

    def test_filters_stopwords_english(self):
        """English stop words are filtered."""
        terms = extract_highlight_terms("the python and machine")
        assert "python" in terms
        assert "machine" in terms
        assert "the" not in terms
        assert "and" not in terms

    def test_filters_additional_english_stopwords(self):
        """Additional English stopwords are filtered."""
        terms = extract_highlight_terms("that build with python")
        assert "python" in terms
        assert "build" in terms
        assert "that" not in terms
        assert "with" not in terms

    def test_filters_portuguese_stopwords(self):
        """Portuguese stop words remain supported for multilingual vaults."""
        assert extract_highlight_terms("como fazer com python") == ["fazer", "python"]

    def test_filters_short_terms(self):
        """Terms shorter than MIN_TERM_LENGTH are filtered."""
        terms = extract_highlight_terms("a is to python ai")
        assert "python" in terms
        assert "a" not in terms
        assert "is" not in terms
        assert "to" not in terms
        assert "ai" not in terms  # 2 chars < 3

    def test_case_preservation(self):
        """Extracted terms preserve their original case."""
        terms = extract_highlight_terms("Python MACHINE Learning")
        assert "Python" in terms
        assert "MACHINE" in terms
        assert "Learning" in terms


class TestValidateMarkers:
    """Tests for validate_markers()."""

    def test_valid_markers(self):
        """Valid markers are accepted."""
        start, end = validate_markers("**", "**")
        assert start == "**"
        assert end == "**"

    def test_valid_html_markers(self):
        """Markers HTML are accepted."""
        start, end = validate_markers("<mark>", "</mark>")
        assert start == "<mark>"
        assert end == "</mark>"

    def test_invalid_start_marker(self):
        """Marker of start invalid is replaced by default."""
        start, end = validate_markers("INVALID", "**")
        assert start == "**"
        assert end == "**"

    def test_invalid_end_marker(self):
        """Marker of end invalid is replaced by default."""
        start, end = validate_markers("**", "INVALID")
        assert start == "**"
        assert end == "**"

    def test_both_invalid(self):
        """Two invalid markers are replaced by the defaults."""
        start, end = validate_markers("<script>", "</script>")
        assert start == "**"
        assert end == "**"

    def test_all_allowed_markers(self):
        """All the markers of the whitelist are accepted."""
        for marker in ALLOWED_HIGHLIGHT_MARKERS:
            start, end = validate_markers(marker, marker)
            assert start == marker
            assert end == marker


class TestHighlightText:
    """Tests for highlight_text()."""

    def test_empty_text(self):
        """Empty text remains empty."""
        result = highlight_text("", "python")
        assert result == ""

    def test_empty_query(self):
        """An empty query returns the original text."""
        text = "Hello world"
        result = highlight_text(text, "")
        assert result == text

    def test_in_match(self):
        """Without matches returns text original."""
        text = "Hello world"
        result = highlight_text(text, "python")
        assert result == text

    def test_single_match(self):
        """A single match is highlighted."""
        result = highlight_text("Learn python today", "python")
        assert result == "Learn **python** today"

    def test_multiple_matches(self):
        """Multiple matches are highlighted."""
        result = highlight_text("Python is great. Use python!", "python")
        assert result == "**Python** is great. Use **python**!"

    def test_case_insensitive(self):
        """Match is case-insensitive."""
        result = highlight_text("PYTHON Python python", "python")
        assert "**PYTHON**" in result
        assert "**Python**" in result
        assert "**python**" in result

    def test_multiple_terms(self):
        """Multiple terms are highlighted."""
        result = highlight_text("Python and machine learning", "python learning")
        assert "**Python**" in result
        assert "**learning**" in result

    def test_custom_markers(self):
        """Custom markers work."""
        result = highlight_text("Learn python", "python", "<mark>", "</mark>")
        assert result == "Learn <mark>python</mark>"

    def test_stopwords_not_highlighted(self):
        """Stopwords of the query are not highlighted."""
        result = highlight_text("the python and the snake", "the python")
        # "the" must not be highlighted.
        assert "**the**" not in result
        assert "**python**" in result

    def test_special_regex_chars_escaped(self):
        """Regular-expression metacharacters are escaped."""
        result = highlight_text("Use file.py for test", "file.py")
        assert "**file.py**" in result

    def test_preserves_original_case(self):
        """Text original preserves case."""
        result = highlight_text("PYTHON is Great", "python great")
        assert "**PYTHON**" in result
        assert "**Great**" in result


class TestApplyHighlight:
    """Tests for apply_highlight()."""

    def test_highlight_disabled(self):
        """If highlight=False, returns results unchanged."""
        results = [{"text": "python code"}]
        output = apply_highlight(results, "python", highlight=False)
        assert output == results
        assert output[0]["text"] == "python code"

    def test_highlight_enabled(self):
        """highlight=True applies highlighting."""
        results = [{"text": "python code"}]
        output = apply_highlight(results, "python", highlight=True)
        assert output[0]["text"] == "**python** code"

    def test_multiple_results(self):
        """Multiple results are processed."""
        results = [
            {"text": "Learn python"},
            {"text": "Python basics"},
            {"text": "In the match here"},
        ]
        output = apply_highlight(results, "python", highlight=True)
        assert output[0]["text"] == "Learn **python**"
        assert output[1]["text"] == "**Python** basics"
        assert output[2]["text"] == "In the match here"

    def test_does_not_mutate_input(self):
        """Not mutates a list of input."""
        results = [{"text": "python code", "score": 0.9}]
        output = apply_highlight(results, "python", highlight=True)
        assert results[0]["text"] == "python code"
        assert output[0]["text"] == "**python** code"
        assert output[0]["score"] == 0.9

    def test_empty_results(self):
        """An empty list returns an empty list."""
        output = apply_highlight([], "python", highlight=True)
        assert output == []

    def test_missing_text_field(self):
        """A result without a text field does not raise an error."""
        results = [{"score": 0.9}]
        output = apply_highlight(results, "python", highlight=True)
        assert output[0]["text"] == ""
        assert output[0]["score"] == 0.9

    def test_custom_markers_in_apply(self):
        """Custom markers work in apply_highlight."""
        results = [{"text": "python code"}]
        output = apply_highlight(
            results, "python", highlight=True, start_marker="==", end_marker="=="
        )
        assert output[0]["text"] == "==python== code"


class TestEdgeCases:
    """Tests for edge cases."""

    def test_unicode_text(self):
        """Unicode text is highlighted correctly."""
        result = highlight_text("Python handles café and naïve text", "café naïve")
        assert "**café**" in result
        assert "**naïve**" in result

    def test_newlines_preserved(self):
        """Line breaks are preserved."""
        result = highlight_text("Python\nis\ngreat", "python great")
        assert "**Python**" in result
        assert "**great**" in result
        assert "\n" in result

    def test_term_at_boundary(self):
        """Term in the start/end of the text."""
        assert highlight_text("python", "python") == "**python**"
        assert highlight_text("python is", "python") == "**python** is"
        assert highlight_text("is python", "python") == "is **python**"

    def test_adjacent_terms(self):
        """Adjacent terms are highlighted separately."""
        result = highlight_text("python machine", "python machine")
        assert "**python**" in result
        assert "**machine**" in result

    def test_term_as_substring(self):
        """A term that is a substring of a larger word is highlighted."""
        # "code" is substring of "codebase"
        result = highlight_text("codebase contains code", "code")
        # Ambas occurrences of "code" must be highlighted
        assert "**code**" in result

    def test_very_long_text(self):
        """Text very long works."""
        text = "python " * 1000
        result = highlight_text(text, "python")
        assert result.count("**python**") == 1000

    def test_many_terms_query(self):
        """A query containing many terms is highlighted correctly."""
        # Use names singles that not are prefixes some of the other
        terms = ["alpha", "beta", "gamma", "delta", "epsilon"]
        query = " ".join(terms)
        text = "alpha and beta with gamma"
        result = highlight_text(text, query)
        assert "**alpha**" in result
        assert "**beta**" in result
        assert "**gamma**" in result
