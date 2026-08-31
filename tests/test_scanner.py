"""
Testes unitários para scanner.py — escaneamento do vault.

Testes rápidos que NÃO precisam de modelos ML nem LanceDB.
"""

from vault_search.core.scanner import scan_vault


class TestScanVault:
    def test_encontra_notas_md(self, tmp_vault):
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "simples.md" in names
        assert "com_meta.md" in names
        assert "projeto1.md" in names

    def test_encontra_canvas(self, tmp_vault):
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "diagrama.canvas" in names

    def test_encontra_pdf(self, tmp_vault):
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "documento.pdf" in names

    def test_encontra_txt(self, tmp_vault):
        """Arquivo .txt deve ser indexável."""
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "readme.txt" in names

    def test_ignora_nao_indexavel(self, tmp_vault):
        """Arquivos não indexáveis (.jpg, .png, etc) devem ser ignorados."""
        (tmp_vault / "image.jpg").write_bytes(b"fake image")
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "image.jpg" not in names

    def test_ignora_pastas_ignoradas(self, tmp_vault):
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "config.md" not in names  # dentro de .obsidian

    def test_extensao_case_insensitive(self, tmp_vault):
        """Nota com .MD deve ser encontrada."""
        (tmp_vault / "upper.MD").write_text("# Upper", encoding="utf-8")
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "upper.MD" in names

    def test_ignora_frontmatter_invalido_gracefully(self, tmp_vault):
        """meta_invalido.md tem YAML lista — deve ser encontrado pelo scanner."""
        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "meta_invalido.md" in names

    def test_symlink_fora_do_vault(self, tmp_vault, tmp_path):
        """Symlink apontando para fora do vault deve ser ignorado."""
        external = tmp_path / "external.md"
        external.write_text("# External", encoding="utf-8")
        link = tmp_vault / "link_externo.md"
        link.symlink_to(external)

        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "link_externo.md" not in names

    def test_symlink_dentro_do_vault(self, tmp_vault):
        """Symlink apontando para dentro do vault deve ser incluído."""
        target = tmp_vault / "simples.md"
        link = tmp_vault / "link_interno.md"
        link.symlink_to(target)

        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "link_interno.md" in names

    def test_vault_vazio(self, tmp_path):
        """Vault sem arquivos indexáveis deve retornar lista vazia."""
        vault = tmp_path / "empty_vault"
        vault.mkdir()
        (vault / "image.jpg").write_bytes(b"fake image")

        files = scan_vault(vault)
        assert files == []

    def test_symlink_quebrado(self, tmp_vault):
        """Symlink apontando para arquivo inexistente deve ser ignorado."""
        link = tmp_vault / "broken_link.md"
        link.symlink_to(tmp_vault / "nao_existe.md")

        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "broken_link.md" not in names

    def test_subpastas_multiplas(self, tmp_vault):
        """Deve encontrar notas em subpastas recursivamente."""
        deep = tmp_vault / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "deep.md").write_text("# Deep", encoding="utf-8")

        files = scan_vault(tmp_vault)
        names = [f.name for f in files]
        assert "deep.md" in names

    def test_ignora_multiplas_pastas(self, tmp_vault):
        """Todas as pastas ignoradas devem ser excluídas."""
        for folder in [".smart-env", ".trash"]:
            d = tmp_vault / folder
            d.mkdir(exist_ok=True)
            (d / "note.md").write_text("# Ignore", encoding="utf-8")

        files = scan_vault(tmp_vault)
        paths = [str(f) for f in files]
        for folder in [".smart-env", ".trash"]:
            assert not any(folder in p for p in paths)
