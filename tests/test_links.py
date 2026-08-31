"""
Testes para utils/links.py — extração e análise de links em markdown.
"""

from vault_search.utils.links import (
    EMBED_PATTERN,
    MARKDOWN_LINK_PATTERN,
    WIKILINK_PATTERN,
    extract_all_links,
    extract_embeds,
    extract_markdown_links,
    extract_wikilinks,
    matches_note,
    normalize_link_target,
)


class TestWikilinkPattern:
    """Testes para o regex WIKILINK_PATTERN."""

    def test_wikilink_simples(self):
        text = "Ver [[minha nota]] para detalhes."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["minha nota"]

    def test_wikilink_com_alias(self):
        text = "Ver [[minha nota|alias]] para detalhes."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["minha nota"]

    def test_wikilink_com_heading(self):
        text = "Ver [[minha nota#seção]] para detalhes."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["minha nota"]

    def test_wikilink_com_alias_e_heading(self):
        text = "Ver [[minha nota#seção|alias]] para detalhes."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["minha nota"]

    def test_wikilink_com_path(self):
        text = "Ver [[pasta/subpasta/nota]] aqui."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["pasta/subpasta/nota"]

    def test_multiplos_wikilinks(self):
        text = "Ver [[nota1]] e [[nota2]] e [[nota3]]."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["nota1", "nota2", "nota3"]

    def test_wikilink_multiline(self):
        text = "Linha 1 [[nota1]]\nLinha 2 [[nota2]]"
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["nota1", "nota2"]

    def test_pattern_nao_captura_embeds(self):
        """
        Regression test: WIKILINK_PATTERN não deve capturar embeds.

        Bug corrigido: o pattern [[...]] capturava wikilinks dentro de embeds ![[...]].
        Solução: usar negative lookbehind (?<!!) para excluir embeds.
        """
        text = "Imagem ![[foto.png]] e link [[Nota]]"
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["Nota"], f"BUG: obtido {matches}, esperado ['Nota']"


class TestMarkdownLinkPattern:
    """Testes para o regex MARKDOWN_LINK_PATTERN."""

    def test_markdown_link_simples(self):
        text = "Ver [texto](nota.md) para detalhes."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == [("texto", "nota.md")]

    def test_markdown_link_sem_extensao(self):
        text = "Ver [texto](nota) para detalhes."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == [("texto", "nota")]

    def test_markdown_link_com_path(self):
        text = "Ver [texto](pasta/nota.md) aqui."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == [("texto", "pasta/nota.md")]

    def test_ignora_url_http(self):
        text = "Ver [Google](https://google.com) externo."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == []

    def test_ignora_url_http_sem_s(self):
        text = "Ver [site](http://example.com) externo."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == []

    def test_ignora_mailto(self):
        text = "Contato [email](mailto:test@test.com)."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == []

    def test_ignora_ancora(self):
        text = "Ver [seção](#ancora) aqui."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == []

    def test_multiplos_links(self):
        text = "[a](nota1.md) e [b](nota2.md)"
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == [("a", "nota1.md"), ("b", "nota2.md")]


class TestEmbedPattern:
    """Testes para o regex EMBED_PATTERN."""

    def test_embed_simples(self):
        text = "Imagem: ![[foto.png]]"
        matches = EMBED_PATTERN.findall(text)
        assert matches == ["foto.png"]

    def test_embed_com_tamanho(self):
        text = "Imagem: ![[foto.png|500]]"
        matches = EMBED_PATTERN.findall(text)
        assert matches == ["foto.png"]

    def test_embed_nota(self):
        text = "Conteúdo: ![[minha nota]]"
        matches = EMBED_PATTERN.findall(text)
        assert matches == ["minha nota"]

    def test_embed_com_path(self):
        text = "![[assets/images/foto.png]]"
        matches = EMBED_PATTERN.findall(text)
        assert matches == ["assets/images/foto.png"]

    def test_multiplos_embeds(self):
        text = "![[a.png]] e ![[b.png]]"
        matches = EMBED_PATTERN.findall(text)
        assert matches == ["a.png", "b.png"]


class TestExtractWikilinks:
    """Testes para extract_wikilinks()."""

    def test_texto_vazio(self):
        assert extract_wikilinks("") == []

    def test_none(self):
        assert extract_wikilinks(None) == []

    def test_sem_links(self):
        assert extract_wikilinks("Texto sem links.") == []

    def test_extrai_unicos(self):
        text = "[[nota]] e [[nota]] e [[NOTA]]"
        result = extract_wikilinks(text)
        # Case-insensitive deduplication, mantém primeira ocorrência
        assert len(result) == 1
        assert result[0] == "nota"

    def test_mantem_case_original(self):
        text = "[[Minha Nota Importante]]"
        result = extract_wikilinks(text)
        assert result == ["Minha Nota Importante"]

    def test_remove_espacos_extras(self):
        text = "[[  nota  ]]"
        result = extract_wikilinks(text)
        assert result == ["nota"]

    def test_nao_captura_embeds(self):
        """
        Regression test: embeds (![[...]]) não devem ser capturados como wikilinks.

        Bug corrigido: o regex [[...]] capturava wikilinks dentro de embeds ![[...]].
        """
        text = "Imagem ![[foto.png]] e link [[Nota]]"
        result = extract_wikilinks(text)

        assert "foto.png" not in result, "BUG: foto.png não deve ser capturado como wikilink"
        assert "Nota" in result, "Nota deve ser capturada como wikilink"
        assert len(result) == 1, "Deve ter exatamente 1 wikilink"


