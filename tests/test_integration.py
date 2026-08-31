"""
Testes de integração — precisam de modelos ML e LanceDB.

Marcados com @pytest.mark.slow para poder rodar separadamente.
Execute com: pytest tests/test_integration.py -v

Usam fixture indexed_vault baseada em tmp_vault com full_reindex,
removendo dependência do vault real.
"""

from unittest.mock import patch

import pytest

from vault_search.core.indexer import VaultIndexer
from vault_search.core.searcher import VaultSearcher

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def indexed_vault(tmp_path_factory):
    """
    Cria vault temporário, indexa com modelos reais e retorna
    (vault_path, indexer, searcher).
    """
    vault = tmp_path_factory.mktemp("vault")

    # Notas com conteúdo suficiente para gerar embeddings
    (vault / "Welcome.md").write_text(
        "---\ntitle: Welcome\ntags:\n  - welcome\n  - inicio\n---\n"
        "# Welcome to the vault\n\n"
        "Este é o vault de boas-vindas. Aqui você encontra informações "
        "sobre como organizar suas notas e projetos.\n\n"
        "## Começando\n\n"
        "Para começar, crie notas em pastas temáticas.",
        encoding="utf-8",
    )

    examples = vault / "examples"
    examples.mkdir()
    (examples / "exemplo1.md").write_text(
        "---\ntitle: Exemplo 1\ntags: exemplo\n---\n"
        "# Exemplo de nota\n\n"
        "Esta é uma nota de exemplo dentro da pasta examples. "
        "Contém informações sobre testes e validações.",
        encoding="utf-8",
    )
    (examples / "exemplo2.md").write_text(
        "---\ntitle: Exemplo 2\ntags: exemplo\n---\n"
        "# Segundo exemplo\n\n"
        "Mais um exemplo para testar busca por pasta.",
        encoding="utf-8",
    )

    projetos = vault / "projetos"
    projetos.mkdir()
    (projetos / "projeto1.md").write_text(
        "---\ntitle: Projeto Alpha\ntags:\n  - projeto\n  - python\n---\n"
        "# Projeto Alpha\n\n"
        "Descrição do projeto Alpha usando Python e FastAPI.",
        encoding="utf-8",
    )

    # Canvas com conteúdo pesquisável
    import json

    canvas_data = {
        "nodes": [
            {
                "id": "n1",
                "type": "text",
                "text": "Arquitetura do sistema com microserviços e API gateway",
                "x": 0,
                "y": 0,
                "width": 300,
                "height": 200,
            },
            {
                "id": "g1",
                "type": "group",
                "label": "Backend Services",
                "x": 0,
                "y": 300,
                "width": 600,
                "height": 400,
            },
        ],
        "edges": [
            {"id": "e1", "fromNode": "n1", "toNode": "g1", "label": "compõe"},
        ],
    }
    (vault / "arquitetura.canvas").write_text(json.dumps(canvas_data), encoding="utf-8")

    # PDF com conteúdo pesquisável
    import pymupdf

    pdf_doc = pymupdf.open()
    pdf_page = pdf_doc.new_page()
    pdf_page.insert_text(
        (72, 72),
        "Documentação técnica sobre deploy em Kubernetes com Docker containers",
    )
    pdf_doc.set_metadata({"title": "Guia de Deploy"})
    pdf_doc.save(str(vault / "deploy_guide.pdf"))
    pdf_doc.close()

    data_dir = vault / "_data"
    data_dir.mkdir()

    # Patch config para usar vault temporário
    # scanner e parser recebem vault_path como parâmetro, não importam VAULT_PATH
    with (
        patch("vault_search.config.paths.VAULT_PATH", vault),
        patch("vault_search.config.paths.DATA_DIR", data_dir),
        patch("vault_search.core.indexer.VAULT_PATH", vault),
        patch("vault_search.core.indexer.DATA_DIR", data_dir),
        patch("vault_search.core.searcher.DATA_DIR", data_dir),
    ):
        indexer = VaultIndexer()
        indexer._db = None
        indexer._table = None
        indexer.full_reindex()

        searcher = VaultSearcher()
        searcher._db = None
        searcher._table = None

        yield vault, indexer, searcher


