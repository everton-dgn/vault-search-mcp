"""
PDF parser for vault-search-mcp.

Use PyMuPDF to extract text and Tesseract OCR for scanned or image-only
pages while preserving reading order in mixed PDFs.

OCR requirements:
    brew install tesseract tesseract-lang
"""

import logging
from pathlib import Path

import pymupdf

from vault_search.config.pdf import PDF_OCR_DPI, PDF_OCR_ENABLED, PDF_OCR_LANGUAGES
from vault_search.type_defs import ChunkRecord
from vault_search.utils.chunking import chunk_and_collect
from vault_search.utils.metadata import FileMetadata, extract_file_metadata

logger = logging.getLogger(__name__)

# Cache Tesseract availability after the first check.
_ocr_available: bool | None = None


def _check_ocr_available() -> bool:
    """Check whether Tesseract OCR is available."""
    global _ocr_available
    if _ocr_available is not None:
        return _ocr_available

    try:
        # PyMuPDF checks Tesseract when OCR is attempted. Use an empty page as a probe.
        test_doc = pymupdf.open()
        test_page = test_doc.new_page(width=100, height=100)
        test_page.get_textpage_ocr(language="eng", dpi=72)
        test_doc.close()
        _ocr_available = True
        logger.info("Tesseract OCR available")
    except Exception as e:
        _ocr_available = False
        logger.warning(
            "Tesseract OCR unavailable (error_type=%s)",
            type(e).__name__,
        )

    return _ocr_available


def _extract_page_text(page: pymupdf.Page, page_num: int) -> str:
    """
    Extract page text, using OCR when necessary.

    Strategy:
    1. Try fast native text extraction.
    2. Run full-page OCR when native text is empty and OCR is enabled.
    3. Preserve layout and reading order.

    Parameters:
        page: PyMuPDF page.
        page_num: Zero-based page number used for logging.

    Returns:
        Extracted text, possibly empty when no text or OCR is available.
    """
    # Try native text extraction first.
    text = page.get_text("text").strip()

    if text:
        return text

    # Try OCR when native text is empty and OCR is enabled.
    if not PDF_OCR_ENABLED or not _check_ocr_available():
        return ""

    try:
        # ``full=True`` runs OCR across the full page while preserving layout.
        tp = page.get_textpage_ocr(
            language=PDF_OCR_LANGUAGES,
            dpi=PDF_OCR_DPI,
            full=True,
        )
        text = page.get_text("text", textpage=tp).strip()
        if text:
            logger.debug("OCR extracted text from page %d", page_num + 1)
        return text
    except Exception as e:
        logger.warning(
            "ocr_failed page=%s error_type=%s",
            page_num + 1,
            type(e).__name__,
        )
        return ""


def parse_pdf(
    pdf_path: Path,
    vault_path: Path,
    *,
    raise_on_error: bool = False,
) -> list[ChunkRecord]:
    """
    Process a PDF into chunks with metadata.

    Parameters:
        pdf_path: Absolute PDF path.
        vault_path: Vault root path.

    Returns:
        ``ChunkRecord`` entries ready for LanceDB.
    """
    # Read metadata before opening the PDF to validate its existence.
    try:
        meta = extract_file_metadata(pdf_path, vault_path)
    except (OSError, ValueError) as e:
        if raise_on_error:
            raise
        logger.warning(
            "Failed to access PDF (error_type=%s)",
            type(e).__name__,
        )
        return []

    doc = None
    try:
        doc = pymupdf.open(str(pdf_path))
    except (RuntimeError, ValueError, FileNotFoundError, PermissionError, OSError) as e:
        if raise_on_error:
            raise
        logger.warning(
            "Failed to open PDF (error_type=%s)",
            type(e).__name__,
        )
        return []

    try:
        # Prefer PDF metadata title, then the file stem.
        pdf_title = doc.metadata.get("title", "").strip() if doc.metadata else ""
        title = pdf_title or meta["title"]

        chunks: list[ChunkRecord] = []

        # Replace the file stem with the PDF title when available.
        pdf_meta: FileMetadata = {**meta, "title": title}

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = _extract_page_text(page, page_num)
            if not text:
                continue

            header = f"Page {page_num + 1}"
            chunk_and_collect(text, header, pdf_meta, chunks)

        return chunks
    except Exception as e:
        if raise_on_error:
            raise
        logger.warning(
            "Failed to extract PDF (error_type=%s)",
            type(e).__name__,
        )
        return []
    finally:
        if doc is not None:
            doc.close()
