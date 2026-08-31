"""Fixtures compartilhadas para testes do vault-search-mcp."""

import sys
from pathlib import Path

import pytest

# Garantir que src está no sys.path para imports
SRC_DIR = Path(__file__).parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def sample_markdown_simple():
    """Markdown simples sem frontmatter."""
    return "# Título\n\nParágrafo de texto simples.\n\n## Subtítulo\n\nOutro parágrafo."


@pytest.fixture
def sample_markdown_with_frontmatter():
    """Markdown com frontmatter YAML válido."""
    return (
        "---\n"
        "title: Minha Nota\n"
        "tags:\n"
        "  - python\n"
        "  - obsidian\n"
        "---\n"
        "# Conteúdo\n\n"
        "Texto da nota com **markdown**."
    )


@pytest.fixture
def sample_markdown_scalar_frontmatter():
    """Markdown com frontmatter que retorna escalar (não dict)."""
    return "---\napenas uma string\n---\nCorpo da nota."


@pytest.fixture
def sample_markdown_list_frontmatter():
    """Markdown com frontmatter que retorna lista (não dict)."""
    return "---\n- item1\n- item2\n---\nCorpo da nota."


@pytest.fixture
def sample_long_text():
    """Texto longo que precisa de chunking (~5000 chars)."""
    paragraphs = []
    for i in range(25):
        paragraphs.append(
            f"Parágrafo {i}: Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            f"Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
            f"Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris."
        )
    return "\n\n".join(paragraphs)


@pytest.fixture
def tmp_vault(tmp_path):
    """Cria vault temporário com algumas notas para testes."""
    vault = tmp_path / "test_vault"
    vault.mkdir()

    # Nota simples
    (vault / "simples.md").write_text(
        "# Nota Simples\n\nTexto de teste.",
        encoding="utf-8",
    )

    # Nota com frontmatter
    (vault / "com_meta.md").write_text(
        "---\ntitle: Nota com Meta\ntags:\n  - teste\n  - python\n---\n"
        "# Conteúdo\n\nTexto com metadados.",
        encoding="utf-8",
    )

    # Nota em subpasta
    subdir = vault / "projetos"
    subdir.mkdir()
    (subdir / "projeto1.md").write_text(
        "---\ntitle: Projeto 1\ntags: projeto\n---\n# Projeto 1\n\nDescrição do projeto.",
        encoding="utf-8",
    )

    # Nota com frontmatter inválido (lista)
    (vault / "meta_invalido.md").write_text(
        "---\n- item1\n- item2\n---\nCorpo sem meta válido.",
        encoding="utf-8",
    )

    # Arquivo não-markdown (deve ser ignorado)
    (vault / "readme.txt").write_text("Ignorar este arquivo.", encoding="utf-8")

    # Canvas simples
    import json

    canvas_data = {
        "nodes": [
            {
                "id": "n1",
                "type": "text",
                "text": "Conteúdo do canvas",
                "x": 0,
                "y": 0,
                "width": 200,
                "height": 100,
            }
        ],
        "edges": [],
    }
    (vault / "diagrama.canvas").write_text(json.dumps(canvas_data), encoding="utf-8")

    # PDF simples
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Conteúdo do PDF de teste")
    doc.save(str(vault / "documento.pdf"))
    doc.close()

    # Pasta ignorada
    ignored = vault / ".obsidian"
    ignored.mkdir()
    (ignored / "config.md").write_text("Deve ser ignorado.", encoding="utf-8")

    return vault
