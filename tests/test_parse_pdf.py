"""
Testes unitários para parse_pdf.py — parser de PDF.

Testes rápidos que NÃO precisam de modelos ML nem LanceDB.
Geram PDFs de teste in-memory com pymupdf.
"""

from pathlib import Path
from unittest.mock import patch

import pymupdf

import vault_search.parsers.pdf as pdf_module
from vault_search.parsers.pdf import _check_ocr_available, _extract_page_text, parse_pdf


def _create_pdf(path: Path, pages: list[str], title: str = "") -> Path:
    """Helper: cria um PDF com as páginas de texto fornecidas."""
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
    def test_pdf_uma_pagina(self, tmp_vault):
        path = _create_pdf(tmp_vault / "single.pdf", ["Hello PDF World"])
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 1
        assert "Hello PDF World" in chunks[0]["text"]
        assert chunks[0]["headers"] == "Page 1"
        assert chunks[0]["note_path"] == "single.pdf"
        assert chunks[0]["tags"] == ""

    def test_pdf_multiplas_paginas(self, tmp_vault):
        path = _create_pdf(
            tmp_vault / "multi.pdf",
            ["Página um", "Página dois", "Página três"],
        )
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 3
        headers = {c["headers"] for c in chunks}
        assert "Page 1" in headers
        assert "Page 2" in headers
        assert "Page 3" in headers

    def test_pdf_com_titulo_metadata(self, tmp_vault):
        path = _create_pdf(
            tmp_vault / "titled.pdf",
            ["Conteúdo"],
            title="Meu Documento PDF",
        )
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_title"] == "Meu Documento PDF"

    def test_pdf_sem_titulo_usa_stem(self, tmp_vault):
        path = _create_pdf(tmp_vault / "notitle.pdf", ["Texto"])
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_title"] == "notitle"

    def test_pdf_pagina_sem_texto_ignorada(self, tmp_vault):
        """Página vazia (sem texto) deve ser ignorada."""
        path = _create_pdf(
            tmp_vault / "blank.pdf",
            ["Texto na página 1", "", "Texto na página 3"],
        )
        chunks = parse_pdf(path, tmp_vault)
        headers = {c["headers"] for c in chunks}
        assert "Page 1" in headers
        assert "Page 2" not in headers
        assert "Page 3" in headers


class TestParsePdfChunking:
    def test_pdf_texto_longo_chunked(self, tmp_vault):
        """Texto de página > CHUNK_SIZE deve ser dividido."""
        # insert_text tem limite de largura da página, usar múltiplas linhas
        doc = pymupdf.open()
        page = doc.new_page(width=2000, height=50000)
        y = 72
        line = "Palavra teste repetida para gerar texto longo no PDF. "
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
    def test_pdf_corrompido(self, tmp_vault):
        path = tmp_vault / "corrupt.pdf"
        path.write_bytes(b"not a pdf file contents")
        chunks = parse_pdf(path, tmp_vault)
        assert chunks == []

    def test_pdf_sem_texto(self, tmp_vault):
        """PDF com página mas sem texto extraível."""
        path = _create_pdf(tmp_vault / "notext.pdf", [""])
        chunks = parse_pdf(path, tmp_vault)
        assert chunks == []

    def test_pdf_subpasta(self, tmp_vault):
        sub = tmp_vault / "docs"
        sub.mkdir()
        path = _create_pdf(sub / "doc.pdf", ["Conteúdo em subpasta"])
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["folder"] == "docs"
        assert chunks[0]["note_path"] == "docs/doc.pdf"

    def test_pdf_unicode(self, tmp_vault):
        path = _create_pdf(
            tmp_vault / "unicode.pdf",
            ["Programação: áéíóú ãõ ç"],
        )
        chunks = parse_pdf(path, tmp_vault)
        assert len(chunks) >= 1
        # pymupdf pode ter encoding diferente, verificar que não crasheia
        assert chunks[0]["text"].strip() != ""

    def test_pdf_protegido_senha(self, tmp_vault):
        """PDF protegido com senha deve retornar lista vazia."""
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Conteúdo secreto")
        path = tmp_vault / "protected.pdf"
        # Salvar com encriptação (senha de usuário)
        doc.save(
            str(path),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw="senha123",
            owner_pw="owner456",
        )
        doc.close()
        chunks = parse_pdf(path, tmp_vault)
        # pymupdf não consegue extrair texto sem a senha
        assert chunks == []


class TestParsePdfOcr:
    """Testes para funcionalidade de OCR."""

    def test_check_ocr_available(self):
        """Verifica detecção de disponibilidade do Tesseract."""
        # Reseta o cache global
        pdf_module._ocr_available = None
        result = _check_ocr_available()
        # Deve retornar bool (True se Tesseract instalado, False caso contrário)
        assert isinstance(result, bool)
        # Segunda chamada deve usar cache
        result2 = _check_ocr_available()
        assert result == result2

    def test_extract_page_text_com_texto_nativo(self):
        """Página com texto nativo não deve usar OCR."""
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Texto nativo direto")
        text = _extract_page_text(page, 0)
        assert "Texto nativo direto" in text
        doc.close()

    def test_extract_page_text_sem_texto_ocr_desabilitado(self):
        """Página sem texto com OCR desabilitado retorna vazio."""
        doc = pymupdf.open()
        page = doc.new_page()  # Página vazia
        with patch.object(pdf_module, "PDF_OCR_ENABLED", False):
            text = _extract_page_text(page, 0)
        assert text == ""
        doc.close()

    def test_extract_page_text_sem_texto_ocr_indisponivel(self):
        """Página sem texto com Tesseract indisponível retorna vazio."""
        doc = pymupdf.open()
        page = doc.new_page()
        # Simula Tesseract não disponível
        pdf_module._ocr_available = False
        text = _extract_page_text(page, 0)
        assert text == ""
        # Restaura estado
        pdf_module._ocr_available = None
        doc.close()

    def test_parse_pdf_ocr_flag_respeitada(self, tmp_vault):
        """Quando OCR desabilitado, páginas sem texto são ignoradas."""
        path = _create_pdf(tmp_vault / "notext_ocr.pdf", [""])
        with patch.object(pdf_module, "PDF_OCR_ENABLED", False):
            chunks = parse_pdf(path, tmp_vault)
        assert chunks == []