class TestExtractMarkdownLinks:
    """Testes para extract_markdown_links()."""

    def test_texto_vazio(self):
        assert extract_markdown_links("") == []

    def test_none(self):
        assert extract_markdown_links(None) == []

    def test_sem_links(self):
        assert extract_markdown_links("Texto sem links.") == []

    def test_extrai_path(self):
        text = "[texto](pasta/nota.md)"
        result = extract_markdown_links(text)
        assert result == ["pasta/nota.md"]

    def test_remove_ancora(self):
        text = "[texto](nota.md#section)"
        result = extract_markdown_links(text)
        assert result == ["nota.md"]

    def test_deduplica(self):
        text = "[a](nota.md) e [b](nota.md)"
        result = extract_markdown_links(text)
        assert len(result) == 1

    def test_ignora_externos(self):
        text = "[local](nota.md) e [externo](https://google.com)"
        result = extract_markdown_links(text)
        assert result == ["nota.md"]


class TestExtractEmbeds:
    """Testes para extract_embeds()."""

    def test_texto_vazio(self):
        assert extract_embeds("") == []

    def test_none(self):
        assert extract_embeds(None) == []

    def test_sem_embeds(self):
        assert extract_embeds("Texto sem embeds.") == []

    def test_extrai_embeds(self):
        text = "![[imagem.png]] e ![[nota]]"
        result = extract_embeds(text)
        assert "imagem.png" in result
        assert "nota" in result

    def test_deduplica(self):
        text = "![[img.png]] e ![[IMG.PNG]]"
        result = extract_embeds(text)
        assert len(result) == 1


class TestExtractAllLinks:
    """Testes para extract_all_links()."""

    def test_texto_vazio(self):
        result = extract_all_links("")
        assert result == {"wikilinks": [], "markdown_links": [], "embeds": []}

    def test_todos_tipos(self):
        text = """
        Wikilink: [[nota1]]
        Markdown: [texto](nota2.md)
        Embed: ![[imagem.png]]
        """
        result = extract_all_links(text)

        # Wikilinks agora são dicts com campo 'target'
        wikilink_targets = [w["target"] for w in result["wikilinks"]]
        assert "nota1" in wikilink_targets

        # Markdown links são dicts com campo 'target'
        markdown_targets = [m["target"] for m in result["markdown_links"]]
        assert "nota2.md" in markdown_targets

        # Embeds são dicts com campo 'target'
        embed_targets = [e["target"] for e in result["embeds"]]
        assert "imagem.png" in embed_targets

    def test_estrutura_retorno(self):
        result = extract_all_links("test")
        assert "wikilinks" in result
        assert "markdown_links" in result
        assert "embeds" in result
        assert isinstance(result["wikilinks"], list)
        assert isinstance(result["markdown_links"], list)
        assert isinstance(result["embeds"], list)

    def test_wikilink_dict_structure(self):
        """Verifica que wikilinks retornam estrutura completa."""
        result = extract_all_links("Link para [[Nota#Heading|alias]]")
        assert len(result["wikilinks"]) == 1
        wl = result["wikilinks"][0]
        assert wl["target"] == "Nota"
        assert wl["alias"] == "alias"
        assert wl["heading"] == "Heading"
        assert wl["raw"] == "[[Nota#Heading|alias]]"

    def test_markdown_link_dict_structure(self):
        """Verifica que markdown links retornam estrutura completa."""
        result = extract_all_links("[texto do link](path/to/nota.md)")
        assert len(result["markdown_links"]) == 1
        ml = result["markdown_links"][0]
        assert ml["target"] == "path/to/nota.md"
        assert ml["text"] == "texto do link"

    def test_embed_dict_structure(self):
        """Verifica que embeds retornam estrutura completa."""
        result = extract_all_links("Imagem: ![[foto.png|400]]")
        assert len(result["embeds"]) == 1
        emb = result["embeds"][0]
        # target é extraído sem o tamanho
        assert emb["target"] == "foto.png"
        assert "raw" in emb

    def test_embeds_nao_aparecem_em_wikilinks(self):
        """
        Regression test: embeds (![[...]]) não devem aparecer em wikilinks.

        Bug corrigido: o regex de wikilinks capturava [[...]] dentro de ![[...]],
        causando duplicação do target em wikilinks e embeds.
        """
        text = "Imagem ![[foto.png]] e link [[Nota]]"
        result = extract_all_links(text)

        # Embeds só devem aparecer em 'embeds', não em 'wikilinks'
        embed_targets = [e["target"] for e in result["embeds"]]
        wikilink_targets = [w["target"] for w in result["wikilinks"]]

        assert "foto.png" in embed_targets, "foto.png deve estar em embeds"
        assert "foto.png" not in wikilink_targets, "BUG: foto.png não deve aparecer em wikilinks"
        assert "Nota" in wikilink_targets, "Nota deve estar em wikilinks"
        assert len(result["wikilinks"]) == 1, "Deve ter exatamente 1 wikilink"
        assert len(result["embeds"]) == 1, "Deve ter exatamente 1 embed"


