"""
Parser de arquivos PDF para o vault-search-mcp.

Usa pymupdf para extrair texto de PDFs. Suporta OCR via Tesseract
para páginas sem texto nativo (scans, imagens), mantendo a ordem
de leitura correta mesmo em PDFs mistos (texto + imagens).

Requisitos para OCR:
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

# Flag para verificar disponibilidade do Tesseract (checado uma vez)
_ocr_available: bool | None = None


def _check_ocr_available() -> bool:
    """Verifica se Tesseract está disponível para OCR."""
    global _ocr_available
    if _ocr_available is not None:
        return _ocr_available

    try:
        # pymupdf verifica Tesseract ao tentar OCR
        # Criamos uma página vazia para testar
        test_doc = pymupdf.open()
        test_page = test_doc.new_page(width=100, height=100)
        test_page.get_textpage_ocr(language="eng", dpi=72)
        test_doc.close()
        _ocr_available = True
        logger.info("Tesseract OCR disponível")
    except Exception as e:
        _ocr_available = False
        logger.warning(
            "Tesseract OCR indisponível (error_type=%s)",
            type(e).__name__,
        )

    return _ocr_available


def _extract_page_text(page: pymupdf.Page, page_num: int) -> str:
    """
    Extrai texto de uma página, usando OCR se necessário.

    Estratégia:
    1. Tenta extrair texto nativo (rápido)
    2. Se vazio e OCR habilitado, faz OCR na página inteira
    3. OCR mantém layout e ordem de leitura

    Parâmetros:
        page: página do pymupdf
        page_num: número da página (para logging)

    Retorna:
        Texto extraído (pode ser vazio se não houver texto nem OCR).
    """
    # Primeiro, tentar texto nativo (instantâneo)
    text = page.get_text("text").strip()

    if text:
        return text

    # Sem texto nativo — tentar OCR se habilitado
    if not PDF_OCR_ENABLED or not _check_ocr_available():
        return ""

    try:
        # get_textpage_ocr faz OCR mantendo layout e ordem de leitura
        # full=True: OCR em toda a página (necessário para scans)
        tp = page.get_textpage_ocr(
            language=PDF_OCR_LANGUAGES,
            dpi=PDF_OCR_DPI,
            full=True,
        )
        text = page.get_text("text", textpage=tp).strip()
        if text:
            logger.debug(f"OCR extraiu texto da página {page_num + 1}")
        return text
    except Exception as e:
        logger.warning(
            "OCR falhou (page=%s, error_type=%s)",
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
    Processa um arquivo PDF e retorna lista de chunks com metadados.

    Parâmetros:
        pdf_path: caminho absoluto do PDF
        vault_path: caminho raiz do vault

    Retorna:
        Lista de ChunkRecord prontos para inserção no LanceDB.
    """
    # Extrair metadados antes de abrir o PDF (valida existência)
    try:
        meta = extract_file_metadata(pdf_path, vault_path)
    except (OSError, ValueError) as e:
        if raise_on_error:
            raise
        logger.warning(
            "Falha ao acessar PDF (error_type=%s)",
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
            "Falha ao abrir PDF (error_type=%s)",
            type(e).__name__,
        )
        return []

    try:
        # Título: metadata do PDF ou stem do arquivo
        pdf_title = doc.metadata.get("title", "").strip() if doc.metadata else ""
        title = pdf_title or meta["title"]

        chunks: list[ChunkRecord] = []

        # Criar meta com título do PDF (pode sobrescrever stem)
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
            "Falha ao extrair PDF (error_type=%s)",
            type(e).__name__,
        )
        return []
    finally:
        if doc is not None:
            doc.close()
