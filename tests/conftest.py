"""Shared fixtures for vault-search-mcp tests."""

import sys
from pathlib import Path

import pytest

# Ensure src is on sys.path for imports.
SRC_DIR = Path(__file__).parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def sample_markdown_simple():
    """Simple Markdown without frontmatter."""
    return "# Title\n\nA simple text paragraph.\n\n## Subtitle\n\nAnother paragraph."


@pytest.fixture
def sample_markdown_with_frontmatter():
    """Markdown with valid YAML frontmatter."""
    return (
        "---\n"
        "title: My Note\n"
        "tags:\n"
        "  - python\n"
        "  - obsidian\n"
        "---\n"
        "# Content\n\n"
        "Note text with **markdown**."
    )


@pytest.fixture
def sample_markdown_scalar_frontmatter():
    """Markdown whose frontmatter parses as a scalar instead of a mapping."""
    return "---\njust a scalar\n---\nBody of the note."


@pytest.fixture
def sample_markdown_list_frontmatter():
    """Markdown whose frontmatter parses as a list instead of a mapping."""
    return "---\n- item1\n- item2\n---\nBody of the note."


@pytest.fixture
def sample_long_text():
    """Long text that requires chunking (about 5,000 characters)."""
    paragraphs = []
    for i in range(25):
        paragraphs.append(
            f"Paragraph {i}: Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            f"Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
            f"Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris."
        )
    return "\n\n".join(paragraphs)


@pytest.fixture
def tmp_vault(tmp_path):
    """Create a temporary vault with synthetic notes for tests."""
    vault = tmp_path / "test_vault"
    vault.mkdir()

    # Simple note
    (vault / "simple.md").write_text(
        "# Simple Note\n\nTest text.",
        encoding="utf-8",
    )

    # Note with frontmatter
    (vault / "with_meta.md").write_text(
        "---\ntitle: Note with Meta\ntags:\n  - test\n  - python\n---\n"
        "# Content\n\nText with metadata.",
        encoding="utf-8",
    )

    # Note in subfolder
    subdir = vault / "projects"
    subdir.mkdir()
    (subdir / "project1.md").write_text(
        "---\ntitle: Project 1\ntags: project\n---\n# Project 1\n\nDescription of the project.",
        encoding="utf-8",
    )

    # Note with frontmatter invalid (list)
    (vault / "meta_invalid.md").write_text(
        "---\n- item1\n- item2\n---\nBody without valid metadata.",
        encoding="utf-8",
    )

    # Non-Markdown file.
    (vault / "readme.txt").write_text("Ignore this file.", encoding="utf-8")

    # Simple Canvas file
    import json

    canvas_data = {
        "nodes": [
            {
                "id": "n1",
                "type": "text",
                "text": "Content of the canvas",
                "x": 0,
                "y": 0,
                "width": 200,
                "height": 100,
            }
        ],
        "edges": [],
    }
    (vault / "diagram.canvas").write_text(json.dumps(canvas_data), encoding="utf-8")

    # Simple PDF.
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Test PDF content")
    doc.save(str(vault / "document.pdf"))
    doc.close()

    # Ignored folder.
    ignored = vault / ".obsidian"
    ignored.mkdir()
    (ignored / "config.md").write_text("Must be ignored.", encoding="utf-8")

    return vault
