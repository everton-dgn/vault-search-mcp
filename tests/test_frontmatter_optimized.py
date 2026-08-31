"""
Testes para leitura otimizada de frontmatter.
"""

from pathlib import Path

from vault_search.parsers.frontmatter import read_frontmatter_only


class TestReadFrontmatterOnly:
    """Testes para read_frontmatter_only."""

    def test_arquivo_com_frontmatter(self, tmp_path: Path):
        content = """---
title: Test Note
tags: [python, testing]
---

# Body content
Some text here.
"""
        path = tmp_path / "with-frontmatter.md"
        path.write_text(content)

        metadata, bytes_read = read_frontmatter_only(path)

        assert metadata["title"] == "Test Note"
        assert metadata["tags"] == ["python", "testing"]
        assert bytes_read > 0
        # Para arquivos pequenos, pode ler tudo em um chunk
        assert bytes_read <= len(content.encode("utf-8"))

    def test_arquivo_sem_frontmatter(self, tmp_path: Path):
        content = """# Just a heading

Normal markdown content.
"""
        path = tmp_path / "without-frontmatter.md"
        path.write_text(content)

        metadata, bytes_read = read_frontmatter_only(path)

        assert metadata == {}
        assert bytes_read > 0

    def test_arquivo_com_bom(self, tmp_path: Path):
        content = "\ufeff---\ntitle: BOM Test\n---\nBody"
        path = tmp_path / "with-bom.md"
        path.write_text(content, encoding="utf-8")

        metadata, _ = read_frontmatter_only(path)

        assert metadata["title"] == "BOM Test"

    def test_arquivo_vazio(self, tmp_path: Path):
        path = tmp_path / "empty.md"
        path.write_text("")

        metadata, bytes_read = read_frontmatter_only(path)

        assert metadata == {}
        assert bytes_read == 0

    def test_frontmatter_sem_fechamento(self, tmp_path: Path):
        """Frontmatter aberto sem --- de fechamento."""
        content = """---
title: Incomplete
tags: [test]

Body without closing delimiter.
"""
        path = tmp_path / "unclosed-frontmatter.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        assert metadata == {}  # Não é frontmatter válido

    def test_frontmatter_yaml_invalido(self, tmp_path: Path):
        content = """---
title: [Invalid YAML
missing: bracket
---

Body
"""
        path = tmp_path / "invalid-yaml.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        assert metadata == {}  # YAML inválido

    def test_frontmatter_nao_dict(self, tmp_path: Path):
        """YAML válido mas não é dict."""
        content = """---
- item1
- item2
---

Body
"""
        path = tmp_path / "list-frontmatter.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        assert metadata == {}  # Lista não é válido

    def test_dash_no_meio_do_arquivo(self, tmp_path: Path):
        """--- no meio do corpo não deve confundir."""
        content = """---
title: Test
---

Some text
---
More text after dashes
"""
        path = tmp_path / "body-dashes.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        assert metadata["title"] == "Test"

    def test_frontmatter_grande_multiplos_chunks(self, tmp_path: Path):
        """Frontmatter maior que um chunk."""
        # Criar frontmatter com muitas linhas
        lines = ["---"]
        for i in range(100):
            lines.append(f"key{i}: value{i}")
        lines.append("---")
        lines.append("Body")
        content = "\n".join(lines)

        path = tmp_path / "large-frontmatter.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        assert "key0" in metadata
        assert "key99" in metadata
        assert metadata["key50"] == "value50"

    def test_arquivo_inexistente(self, tmp_path: Path):
        path = tmp_path / "missing.md"
        metadata, bytes_read = read_frontmatter_only(path)

        assert metadata == {}
        assert bytes_read == 0

    def test_whitespace_antes_do_frontmatter(self, tmp_path: Path):
        """Whitespace antes de --- invalida frontmatter."""
        content = """   ---
title: Test
---

Body
"""
        path = tmp_path / "leading-whitespace.md"
        path.write_text(content)

        metadata, _ = read_frontmatter_only(path)

        # Espaços antes do primeiro --- invalidam
        assert metadata == {}

    def test_unicode_no_frontmatter(self, tmp_path: Path):
        content = """---
title: Título com Acentos
tags: [português, 日本語, émojis 🎉]
---

Body with unicode: café ☕
"""
        path = tmp_path / "unicode.md"
        path.write_text(content, encoding="utf-8")

        metadata, _ = read_frontmatter_only(path)

        assert metadata["title"] == "Título com Acentos"
        assert "português" in metadata["tags"]


class TestReadFrontmatterPerformance:
    """Testes de que a leitura é realmente parcial."""

    def test_nao_le_corpo_grande(self, tmp_path: Path):
        """Não deve ler todo o corpo se for grande."""
        # Frontmatter pequeno + corpo muito grande
        large_body = "x" * (100 * 1024)  # 100KB de corpo
        content = f"""---
title: Small Frontmatter
---

{large_body}
"""
        path = tmp_path / "large-body.md"
        path.write_text(content)

        metadata, bytes_read = read_frontmatter_only(path)

        assert metadata["title"] == "Small Frontmatter"
        # Deve ter lido muito menos que o arquivo total
        total_size = len(content.encode("utf-8"))
        assert bytes_read < total_size / 2  # Menos da metade