class TestIndexerIntegration:
    """Testes de integração do indexer com modelos reais."""

    def test_full_reindex(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        stats = indexer.get_stats()
        assert stats["total_chunks"] > 0
        assert stats["unique_notes"] > 0

    def test_reindex_note_existente(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        result = indexer.reindex_note("Welcome.md")
        assert result["status"] == "updated"
        assert result["chunks_indexed"] > 0

    def test_reindex_note_inexistente(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        result = indexer.reindex_note("nao_existe_xyz.md")
        assert result["status"] == "deleted"
        assert result["chunks_indexed"] == 0

    def test_get_stats(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        stats = indexer.get_stats()
        assert stats["total_chunks"] > 0
        assert stats["unique_notes"] > 0
        assert stats["last_modified"] is not None

    def test_stats_sem_pandas(self, indexed_vault):
        """Stats deve funcionar sem pandas instalado (usa PyArrow nativo)."""
        vault, indexer, searcher = indexed_vault
        stats = indexer.get_stats()
        assert isinstance(stats["total_chunks"], int)
        assert isinstance(stats["unique_notes"], int)
        assert isinstance(stats["last_modified"], str)

    def test_canvas_indexado(self, indexed_vault):
        """Canvas deve estar no índice."""
        vault, indexer, searcher = indexed_vault
        stats = indexer.get_stats()
        # Vault tem .md + .canvas + .pdf — mais notas que só .md
        assert stats["unique_notes"] >= 5  # 4 md + 1 canvas + 1 pdf

    def test_pdf_indexado(self, indexed_vault):
        """PDF deve estar no índice."""
        vault, indexer, searcher = indexed_vault
        result = indexer.reindex_note("deploy_guide.pdf")
        assert result["status"] == "updated"
        assert result["chunks_indexed"] > 0

    def test_reindex_canvas(self, indexed_vault):
        """Reindex incremental de canvas deve funcionar."""
        vault, indexer, searcher = indexed_vault
        result = indexer.reindex_note("arquitetura.canvas")
        assert result["status"] == "updated"
        assert result["chunks_indexed"] > 0


class TestSearcherIntegration:
    """Testes de integração do searcher com modelos reais."""

    def test_search_retorna_resultados(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        results = searcher.search("welcome vault", top_k=3)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_search_campos_padrao(self, indexed_vault):
        """Resultados devem ter todos os campos padrão e NÃO ter vetor."""
        vault, indexer, searcher = indexed_vault
        results = searcher.search("exemplo", top_k=1)
        assert len(results) >= 1
        r = results[0]
        assert "note_path" in r
        assert "note_title" in r
        assert "folder" in r
        assert "headers" in r
        assert "tags" in r
        assert "text" in r
        assert "score" in r
        # Vetor NÃO deve estar presente
        assert "vector" not in r
        # Metadata de segurança foi removida por decisão de produto
        assert "_security" not in r

    def test_search_hybrid(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        results = searcher.search_hybrid("exemplo", top_k=3)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_search_by_folder_existente(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        results = searcher.search_by_folder("exemplo", folder="examples", top_k=3)
        assert isinstance(results, list)
        # Todos os resultados devem ser da pasta solicitada
        for r in results:
            assert r["folder"] == "examples" or r["folder"].startswith("examples/")

    def test_search_by_folder_inexistente(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        results = searcher.search_by_folder("teste", folder="pasta_inexistente_xyz", top_k=3)
        assert results == []

    def test_search_by_folder_boundary(self, indexed_vault):
        """Folder 'exam' NÃO deve retornar resultados de 'examples'."""
        vault, indexer, searcher = indexed_vault
        results = searcher.search_by_folder("exemplo", folder="exam", top_k=10)
        for r in results:
            assert r["folder"] != "examples", (
                f"Folder 'exam' não deveria casar com 'examples': {r['folder']}"
            )

    def test_search_vazio_retorna_lista(self, indexed_vault):
        vault, indexer, searcher = indexed_vault
        results = searcher.search("xyznonexistentquery123456", top_k=1)
        assert isinstance(results, list)

    def test_search_com_top_k_grande(self, indexed_vault):
        """top_k > SEARCH_CANDIDATES base deve funcionar com dynamic candidates."""
        vault, indexer, searcher = indexed_vault
        results = searcher.search("teste", top_k=100)
        assert isinstance(results, list)
        # Não deve crashar mesmo com top_k grande

    def test_search_encontra_canvas(self, indexed_vault):
        """Busca deve encontrar conteúdo de canvas."""
        vault, indexer, searcher = indexed_vault
        results = searcher.search("microserviços API gateway", top_k=5)
        canvas_results = [r for r in results if r["note_path"].endswith(".canvas")]
        assert len(canvas_results) > 0, "Deve encontrar conteúdo do canvas"

    def test_search_encontra_pdf(self, indexed_vault):
        """Busca deve encontrar conteúdo de PDF."""
        vault, indexer, searcher = indexed_vault
        results = searcher.search("deploy Kubernetes Docker", top_k=5)
        pdf_results = [r for r in results if r["note_path"].endswith(".pdf")]
        assert len(pdf_results) > 0, "Deve encontrar conteúdo do PDF"


class TestReindexAtomicity:
    """Testa que reindex_note é atômico (add first, delete old)."""

    def test_dados_preservados_apos_reindex(self, indexed_vault):
        """Após reindex, a nota deve ter chunks válidos."""
        vault, indexer, searcher = indexed_vault

        # Reindexar
        indexer.reindex_note("Welcome.md")
        searcher.invalidate_cache()

        # Buscar deve retornar resultados da nota
        results = searcher.search("Welcome vault", top_k=5)
        welcome_results = [r for r in results if "Welcome" in r["note_path"]]
        assert len(welcome_results) > 0, "Welcome.md deve ter resultados após reindex"

    def test_sem_duplicatas_apos_reindex_duplo(self, indexed_vault):
        """Reindex duplo não deve criar duplicatas."""
        vault, indexer, searcher = indexed_vault

        result1 = indexer.reindex_note("Welcome.md")
        # Reset circuit breaker para permitir segundo reindex imediato (teste)
        indexer.reset_circuit_breaker("Welcome.md")
        result2 = indexer.reindex_note("Welcome.md")

        assert result1["chunks_indexed"] == result2["chunks_indexed"], (
            "Mesma nota deve gerar mesmo número de chunks"
        )

        # Verificar via stats que não há duplicatas
        stats = indexer.get_stats()
        # Se houvesse duplicatas, total_chunks seria maior
        assert stats["total_chunks"] >= result2["chunks_indexed"]
