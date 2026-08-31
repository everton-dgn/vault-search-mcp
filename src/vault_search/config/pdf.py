"""
Configurações de processamento de PDFs e OCR.
"""

from vault_search.config.loader import get_config

_config = get_config().pdf

# Habilita OCR para páginas de PDF sem texto (scans, imagens)
# Requer Tesseract instalado: brew install tesseract tesseract-lang
PDF_OCR_ENABLED = _config.ocr_enabled

# Idiomas do Tesseract para OCR (formato: "lang1+lang2")
# Comum: "por" (português), "eng" (inglês), "por+eng" (ambos)
PDF_OCR_LANGUAGES = _config.ocr_languages

# DPI para renderização de páginas antes do OCR (maior = mais preciso, mais lento)
PDF_OCR_DPI = _config.ocr_dpi
