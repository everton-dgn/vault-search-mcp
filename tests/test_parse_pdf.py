"""
Unit tests for parse_pdf.py — parser of PDF.

Fast tests that do not require ML models or LanceDB.
Generate PDFs of test in-memory with pymupdf.
"""

from pathlib import Path
from unittest.mock import patch

import pymupdf

import vault_search.parsers.pdf as pdf_module
from vault_search.parsers.pdf import _check_ocr_available, _extract_page_text, parse_pdf


def _create_pdf(path: Path, pages: list[str], title: str = "") -> Path:
    """Create a PDF with the supplied text pages."""
    doc = pymupdf.open()
    if title:
        doc.set_metadata({"title": title})
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


class TestParsePdfBasic:
    def test_single_page_pdf(self, tmp_vault):
        path = _create_pdf(tmp_vault / "single.pdf", ["Hello PDF World"])
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 1
        assert "Hello PDF World" in chunks[0]["text"]
        assert chunks[0]["headers"] == "Page 1"
        assert chunks[0]["note_path"] == "single.pdf"
        assert chunks[0]["tags"] == ""

    def test_pdf_multiple_pages(self, tmp_vault):
        path = _create_pdf(
            tmp_vault / "multi.pdf",
            ["Page a", "Page two", "Page three"],
        )
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 3
        headers = {c["headers"] for c in chunks}
        assert "Page 1" in headers
        assert "Page 2" in headers
        assert "Page 3" in headers

    def test_pdf_with_title_metadata(self, tmp_vault):
        path = _create_pdf(
            tmp_vault / "titled.pdf",
            ["Content"],
            title="My Document PDF",
        )
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_title"] == "My Document PDF"

    def test_pdf_without_title_uses_stem(self, tmp_vault):
        path = _create_pdf(tmp_vault / "notitle.pdf", ["Text"])
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_title"] == "notitle"

    def test_pdf_page_without_text_is_ignored(self, tmp_vault):
        """An empty page without text is ignored."""
        path = _create_pdf(
            tmp_vault / "blank.pdf",
            ["Text in the page 1", "", "Text in the page 3"],
        )
        chunks = parse_pdf(path, tmp_vault)
        headers = {c["headers"] for c in chunks}
        assert "Page 1" in headers
        assert "Page 2" not in headers
        assert "Page 3" in headers


class TestParsePdfChunking:
    def test_pdf_with_long_text_is_chunked(self, tmp_vault):
        """Page text larger than CHUNK_SIZE must be split."""
        # insert_text has limit of width of the page, use multiple lines
        doc = pymupdf.open()
        page = doc.new_page(width=2000, height=50000)
        y = 72
        line = "Word test repeated for generate text long in the PDF. "
        for _ in range(100):
            page.insert_text((72, y), line)
            y += 14
        doc.save(str(tmp_vault / "long.pdf"))
        doc.close()
        path = tmp_vault / "long.pdf"
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) > 1
        for c in chunks:
            assert c["headers"] == "Page 1"


class TestParsePdfEdgeCases:
    def test_corrupt_pdf(self, tmp_vault):
        path = tmp_vault / "corrupt.pdf"
        path.write_bytes(b"not a pdf file contents")
        chunks = parse_pdf(path, tmp_vault)
        assert chunks == []

    def test_pdf_without_text(self, tmp_vault):
        """PDF with page but without text extractable."""
        path = _create_pdf(tmp_vault / "notext.pdf", [""])
        chunks = parse_pdf(path, tmp_vault)
        assert chunks == []

    def test_pdf_subfolder(self, tmp_vault):
        sub = tmp_vault / "docs"
        sub.mkdir()
        path = _create_pdf(sub / "doc.pdf", ["Content in subfolder"])
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["folder"] == "docs"
        assert chunks[0]["note_path"] == "docs/doc.pdf"

    def test_pdf_unicode(self, tmp_vault):
        path = _create_pdf(
            tmp_vault / "unicode.pdf",
            ["Programming: áéíóú ãõ ç"],
        )
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 1
        # PyMuPDF may use a different encoding; verify that parsing does not crash.
        assert chunks[0]["text"].strip() != ""

    def test_password_protected_pdf(self, tmp_vault):
        """A password-protected PDF returns an empty list."""
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Content secreto")
        path = tmp_vault / "protected.pdf"
        # Save with encryption (password of user)
        doc.save(
            str(path),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw="password123",
            owner_pw="owner456",
        )
        doc.close()
        chunks = parse_pdf(path, tmp_vault)
        # PyMuPDF cannot extract text without a password.
        assert chunks == []


class TestParsePdfOcr:
    """Tests for OCR behavior."""

    def test_check_ocr_available(self):
        """Check Tesseract availability detection."""
        # Resets the cache global
        pdf_module._ocr_available = None
        result = _check_ocr_available()
        # Return a bool: True when Tesseract is installed, otherwise False.
        assert isinstance(result, bool)
        # Second called must use cache
        result2 = _check_ocr_available()
        assert result == result2

    def test_extract_page_text_with_native_text(self):
        """A page containing native text does not use OCR."""
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Direct native text")
        text = _extract_page_text(page, 0)
        assert "Direct native text" in text
        doc.close()

    def test_extract_page_text_without_text_ocr_disabled(self):
        """A page without text returns empty content when OCR is disabled."""
        doc = pymupdf.open()
        page = doc.new_page()  # Page empty
        with patch.object(pdf_module, "PDF_OCR_ENABLED", False):
            text = _extract_page_text(page, 0)
        assert text == ""
        doc.close()

    def test_extract_page_text_without_text_ocr_unavailable(self):
        """A page without text returns empty content when Tesseract is unavailable."""
        doc = pymupdf.open()
        page = doc.new_page()
        # Simulate an unavailable Tesseract installation.
        pdf_module._ocr_available = False
        text = _extract_page_text(page, 0)
        assert text == ""
        # Restore the cached state.
        pdf_module._ocr_available = None
        doc.close()

    def test_parse_pdf_respects_ocr_flag(self, tmp_vault):
        """When OCR disabled, pages without text are ignored."""
        path = _create_pdf(tmp_vault / "notext_ocr.pdf", [""])
        with patch.object(pdf_module, "PDF_OCR_ENABLED", False):
            chunks = parse_pdf(path, tmp_vault)
        assert chunks == []
