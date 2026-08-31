"""
Testes unitários para parser.py — frontmatter, tags, headers, parse_note.

Testes rápidos que NÃO precisam de modelos ML nem LanceDB.
"""

from vault_search.parsers.frontmatter import (
    extract_frontmatter_fields,
    extract_tags,
    parse_frontmatter,
)
from vault_search.parsers.markdown import parse_note, split_by_headers

# === parse_frontmatter ===


class TestParseFrontmatter:
    def test_sem_frontmatter(self):
        meta, body = parse_frontmatter("# Título\n\nTexto.")
        assert meta == {}
        assert "Título" in body

    def test_frontmatter_valido(self, sample_markdown_with_frontmatter):
        meta, body = parse_frontmatter(sample_markdown_with_frontmatter)
        assert meta["title"] == "Minha Nota"
        assert "python" in meta["tags"]
        assert "Conteúdo" in body

    def test_frontmatter_escalar_retorna_dict_vazio(self, sample_markdown_scalar_frontmatter):
        """YAML escalar (string) não deve ser aceito como metadata."""
        meta, body = parse_frontmatter(sample_markdown_scalar_frontmatter)
        assert meta == {}
        assert "Corpo da nota" in body

    def test_frontmatter_lista_retorna_dict_vazio(self, sample_markdown_list_frontmatter):
        """YAML lista não deve ser aceito como metadata."""
        meta, body = parse_frontmatter(sample_markdown_list_frontmatter)
        assert meta == {}
        assert "Corpo da nota" in body

    def test_frontmatter_int_retorna_dict_vazio(self):
        meta, body = parse_frontmatter("---\n42\n---\nCorpo.")
        assert meta == {}

    def test_bom_removido(self):
        content = "\ufeff---\ntitle: teste\n---\nCorpo."
        meta, body = parse_frontmatter(content)
        assert meta.get("title") == "teste"

    def test_yaml_invalido(self):
        content = "---\n: invalid: yaml: [broken\n---\nCorpo."
        meta, body = parse_frontmatter(content)
        assert meta == {}

    def test_frontmatter_vazio(self):
        content = "---\n---\nCorpo."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert "Corpo" in body

    def test_texto_antes_do_frontmatter(self):
        content = "Texto antes\n---\ntitle: teste\n---\nCorpo."
        meta, body = parse_frontmatter(content)
        assert meta == {}


# === extract_tags ===


class TestExtractTags:
    def test_tags_lista(self):
        assert extract_tags({"tags": ["python", "obsidian"]}) == ["python", "obsidian"]

    def test_tags_string_csv(self):
        assert extract_tags({"tags": "python, obsidian"}) == ["python", "obsidian"]

    def test_tags_ausente(self):
        assert extract_tags({}) == []

    def test_tags_tipo_invalido(self):
        assert extract_tags({"tags": 42}) == []

    def test_tags_com_espaco(self):
        assert extract_tags({"tags": [" python ", " obs "]}) == ["python", "obs"]

    def test_tags_com_vazio(self):
        assert extract_tags({"tags": ["python", "", "  "]}) == ["python"]


# === extract_frontmatter_fields ===


