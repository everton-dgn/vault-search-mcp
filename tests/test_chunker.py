"""
Unit tests for chunker.py — chunking hierarchical with overlap.

Fast tests that do not require ML models or LanceDB.
"""

from vault_search.config.chunking import CHUNK_OVERLAP, CHUNK_SIZE
from vault_search.core.chunker import _get_overlap_prefix, chunk_text

# === chunk_text ===


class TestChunkText:
    def test_short_text_is_not_chunked(self):
        text = "Text short."
        chunks = chunk_text(text, 2000, 200)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_generates_multiple_chunks(self, sample_long_text):
        chunks = chunk_text(sample_long_text, 2000, 200)
        assert len(chunks) > 1

    def test_no_chunk_exceeds_chunk_size(self, sample_long_text):
        """No chunk must exceed CHUNK_SIZE (fix #1 overlap stacking)."""
        chunks = chunk_text(sample_long_text, CHUNK_SIZE, CHUNK_OVERLAP)
        for i, chunk in enumerate(chunks):
            assert len(chunk) <= CHUNK_SIZE, (
                f"Chunk {i} has {len(chunk)} chars, maximum is {CHUNK_SIZE}"
            )

    def test_overlap_is_present_between_chunks(self, sample_long_text):
        """Adjacent chunks must share overlapping text."""
        chunks = chunk_text(sample_long_text, 500, 100)
        if len(chunks) >= 2:
            # The end of chunk 0 must appear at the start of chunk 1.
            tail_0 = chunks[0][-50:]  # lasts 50 chars
            # Part of the tail must appear in chunk 1.
            # (overlap can cut in boundary of word)
            found_overlap = any(word in chunks[1][:200] for word in tail_0.split() if len(word) > 3)
            assert found_overlap, "Overlap expected between chunks adjacent"

    def test_without_overlap_when_zero(self):
        text = "A" * 100 + "\n\n" + "B" * 100
        chunks = chunk_text(text, 120, 0)
        assert len(chunks) >= 2

    def test_hierarchical_separators(self):
        """Splitting prefers double newline, newline, sentence, then space boundaries."""
        text = "Paragraph 1.\n\nParagraph 2."
        chunks = chunk_text(text, 20, 0)
        # Split at the blank line instead of inside words.
        assert len(chunks) >= 2

    def test_hard_split_without_separator(self):
        """Text without separators is split at chunk_size."""
        text = "A" * 5000  # without spaces, without newlines
        chunks = chunk_text(text, 2000, 0)
        assert len(chunks) >= 3
        for chunk in chunks:
            assert len(chunk) <= 2000


# === _get_overlap_prefix ===


class TestGetOverlapPrefix:
    def test_text_smaller_than_overlap(self):
        assert _get_overlap_prefix("short", 100) == "short"

    def test_respects_word_boundary(self):
        text = "word1 word2 word3 word4"
        prefix = _get_overlap_prefix(text, 15)
        # Must not cut in the middle of a word
        assert " " not in prefix or prefix[0] != " "

    def test_returns_suffix(self):
        text = "start middle end"
        prefix = _get_overlap_prefix(text, 10)
        assert "end" in prefix

    def test_overlap_without_space(self):
        """Text without spaces returns the complete substring."""
        prefix = _get_overlap_prefix("AAAAAA", 3)
        assert prefix == "AAA"

    def test_overlap_text_empty(self):
        assert _get_overlap_prefix("", 10) == ""


# === Edge cases chunk_text ===


class TestChunkTextEdgeCases:
    def test_text_empty(self):
        """Empty text returns a list containing an empty string."""
        chunks = chunk_text("", 2000, 200)
        assert chunks == [""]

    def test_chunk_size_one(self):
        """chunk_size=1 must split text into individual characters."""
        chunks = chunk_text("abc", 1, 0)
        assert len(chunks) == 3
        assert chunks == ["a", "b", "c"]

    def test_overlap_larger_than_chunk_size(self):
        """Overlap >= chunk_size must not break — prefix truncated."""
        text = "Paragraph 1.\n\nParagraph 2.\n\nParagraph 3."
        chunks = chunk_text(text, 15, 100)
        for chunk in chunks:
            assert len(chunk) <= 15

    def test_text_exactly_chunk_size(self):
        """Text exactly chunk_size characters long produces one chunk."""
        text = "A" * 2000
        chunks = chunk_text(text, 2000, 200)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_unicode_chunking(self):
        """Characters multibyte must not break the chunking."""
        text = "café " * 500  # ~2500 chars
        chunks = chunk_text(text, 500, 50)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 500

    def test_only_separators(self):
        """Text containing only newlines must still produce chunks."""
        text = "\n\n" * 100
        chunks = chunk_text(text, 10, 0)
        # Separators generate empty strings during splitting, but real chunks
        assert all(len(c) <= 10 for c in chunks)

    def test_one_huge_separator(self):
        """A single paragraph larger than chunk_size is split without subseparators."""
        text = "A" * 5000  # without spaces nor newlines
        chunks = chunk_text(text, 2000, 0)
        assert len(chunks) == 3  # 2000 + 2000 + 1000
        assert all(len(c) <= 2000 for c in chunks)
