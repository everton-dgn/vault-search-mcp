"""
Testes unitários para chunker.py — chunking hierárquico com overlap.

Testes rápidos que NÃO precisam de modelos ML nem LanceDB.
"""

from vault_search.config.chunking import CHUNK_OVERLAP, CHUNK_SIZE
from vault_search.core.chunker import _get_overlap_prefix, chunk_text

# === chunk_text ===


class TestChunkText:
    def test_texto_curto_nao_chunka(self):
        text = "Texto curto."
        chunks = chunk_text(text, 2000, 200)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_texto_longo_gera_multiplos_chunks(self, sample_long_text):
        chunks = chunk_text(sample_long_text, 2000, 200)
        assert len(chunks) > 1

    def test_nenhum_chunk_excede_chunk_size(self, sample_long_text):
        """Nenhum chunk deve exceder CHUNK_SIZE (fix #1 overlap stacking)."""
        chunks = chunk_text(sample_long_text, CHUNK_SIZE, CHUNK_OVERLAP)
        for i, chunk in enumerate(chunks):
            assert len(chunk) <= CHUNK_SIZE, (
                f"Chunk {i} tem {len(chunk)} chars, máximo é {CHUNK_SIZE}"
            )

    def test_overlap_presente_entre_chunks(self, sample_long_text):
        """Chunks adjacentes devem ter texto em comum (overlap)."""
        chunks = chunk_text(sample_long_text, 500, 100)
        if len(chunks) >= 2:
            # O final do chunk 0 deve aparecer no início do chunk 1
            tail_0 = chunks[0][-50:]  # últimos 50 chars
            # Alguma parte do tail deve aparecer no chunk 1
            # (overlap pode cortar em fronteira de palavra)
            found_overlap = any(word in chunks[1][:200] for word in tail_0.split() if len(word) > 3)
            assert found_overlap, "Overlap esperado entre chunks adjacentes"

    def test_sem_overlap_quando_zero(self):
        text = "A" * 100 + "\n\n" + "B" * 100
        chunks = chunk_text(text, 120, 0)
        assert len(chunks) >= 2

    def test_separadores_hierarquicos(self):
        """Deve tentar \\n\\n antes de \\n antes de '. ' antes de ' '."""
        text = "Parágrafo 1.\n\nParágrafo 2."
        chunks = chunk_text(text, 20, 0)
        # Deve splittar em \n\n, não no meio das palavras
        assert len(chunks) >= 2

    def test_corte_duro_quando_sem_separador(self):
        """Texto sem separadores deve ser cortado em chunk_size."""
        text = "A" * 5000  # sem espaços, sem newlines
        chunks = chunk_text(text, 2000, 0)
        assert len(chunks) >= 3
        for chunk in chunks:
            assert len(chunk) <= 2000


# === _get_overlap_prefix ===


class TestGetOverlapPrefix:
    def test_texto_menor_que_overlap(self):
        assert _get_overlap_prefix("curto", 100) == "curto"

    def test_respeita_fronteira_palavra(self):
        text = "palavra1 palavra2 palavra3 palavra4"
        prefix = _get_overlap_prefix(text, 15)
        # Não deve cortar no meio de uma palavra
        assert " " not in prefix or prefix[0] != " "

    def test_retorna_sufixo(self):
        text = "inicio meio final"
        prefix = _get_overlap_prefix(text, 10)
        assert "final" in prefix

    def test_overlap_sem_espaco(self):
        """Texto sem espaço retorna substring inteira."""
        prefix = _get_overlap_prefix("AAAAAA", 3)
        assert prefix == "AAA"

    def test_overlap_texto_vazio(self):
        assert _get_overlap_prefix("", 10) == ""


# === Edge cases chunk_text ===


class TestChunkTextEdgeCases:
    def test_texto_vazio(self):
        """Texto vazio deve retornar lista com string vazia."""
        chunks = chunk_text("", 2000, 200)
        assert chunks == [""]

    def test_chunk_size_um(self):
        """chunk_size=1 deve dividir em caracteres individuais."""
        chunks = chunk_text("abc", 1, 0)
        assert len(chunks) == 3
        assert chunks == ["a", "b", "c"]

    def test_overlap_maior_que_chunk_size(self):
        """Overlap >= chunk_size não deve quebrar — prefix truncado."""
        text = "Parágrafo 1.\n\nParágrafo 2.\n\nParágrafo 3."
        chunks = chunk_text(text, 15, 100)
        for chunk in chunks:
            assert len(chunk) <= 15

    def test_texto_exatamente_chunk_size(self):
        """Texto com exatamente chunk_size chars = 1 chunk."""
        text = "A" * 2000
        chunks = chunk_text(text, 2000, 200)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_unicode_chunking(self):
        """Caracteres multibyte não devem quebrar o chunking."""
        text = "café " * 500  # ~2500 chars
        chunks = chunk_text(text, 500, 50)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 500

    def test_apenas_separadores(self):
        """Texto com apenas newlines deve resultar em chunks."""
        text = "\n\n" * 100
        chunks = chunk_text(text, 10, 0)
        # Separadores geram strings vazias ao splittar, mas chunks reais
        assert all(len(c) <= 10 for c in chunks)

    def test_um_separador_gigante(self):
        """Parágrafo único maior que chunk_size sem sub-separadores."""
        text = "A" * 5000  # sem espaços nem newlines
        chunks = chunk_text(text, 2000, 0)
        assert len(chunks) == 3  # 2000 + 2000 + 1000
        assert all(len(c) <= 2000 for c in chunks)
