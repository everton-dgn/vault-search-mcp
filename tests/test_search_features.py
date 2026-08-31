"""
Testes para features de busca: highlight, exclude.
"""

import pytest

from vault_search.core.highlight import (
    apply_highlight,
    extract_highlight_terms,
    highlight_text,
    validate_markers,
)

# === Highlight ===


class TestHighlightText:
    """Testes para highlight_text."""

    def test_highlight_basico(self):
        text = "Python é uma linguagem de programação"
        result = highlight_text(text, "python")
        assert "**Python**" in result

    def test_highlight_case_insensitive(self):
        text = "O PYTHON é ótimo"
        result = highlight_text(text, "python")
        assert "**PYTHON**" in result

    def test_highlight_multiplos_termos(self):
        text = "Python e JavaScript são linguagens populares"
        result = highlight_text(text, "python javascript")
        assert "**Python**" in result
        assert "**JavaScript**" in result

    def test_highlight_ignora_stopwords(self):
        text = "O que é isso para você"
        result = highlight_text(text, "que para isso")
        # Stopwords não devem ser destacadas
        assert "**que**" not in result.lower()
        assert "**para**" not in result.lower()

    def test_highlight_ignora_termos_curtos(self):
        text = "A é uma letra"
        result = highlight_text(text, "a é")
        # Termos < 3 chars não destacados
        assert result == text

    def test_highlight_custom_markers(self):
        text = "Python é legal"
        result = highlight_text(text, "python", "<mark>", "</mark>")
        assert "<mark>Python</mark>" in result

    def test_highlight_texto_vazio(self):
        result = highlight_text("", "python")
        assert result == ""

    def test_highlight_query_vazia(self):
        text = "Python é legal"
        result = highlight_text(text, "")
        assert result == text


class TestExtractHighlightTerms:
    """Testes para extract_highlight_terms."""

    def test_extrai_termos_validos(self):
        terms = extract_highlight_terms("python javascript rust")
        assert "python" in terms
        assert "javascript" in terms
        assert "rust" in terms

    def test_ignora_stopwords(self):
        terms = extract_highlight_terms("the python and javascript")
        assert "the" not in terms
        assert "and" not in terms
        assert "python" in terms

    def test_ignora_termos_curtos(self):
        terms = extract_highlight_terms("a is py python")
        assert "a" not in terms
        assert "is" not in terms
        assert "py" not in terms
        assert "python" in terms

    def test_query_vazia_retorna_lista_vazia(self):
        terms = extract_highlight_terms("")
        assert terms == []

    def test_query_none_like(self):
        # Query que resulta em nenhum termo válido
        terms = extract_highlight_terms("a is the")
        assert terms == []


class TestValidateMarkers:
    """Testes para validate_markers."""

    def test_marcadores_validos(self):
        start, end = validate_markers("**", "**")
        assert start == "**"
        assert end == "**"

    def test_marcadores_html_validos(self):
        start, end = validate_markers("<mark>", "</mark>")
        assert start == "<mark>"
        assert end == "</mark>"

    def test_marcador_inicio_invalido_usa_default(self):
        start, end = validate_markers("INVALID", "**")
        assert start == "**"  # Fallback para default
        assert end == "**"

    def test_marcador_fim_invalido_usa_default(self):
        start, end = validate_markers("**", "INVALID")
        assert start == "**"
        assert end == "**"  # Fallback para default

    def test_ambos_invalidos_usam_default(self):
        start, end = validate_markers("<script>", "</script>")
        assert start == "**"
        assert end == "**"


# === Exclude ===


class TestFilterExcluded:
    """Testes para _filter_excluded do VaultSearcher."""

    @pytest.fixture
    def searcher(self):
        from vault_search.core.searcher import VaultSearcher

        return VaultSearcher()

    @pytest.fixture
    def sample_results(self):
        return [
            {"text": "Python com Django é ótimo", "note_path": "a.md"},
            {"text": "Python com Flask é simples", "note_path": "b.md"},
            {"text": "Python puro sem framework", "note_path": "c.md"},
            {"text": "JavaScript com React", "note_path": "d.md"},
        ]

    def test_exclude_termo_unico(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, ["django"])
        assert len(result) == 3
        assert not any("django" in r["text"].lower() for r in result)

    def test_exclude_multiplos_termos(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, ["django", "flask"])
        assert len(result) == 2
        assert all("django" not in r["text"].lower() for r in result)
        assert all("flask" not in r["text"].lower() for r in result)

    def test_exclude_case_insensitive(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, ["DJANGO"])
        assert len(result) == 3

    def test_exclude_lista_vazia(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, [])
        assert len(result) == 4

    def test_exclude_none(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, None)
        assert len(result) == 4

    def test_exclude_termo_inexistente(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, ["inexistente"])
        assert len(result) == 4

    def test_exclude_remove_todos(self, searcher, sample_results):
        result = searcher._filter_excluded(sample_results, ["python", "javascript"])
        assert len(result) == 0


# === Apply Highlight ===


class TestApplyHighlight:
    """Testes para apply_highlight."""

    def test_apply_highlight_lista(self):
        results = [
            {"text": "Python é ótimo", "note_path": "a.md"},
            {"text": "JavaScript é popular", "note_path": "b.md"},
        ]
        highlighted = apply_highlight(results, "python", True)
        assert "**Python**" in highlighted[0]["text"]
        # Original não deve ser modificado
        assert "**" not in results[0]["text"]

    def test_apply_highlight_false(self):
        results = [{"text": "Python é ótimo", "note_path": "a.md"}]
        highlighted = apply_highlight(results, "python", False)
        assert "**" not in highlighted[0]["text"]