class TestExtractFrontmatterFields:
    def test_campos_vazios(self):
        fields = extract_frontmatter_fields({})
        assert fields == {}

    def test_created_at(self):
        fields = extract_frontmatter_fields({"created_at": "2026-01-15"})
        assert fields["created_at"] == "2026-01-15"

    def test_created_alternativo(self):
        fields = extract_frontmatter_fields({"created": "2026-01-15"})
        assert fields["created_at"] == "2026-01-15"

    def test_date_como_created(self):
        fields = extract_frontmatter_fields({"date": "2026-01-15"})
        assert fields["created_at"] == "2026-01-15"

    def test_description(self):
        fields = extract_frontmatter_fields({"description": "Uma nota importante"})
        assert fields["description"] == "Uma nota importante"

    def test_summary_como_description(self):
        fields = extract_frontmatter_fields({"summary": "Resumo da nota"})
        assert fields["description"] == "Resumo da nota"

    def test_status(self):
        fields = extract_frontmatter_fields({"status": "Draft"})
        assert fields["status"] == "draft"

    def test_note_type(self):
        fields = extract_frontmatter_fields({"note_type": "Meeting"})
        assert fields["note_type"] == "meeting"

    def test_type_como_note_type(self):
        fields = extract_frontmatter_fields({"type": "daily"})
        assert fields["note_type"] == "daily"

    def test_category_string(self):
        fields = extract_frontmatter_fields({"category": "Work"})
        assert fields["category"] == "work"

    def test_category_lista(self):
        fields = extract_frontmatter_fields({"categories": ["work", "project"]})
        assert "work" in fields["category"]
        assert "project" in fields["category"]

    def test_project(self):
        fields = extract_frontmatter_fields({"project": "vault-search-mcp"})
        assert fields["project"] == "vault-search-mcp"

    def test_source_url(self):
        fields = extract_frontmatter_fields({"source": "https://example.com/doc"})
        assert fields["source"] == "https://example.com/doc"

    def test_url_como_source(self):
        fields = extract_frontmatter_fields({"url": "https://example.com"})
        assert fields["source"] == "https://example.com"

    def test_campos_multiplos(self):
        metadata = {
            "created_at": "2026-01-15",
            "status": "published",
            "type": "weekly",
            "category": "personal",
            "project": "notes",
        }
        fields = extract_frontmatter_fields(metadata)
        assert fields["created_at"] == "2026-01-15"
        assert fields["status"] == "published"
        assert fields["note_type"] == "weekly"
        assert fields["category"] == "personal"
        assert fields["project"] == "notes"

    def test_ignora_campos_vazios(self):
        fields = extract_frontmatter_fields({"status": ""})
        assert "status" not in fields

    def test_ignora_tipos_invalidos(self):
        fields = extract_frontmatter_fields({"status": 42})
        assert "status" not in fields

    def test_description_truncada(self):
        long_desc = "a" * 1000
        fields = extract_frontmatter_fields({"description": long_desc})
        assert len(fields["description"]) == 500


# === split_by_headers ===


class TestSplitByHeaders:
    def test_sem_headers(self):
        sections = split_by_headers("Texto simples sem headers.")
        assert len(sections) == 1
        assert sections[0]["headers"] == []
        assert "Texto simples" in sections[0]["content"]

    def test_headers_hierarquicos(self):
        text = "# H1\n\nTexto H1\n\n## H2\n\nTexto H2\n\n### H3\n\nTexto H3"
        sections = split_by_headers(text)
        assert len(sections) >= 3
        # H3 deve ter hierarquia completa
        h3_section = [s for s in sections if "H3" in s.get("content", "")]
        assert len(h3_section) >= 1

    def test_header_inclui_linha_no_conteudo(self):
        text = "# Título\n\nTexto."
        sections = split_by_headers(text)
        assert any("# Título" in s["content"] for s in sections)

    def test_texto_vazio(self):
        sections = split_by_headers("")
        assert sections == []

    def test_apenas_whitespace(self):
        sections = split_by_headers("   \n  \n  ")
        assert sections == []


# === parse_note ===


