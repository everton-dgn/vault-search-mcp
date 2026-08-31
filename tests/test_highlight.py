"""
Testes para o módulo de highlight.

Testa extração de termos, validação de marcadores e aplicação de highlight.
"""

from vault_search.core.highlight import (
    ALLOWED_HIGHLIGHT_MARKERS,
    apply_highlight,
    extract_highlight_terms,
    highlight_text,
    validate_markers,
)


class TestExtractHighlightTerms:
    """Testes para extract_highlight_terms()."""

    def test_empty_query(self):
        """Query vazia retorna lista vazia."""
        assert extract_highlight_terms("") == []
        assert extract_highlight_terms(None) == []

    def test_single_term(self):
        """Termo único é extraído."""
        assert extract_highlight_terms("python") == ["python"]

    def test_multiple_terms(self):
        """Múltiplos termos são extraídos."""
        terms = extract_highlight_terms("python machine learning")
        assert "python" in terms
        assert "machine" in terms
        assert "learning" in terms

    def test_filters_stopwords_english(self):
        """Stopwords em inglês são filtradas."""
        terms = extract_highlight_terms("the python and machine")
        assert "python" in terms
        assert "machine" in terms
        assert "the" not in terms
        assert "and" not in terms

    def test_filters_stopwords_portuguese(self):
        """Stopwords em português são filtradas."""
        terms = extract_highlight_terms("como fazer com python")
        assert "python" in terms
        assert "fazer" in terms
        assert "como" not in terms
        assert "com" not in terms

    def test_filters_short_terms(self):
        """Termos curtos (< MIN_TERM_LENGTH) são filtrados."""
        terms = extract_highlight_terms("a is to python ai")
        assert "python" in terms
        assert "a" not in terms
        assert "is" not in terms
        assert "to" not in terms
        assert "ai" not in terms  # 2 chars < 3

    def test_case_preservation(self):
        """Case é preservado nos termos extraídos."""
        terms = extract_highlight_terms("Python MACHINE Learning")
        assert "Python" in terms
        assert "MACHINE" in terms
        assert "Learning" in terms


class TestValidateMarkers:
    """Testes para validate_markers()."""

    def test_valid_markers(self):
        """Marcadores válidos são aceitos."""
        start, end = validate_markers("**", "**")
        assert start == "**"
        assert end == "**"

    def test_valid_html_markers(self):
        """Marcadores HTML são aceitos."""
        start, end = validate_markers("<mark>", "</mark>")
        assert start == "<mark>"
        assert end == "</mark>"

    def test_invalid_start_marker(self):
        """Marcador de início inválido é substituído por padrão."""
        start, end = validate_markers("INVALID", "**")
        assert start == "**"
        assert end == "**"

    def test_invalid_end_marker(self):
        """Marcador de fim inválido é substituído por padrão."""
        start, end = validate_markers("**", "INVALID")
        assert start == "**"
        assert end == "**"

    def test_both_invalid(self):
        """Ambos inválidos são substituídos por padrão."""
        start, end = validate_markers("<script>", "</script>")
        assert start == "**"
        assert end == "**"

    def test_all_allowed_markers(self):
        """Todos os marcadores da whitelist são aceitos."""
        for marker in ALLOWED_HIGHLIGHT_MARKERS:
            start, end = validate_markers(marker, marker)
            assert start == marker
            assert end == marker


class TestHighlightText:
    """Testes para highlight_text()."""

    def test_empty_text(self):
        """Texto vazio retorna texto vazio."""
        result = highlight_text("", "python")
        assert result == ""

    def test_empty_query(self):
        """Query vazia retorna texto original."""
        text = "Hello world"
        result = highlight_text(text, "")
        assert result == text

    def test_no_match(self):
        """Sem matches retorna texto original."""
        text = "Hello world"
        result = highlight_text(text, "python")
        assert result == text

    def test_single_match(self):
        """Match único é destacado."""
        result = highlight_text("Learn python today", "python")
        assert result == "Learn **python** today"

    def test_multiple_matches(self):
        """Múltiplos matches são destacados."""
        result = highlight_text("Python is great. Use python!", "python")
        assert result == "**Python** is great. Use **python**!"

    def test_case_insensitive(self):
        """Match é case-insensitive."""
        result = highlight_text("PYTHON Python python", "python")
        assert "**PYTHON**" in result
        assert "**Python**" in result
        assert "**python**" in result

    def test_multiple_terms(self):
        """Múltiplos termos são destacados."""
        result = highlight_text("Python and machine learning", "python learning")
        assert "**Python**" in result
        assert "**learning**" in result

    def test_custom_markers(self):
        """Marcadores customizados funcionam."""
        result = highlight_text("Learn python", "python", "<mark>", "</mark>")
        assert result == "Learn <mark>python</mark>"

    def test_stopwords_not_highlighted(self):
        """Stopwords da query não são destacadas."""
        result = highlight_text("the python and the snake", "the python")
        # "the" não deve ser destacado
        assert "**the**" not in result
        assert "**python**" in result

    def test_special_regex_chars_escaped(self):
        """Caracteres especiais de regex são escapados."""
        result = highlight_text("Use file.py for test", "file.py")
        assert "**file.py**" in result

    def test_preserves_original_case(self):
        """Texto original preserva case."""
        result = highlight_text("PYTHON is Great", "python great")
        assert "**PYTHON**" in result
        assert "**Great**" in result