class TestNormalizeLinkTarget:
    """Testes para normalize_link_target()."""

    def test_lowercase(self):
        assert normalize_link_target("NOTA") == "nota"

    def test_remove_extensao_md(self):
        assert normalize_link_target("nota.md") == "nota"

    def test_remove_extensao_MD_maiuscula(self):
        assert normalize_link_target("nota.MD") == "nota"

    def test_strip_espacos(self):
        assert normalize_link_target("  nota  ") == "nota"

    def test_path_com_extensao(self):
        assert normalize_link_target("pasta/nota.md") == "pasta/nota"

    def test_sem_extensao(self):
        assert normalize_link_target("nota") == "nota"


class TestMatchesNote:
    """Testes para matches_note()."""

    def test_match_exato_nome(self):
        assert matches_note("minha-nota", "pasta/minha-nota.md") is True

    def test_match_case_insensitive(self):
        assert matches_note("Minha-Nota", "pasta/minha-nota.md") is True

    def test_match_com_extensao(self):
        assert matches_note("minha-nota.md", "pasta/minha-nota.md") is True

    def test_match_path_completo(self):
        assert matches_note("pasta/minha-nota", "pasta/minha-nota.md") is True

    def test_match_espacos_vs_hifens(self):
        assert matches_note("minha nota", "pasta/minha-nota.md") is True

    def test_match_hifens_vs_espacos(self):
        assert matches_note("minha-nota", "pasta/minha nota.md") is True

    def test_nao_match_diferente(self):
        assert matches_note("outra-nota", "pasta/minha-nota.md") is False

    def test_nao_match_parcial(self):
        # "nota" não deve dar match em "minha-nota"
        assert matches_note("nota", "pasta/minha-nota.md") is False

    def test_match_nome_arquivo_completo(self):
        assert matches_note("minha-nota.md", "minha-nota.md") is True

    def test_match_subpasta(self):
        # Match parcial: "sub/nota" corresponde a "pasta/sub/nota.md"
        # porque o path termina com "/sub/nota"
        assert matches_note("sub/nota", "pasta/sub/nota.md") is True
        assert matches_note("pasta/sub/nota", "pasta/sub/nota.md") is True
        # Mas "outra/nota" não corresponde
        assert matches_note("outra/nota", "pasta/sub/nota.md") is False


class TestRealWorldCases:
    """Testes com casos reais do Obsidian."""

    def test_obsidian_daily_note(self):
        text = "Ver [[2024-01-15]] para contexto."
        result = extract_wikilinks(text)
        assert result == ["2024-01-15"]

    def test_obsidian_heading_link(self):
        text = "Ver [[Projeto#Requisitos]] para detalhes."
        result = extract_wikilinks(text)
        assert result == ["Projeto"]

    def test_obsidian_block_reference(self):
        text = "Ver [[Nota^abc123]] para referência."
        # Block references usam ^ antes do ID
        # Nosso regex deve capturar o nome da nota
        result = WIKILINK_PATTERN.findall(text)
        # O ^ não está no padrão, então captura "Nota^abc123"
        # Isso pode precisar de ajuste se quisermos ignorar block refs
        assert len(result) == 1

    def test_mixed_links_document(self):
        text = """
# Meu Documento

Este documento referencia [[Projeto A]] e [[Projeto B|PB]].

Para mais informações, veja [documentação](docs/manual.md).

Imagens: ![[diagrama.png|500]]

Links externos são ignorados: [Google](https://google.com)
"""
        result = extract_all_links(text)

        # Extrair targets dos wikilinks
        wikilink_targets = [w["target"] for w in result["wikilinks"]]
        assert "Projeto A" in wikilink_targets
        assert "Projeto B" in wikilink_targets

        # Extrair targets dos markdown links
        markdown_targets = [m["target"] for m in result["markdown_links"]]
        assert "docs/manual.md" in markdown_targets

        # Extrair targets dos embeds
        embed_targets = [e["target"] for e in result["embeds"]]
        assert "diagrama.png" in embed_targets

        # Google foi ignorado (é externo)
        assert len(result["markdown_links"]) == 1