class TestParseNote:
    def test_nota_simples(self, tmp_vault):
        note = tmp_vault / "simples.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_path"] == "simples.md"
        assert chunks[0]["folder"] == ""
        assert chunks[0]["note_title"] == "simples"

    def test_nota_com_frontmatter(self, tmp_vault):
        note = tmp_vault / "com_meta.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_title"] == "Nota com Meta"
        assert "teste" in chunks[0]["tags"]
        assert "python" in chunks[0]["tags"]

    def test_nota_em_subpasta(self, tmp_vault):
        note = tmp_vault / "projetos" / "projeto1.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert chunks[0]["folder"] == "projetos"
        assert chunks[0]["note_path"] == "projetos/projeto1.md"

    def test_nota_com_meta_invalido(self, tmp_vault):
        """Frontmatter lista deve resultar em metadata vazio."""
        note = tmp_vault / "meta_invalido.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["tags"] == ""  # sem tags
        assert chunks[0]["note_title"] == "meta_invalido"  # stem do arquivo

    def test_modified_at_presente(self, tmp_vault):
        note = tmp_vault / "simples.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert chunks[0]["modified_at"] != ""
        # Formato ISO
        assert "T" in chunks[0]["modified_at"]

    def test_chunks_sem_texto_vazio(self, tmp_vault):
        """Nenhum chunk deve ter texto vazio."""
        note = tmp_vault / "simples.md"
        chunks, links, aliases = parse_note(note, tmp_vault)
        for chunk in chunks:
            assert chunk["text"].strip() != ""

    def test_nota_so_frontmatter(self, tmp_vault):
        """Nota com frontmatter mas sem corpo deve retornar tupla com lista vazia."""
        note = tmp_vault / "so_meta.md"
        note.write_text("---\ntitle: Vazio\ntags: teste\n---\n", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert chunks == []

    def test_nota_so_whitespace(self, tmp_vault):
        """Nota com apenas espaços em branco deve retornar tupla com lista vazia."""
        note = tmp_vault / "whitespace.md"
        note.write_text("   \n  \n  \n", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert chunks == []

    def test_nota_subpasta_profunda(self, tmp_vault):
        """Nota em subpasta profunda deve ter folder correto."""
        deep = tmp_vault / "a" / "b" / "c"
        deep.mkdir(parents=True)
        note = deep / "profunda.md"
        note.write_text("# Deep\n\nTexto profundo.", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert chunks[0]["folder"] == "a/b/c"
        assert chunks[0]["note_path"] == "a/b/c/profunda.md"


# === Edge cases: frontmatter e headers ===


class TestParserEdgeCases:
    def test_frontmatter_com_multiplos_separadores(self):
        """Múltiplos --- no corpo não devem confundir o parser."""
        content = "---\ntitle: test\n---\n# Corpo\n\n---\n\nTexto após hr."
        meta, body = parse_frontmatter(content)
        assert meta.get("title") == "test"
        assert "---" in body or "Texto após hr" in body

    def test_frontmatter_com_unicode(self):
        """Frontmatter com valores Unicode deve funcionar."""
        content = "---\ntitle: Café Résumé\ntags:\n  - 日本語\n---\nCorpo."
        meta, body = parse_frontmatter(content)
        assert meta.get("title") == "Café Résumé"
        assert "日本語" in meta.get("tags", [])

    def test_header_nivel_profundo(self):
        """Headers h1-h4 são splitados (MARKDOWN_HEADER_LEVELS=4), h5+ não."""
        text = "# H1\n\n## H2\n\nTexto H2.\n\n### H3\n\nTexto H3.\n\n#### H4\n\nTexto H4."
        sections = split_by_headers(text)
        assert len(sections) >= 4

    def test_tags_como_none(self):
        """tags: null no YAML deve retornar lista vazia."""
        assert extract_tags({"tags": None}) == []

    def test_tags_lista_aninhada(self):
        """Tags com sublistas devem ser achatadas ou ignoradas."""
        result = extract_tags({"tags": [["nested"]]})
        # Deve lidar graciosamente, não crashear
        assert isinstance(result, list)

    def test_tags_booleano(self):
        """Tags como booleano deve retornar vazio."""
        assert extract_tags({"tags": True}) == []

    def test_parse_note_arquivo_inexistente(self, tmp_vault):
        """parse_note deve retornar tupla com lista vazia se arquivo não existe."""
        path = tmp_vault / "nao_existe.md"
        chunks, links, aliases = parse_note(path, tmp_vault)
        assert chunks == []

    def test_title_como_int(self, tmp_vault):
        """Frontmatter com title: 123 deve converter para string."""
        note = tmp_vault / "title_int.md"
        note.write_text("---\ntitle: 123\n---\n# Corpo\n\nTexto.", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_title"] == "123"
        assert isinstance(chunks[0]["note_title"], str)

    def test_title_como_list(self, tmp_vault):
        """Frontmatter com title: [a, b] deve converter para string."""
        note = tmp_vault / "title_list.md"
        note.write_text("---\ntitle:\n  - a\n  - b\n---\n# Corpo\n\nTexto.", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert isinstance(chunks[0]["note_title"], str)

    def test_title_como_none(self, tmp_vault):
        """Frontmatter com title: null deve usar stem do arquivo."""
        note = tmp_vault / "title_null.md"
        note.write_text("---\ntitle: null\n---\n# Corpo\n\nTexto.", encoding="utf-8")
        chunks, links, aliases = parse_note(note, tmp_vault)
        assert len(chunks) >= 1
        assert chunks[0]["note_title"] == "title_null"