class TestApplyHighlight:
    """Testes para apply_highlight()."""

    def test_highlight_disabled(self):
        """Se highlight=False, retorna resultados inalterados."""
        results = [{"text": "python code"}]
        output = apply_highlight(results, "python", highlight=False)
        assert output == results
        assert output[0]["text"] == "python code"

    def test_highlight_enabled(self):
        """Se highlight=True, aplica highlight."""
        results = [{"text": "python code"}]
        output = apply_highlight(results, "python", highlight=True)
        assert output[0]["text"] == "**python** code"

    def test_multiple_results(self):
        """Múltiplos resultados são processados."""
        results = [
            {"text": "Learn python"},
            {"text": "Python basics"},
            {"text": "No match here"},
        ]
        output = apply_highlight(results, "python", highlight=True)
        assert output[0]["text"] == "Learn **python**"
        assert output[1]["text"] == "**Python** basics"
        assert output[2]["text"] == "No match here"

    def test_does_not_mutate_input(self):
        """Não muta a lista de entrada."""
        results = [{"text": "python code", "score": 0.9}]
        output = apply_highlight(results, "python", highlight=True)
        assert results[0]["text"] == "python code"
        assert output[0]["text"] == "**python** code"
        assert output[0]["score"] == 0.9

    def test_empty_results(self):
        """Lista vazia retorna lista vazia."""
        output = apply_highlight([], "python", highlight=True)
        assert output == []

    def test_missing_text_field(self):
        """Resultado sem campo 'text' não causa erro."""
        results = [{"score": 0.9}]
        output = apply_highlight(results, "python", highlight=True)
        assert output[0]["text"] == ""
        assert output[0]["score"] == 0.9

    def test_custom_markers_in_apply(self):
        """Marcadores customizados funcionam em apply_highlight."""
        results = [{"text": "python code"}]
        output = apply_highlight(
            results, "python", highlight=True, start_marker="==", end_marker="=="
        )
        assert output[0]["text"] == "==python== code"


class TestEdgeCases:
    """Testes de edge cases."""

    def test_unicode_text(self):
        """Texto com unicode funciona."""
        result = highlight_text("Python é incrível", "Python incrível")
        assert "**Python**" in result
        assert "**incrível**" in result

    def test_newlines_preserved(self):
        """Quebras de linha são preservadas."""
        result = highlight_text("Python\nis\ngreat", "python great")
        assert "**Python**" in result
        assert "**great**" in result
        assert "\n" in result

    def test_term_at_boundary(self):
        """Termo no início/fim do texto."""
        assert highlight_text("python", "python") == "**python**"
        assert highlight_text("python is", "python") == "**python** is"
        assert highlight_text("is python", "python") == "is **python**"

    def test_adjacent_terms(self):
        """Termos adjacentes são destacados separadamente."""
        result = highlight_text("python machine", "python machine")
        assert "**python**" in result
        assert "**machine**" in result

    def test_term_as_substring(self):
        """Termo como substring de palavra maior é destacado."""
        # "code" é substring de "codebase"
        result = highlight_text("codebase contains code", "code")
        # Ambas ocorrências de "code" devem ser destacadas
        assert "**code**" in result

    def test_very_long_text(self):
        """Texto muito longo funciona."""
        text = "python " * 1000
        result = highlight_text(text, "python")
        assert result.count("**python**") == 1000

    def test_many_terms_query(self):
        """Query com muitos termos funciona."""
        # Usar nomes únicos que não sejam prefixos uns dos outros
        terms = ["alpha", "beta", "gamma", "delta", "epsilon"]
        query = " ".join(terms)
        text = "alpha and beta with gamma"
        result = highlight_text(text, query)
        assert "**alpha**" in result
        assert "**beta**" in result
        assert "**gamma**" in result
