"""Unit tests for utils.py — chunking, metadata, math."""

import numpy as np

from vault_search.utils.chunking import chunk_and_collect
from vault_search.utils.math import distance_to_score, normalize_embeddings
from vault_search.utils.metadata import is_empty_text

# === chunk_and_collect ===


class TestChunkAndCollect:
    """Test a function DRY of chunking used by the parsers."""

    def _make_meta(self):
        """Creates metadata mock for tests."""
        return {
            "relative_path": "test.md",
            "folder": "",
            "title": "Test",
            "modified_at": "2024-01-01T00:00:00",
        }

    def test_short_text_generates_one_chunk(self):
        """Text smaller that CHUNK_SIZE generates exactly a chunk."""
        chunks = []
        chunk_and_collect("Text short.", "Header", self._make_meta(), chunks)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "Text short."
        assert chunks[0]["headers"] == "Header"

    def test_empty_text_generates_no_chunks(self):
        """Empty or whitespace-only text produces no chunks."""
        chunks = []
        chunk_and_collect("", "Header", self._make_meta(), chunks)
        assert chunks == []

        chunks = []
        chunk_and_collect("   \n  \n  ", "Header", self._make_meta(), chunks)
        assert chunks == []

    def test_tags_are_propagated(self):
        """Tags are propagated for the chunks."""
        chunks = []
        chunk_and_collect("Text.", "H", self._make_meta(), chunks, tags="python, test")
        assert chunks[0]["tags"] == "python, test"

    def test_modifies_list_in_place(self):
        """The function mutates the supplied list instead of returning a new one."""
        chunks = [{"existing": True}]
        chunk_and_collect("New.", "H", self._make_meta(), chunks)
        assert len(chunks) == 2
        assert chunks[0] == {"existing": True}  # Original item is preserved.

    def test_long_text_generates_multiple_chunks(self):
        """Text larger that CHUNK_SIZE generates multiple chunks."""
        # Text with ~3000 chars (> CHUNK_SIZE=2000)
        long_text = "Lorem ipsum. " * 300
        chunks = []
        chunk_and_collect(long_text, "Header", self._make_meta(), chunks)
        assert len(chunks) >= 2


# === is_empty_text ===


class TestIsEmptyText:
    """Tests for a function is_empty_text."""

    def test_none_is_empty(self):
        """None is considered empty."""
        assert is_empty_text(None) is True

    def test_empty_string_is_empty(self):
        """An empty string is considered empty."""
        assert is_empty_text("") is True

    def test_whitespace_only_is_empty(self):
        """A whitespace-only string is considered empty."""
        assert is_empty_text("   ") is True
        assert is_empty_text("\n\t  ") is True

    def test_non_empty_string(self):
        """A string containing content is not empty."""
        assert is_empty_text("text") is False
        assert is_empty_text("  text  ") is False

    def test_single_char(self):
        """Single character is not empty."""
        assert is_empty_text("a") is False


# === normalize_embeddings ===


class TestNormalizeEmbeddings:
    """Tests for normalization of embeddings."""

    def test_normalize_unit_vector(self):
        """A unit vector remains unchanged."""
        arr = np.array([[1.0, 0.0, 0.0]])
        result = normalize_embeddings(arr)
        np.testing.assert_array_almost_equal(result, arr)

    def test_normalize_scales_to_unit(self):
        """Vectors are scaled to unit norm."""
        arr = np.array([[3.0, 4.0, 0.0]])  # norm = 5
        result = normalize_embeddings(arr)
        expected = np.array([[0.6, 0.8, 0.0]])
        np.testing.assert_array_almost_equal(result, expected)

    def test_normalize_handles_zero_vector(self):
        """A zero vector does not cause division by zero."""
        arr = np.array([[0.0, 0.0, 0.0]])
        result = normalize_embeddings(arr)
        # With epsilon, a zero vector remains zero because it is divided by 1.0.
        np.testing.assert_array_almost_equal(result, arr)

    def test_normalize_batch(self):
        """Multiple vectors are normalized correctly."""
        arr = np.array(
            [
                [3.0, 4.0, 0.0],  # norm = 5
                [1.0, 0.0, 0.0],  # norm = 1
                [0.0, 0.0, 2.0],  # norm = 2
            ]
        )
        result = normalize_embeddings(arr)

        # Verify norms
        norms = np.linalg.norm(result, axis=1)
        np.testing.assert_array_almost_equal(norms, [1.0, 1.0, 1.0])

    def test_normalize_preserves_dtype(self):
        """Preserves dtype float32."""
        arr = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
        result = normalize_embeddings(arr)
        assert result.dtype == np.float32


# === distance_to_score ===


class TestDistanceToScore:
    """Tests for conversion of distance for score."""

    def test_zero_distance_is_max_score(self):
        """Distance 0 produces score 1.0."""
        assert distance_to_score(0.0) == 1.0

    def test_distance_one_is_half_score(self):
        """Distance 1 produces score 0.5."""
        assert distance_to_score(1.0) == 0.5

    def test_large_distance_approaches_zero(self):
        """A large distance produces a score near 0."""
        score = distance_to_score(1000.0)
        assert score < 0.01
        assert score > 0

    def test_score_is_rounded(self):
        """The score is rounded to SCORE_PRECISION decimal places."""
        score = distance_to_score(3.0)
        # 1/(1+3) = 0.25
        assert score == 0.25

    def test_fractional_distance(self):
        """Distance fractional works correctly."""
        score = distance_to_score(0.5)
        # 1/(1+0.5) = 1/1.5 = 0.6667
        assert score == 0.6667


class TestNormalizeTitle:
    """Tests for normalize_title."""

    def test_string_title(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title("My Title", "fallback") == "My Title"

    def test_empty_string_uses_fallback(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title("", "fallback") == "fallback"

    def test_whitespace_only_uses_fallback(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title("   ", "fallback") == "fallback"

    def test_list_uses_first_element(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title(["Title 1", "Title 2"], "fallback") == "Title 1"

    def test_empty_list_uses_fallback(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title([], "fallback") == "fallback"

    def test_list_with_empty_first_uses_fallback(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title(["", "Title 2"], "fallback") == "fallback"

    def test_none_uses_fallback(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title(None, "fallback") == "fallback"

    def test_integer_converts_to_string(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title(123, "fallback") == "123"

    def test_integer_in_list(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title([42], "fallback") == "42"
