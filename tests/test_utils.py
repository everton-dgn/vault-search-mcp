"""Testes unitários para utils.py — chunking, metadata, math."""

import numpy as np

from vault_search.utils.chunking import chunk_and_collect
from vault_search.utils.math import distance_to_score, normalize_embeddings
from vault_search.utils.metadata import is_empty_text

# === chunk_and_collect ===


class TestChunkAndCollect:
    """Testa a função DRY de chunking usada pelos parsers."""

    def _make_meta(self):
        """Cria metadata mock para testes."""
        return {
            "relative_path": "test.md",
            "folder": "",
            "title": "Test",
            "modified_at": "2024-01-01T00:00:00",
        }

    def test_texto_curto_gera_um_chunk(self):
        """Texto menor que CHUNK_SIZE gera exatamente um chunk."""
        chunks = []
        chunk_and_collect("Texto curto.", "Header", self._make_meta(), chunks)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "Texto curto."
        assert chunks[0]["headers"] == "Header"

    def test_texto_vazio_nao_gera_chunks(self):
        """Texto vazio ou só whitespace não gera chunks."""
        chunks = []
        chunk_and_collect("", "Header", self._make_meta(), chunks)
        assert chunks == []

        chunks = []
        chunk_and_collect("   \n  \n  ", "Header", self._make_meta(), chunks)
        assert chunks == []

    def test_tags_propagam(self):
        """Tags são propagadas para os chunks."""
        chunks = []
        chunk_and_collect("Texto.", "H", self._make_meta(), chunks, tags="python, test")
        assert chunks[0]["tags"] == "python, test"

    def test_modifica_lista_inplace(self):
        """A função modifica a lista passada, não retorna nova."""
        chunks = [{"existing": True}]
        chunk_and_collect("Novo.", "H", self._make_meta(), chunks)
        assert len(chunks) == 2
        assert chunks[0] == {"existing": True}  # Original preservado

    def test_texto_longo_gera_multiplos_chunks(self):
        """Texto maior que CHUNK_SIZE gera múltiplos chunks."""
        # Texto com ~3000 chars (> CHUNK_SIZE=2000)
        long_text = "Lorem ipsum. " * 300
        chunks = []
        chunk_and_collect(long_text, "Header", self._make_meta(), chunks)
        assert len(chunks) >= 2


# === is_empty_text ===


class TestIsEmptyText:
    """Testes para a função is_empty_text."""

    def test_none_is_empty(self):
        """None é considerado vazio."""
        assert is_empty_text(None) is True

    def test_empty_string_is_empty(self):
        """String vazia é considerada vazia."""
        assert is_empty_text("") is True

    def test_whitespace_only_is_empty(self):
        """String com apenas whitespace é considerada vazia."""
        assert is_empty_text("   ") is True
        assert is_empty_text("\n\t  ") is True

    def test_non_empty_string(self):
        """String com conteúdo não é vazia."""
        assert is_empty_text("text") is False
        assert is_empty_text("  text  ") is False

    def test_single_char(self):
        """Único caractere não é vazio."""
        assert is_empty_text("a") is False


# === normalize_embeddings ===


class TestNormalizeEmbeddings:
    """Testes para normalização de embeddings."""

    def test_normalize_unit_vector(self):
        """Vetor unitário permanece inalterado."""
        arr = np.array([[1.0, 0.0, 0.0]])
        result = normalize_embeddings(arr)
        np.testing.assert_array_almost_equal(result, arr)

    def test_normalize_scales_to_unit(self):
        """Vetores são escalados para norm=1."""
        arr = np.array([[3.0, 4.0, 0.0]])  # norm = 5
        result = normalize_embeddings(arr)
        expected = np.array([[0.6, 0.8, 0.0]])
        np.testing.assert_array_almost_equal(result, expected)

    def test_normalize_handles_zero_vector(self):
        """Vetor zero não causa divisão por zero."""
        arr = np.array([[0.0, 0.0, 0.0]])
        result = normalize_embeddings(arr)
        # Com epsilon, vetor zero permanece zero (divide por 1.0)
        np.testing.assert_array_almost_equal(result, arr)

    def test_normalize_batch(self):
        """Normaliza múltiplos vetores corretamente."""
        arr = np.array(
            [
                [3.0, 4.0, 0.0],  # norm = 5
                [1.0, 0.0, 0.0],  # norm = 1
                [0.0, 0.0, 2.0],  # norm = 2
            ]
        )
        result = normalize_embeddings(arr)

        # Verificar norms
        norms = np.linalg.norm(result, axis=1)
        np.testing.assert_array_almost_equal(norms, [1.0, 1.0, 1.0])

    def test_normalize_preserves_dtype(self):
        """Preserva dtype float32."""
        arr = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
        result = normalize_embeddings(arr)
        assert result.dtype == np.float32


# === distance_to_score ===


class TestDistanceToScore:
    """Testes para conversão de distância para score."""

    def test_zero_distance_is_max_score(self):
        """Distância 0 resulta em score 1.0."""
        assert distance_to_score(0.0) == 1.0

    def test_distance_one_is_half_score(self):
        """Distância 1 resulta em score 0.5."""
        assert distance_to_score(1.0) == 0.5

    def test_large_distance_approaches_zero(self):
        """Distância grande resulta em score próximo de 0."""
        score = distance_to_score(1000.0)
        assert score < 0.01
        assert score > 0

    def test_score_is_rounded(self):
        """Score é arredondado para SCORE_PRECISION casas."""
        score = distance_to_score(3.0)
        # 1/(1+3) = 0.25
        assert score == 0.25

    def test_fractional_distance(self):
        """Distância fracionária funciona corretamente."""
        score = distance_to_score(0.5)
        # 1/(1+0.5) = 1/1.5 = 0.6667
        assert score == 0.6667


class TestNormalizeTitle:
    """Testes para normalize_title."""

    def test_string_title(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title("Meu Título", "fallback") == "Meu Título"

    def test_empty_string_uses_fallback(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title("", "fallback") == "fallback"

    def test_whitespace_only_uses_fallback(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title("   ", "fallback") == "fallback"

    def test_list_uses_first_element(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title(["Título 1", "Título 2"], "fallback") == "Título 1"

    def test_empty_list_uses_fallback(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title([], "fallback") == "fallback"

    def test_list_with_empty_first_uses_fallback(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title(["", "Título 2"], "fallback") == "fallback"

    def test_none_uses_fallback(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title(None, "fallback") == "fallback"

    def test_integer_converts_to_string(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title(123, "fallback") == "123"

    def test_integer_in_list(self):
        from vault_search.utils.metadata import normalize_title

        assert normalize_title([42], "fallback") == "42"
