"""
PDF and OCR processing settings.
"""

from vault_search.config.loader import get_config

_config = get_config().pdf

# Enable OCR for PDF pages without text, such as scans and images
# Requires Tesseract: brew install tesseract tesseract-lang
PDF_OCR_ENABLED = _config.ocr_enabled

# Tesseract OCR languages in "lang1+lang2" format
# Common values: "eng" (English) or another installed Tesseract language code
PDF_OCR_LANGUAGES = _config.ocr_languages

# DPI used to render pages before OCR; higher values are more accurate and slower
PDF_OCR_DPI = _config.ocr_dpi
