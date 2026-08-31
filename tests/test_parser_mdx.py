"""
Unit tests for parser of MDX.
"""

from vault_search.parsers.mdx import clean_mdx, parse_mdx


class TestCleanMdx:
    def test_remove_import_default(self):
        content = "import Button from './Button'\n\n# Hello"
        assert "import" not in clean_mdx(content)
        assert "# Hello" in clean_mdx(content)

    def test_remove_import_named(self):
        content = "import { Button, Card } from './components'\n\n# Hello"
        assert "import" not in clean_mdx(content)

    def test_remove_export_default(self):
        content = "# Hello\n\nexport default Component"
        assert "export" not in clean_mdx(content)
        assert "# Hello" in clean_mdx(content)

    def test_remove_export_const(self):
        content = "export const meta = { title: 'Test' }\n\n# Hello"
        assert "export" not in clean_mdx(content)

    def test_remove_jsx_self_closing(self):
        content = "# Hello\n\n<Button onClick={handleClick} />\n\nMore text"
        cleaned = clean_mdx(content)
        assert "<Button" not in cleaned
        assert "More text" in cleaned

    def test_remove_jsx_block(self):
        content = "# Hello\n\n<Card>\n  Some content\n</Card>\n\nMore text"
        cleaned = clean_mdx(content)
        assert "<Card>" not in cleaned
        assert "</Card>" not in cleaned
        assert "More text" in cleaned

    def test_remove_nested_jsx(self):
        content = "<Layout>\n  <Header />\n  <Content>text</Content>\n</Layout>"
        cleaned = clean_mdx(content)
        assert "<Layout>" not in cleaned
        assert "<Header" not in cleaned

    def test_preserves_markdown(self):
        content = """---
title: Test
---

import { Button } from './ui'

# Main Title

Some paragraph text.

<Button>Click</Button>

## Section

More content here.
"""
        cleaned = clean_mdx(content)
        assert "---" in cleaned  # frontmatter preserved
        assert "title: Test" in cleaned
        assert "# Main Title" in cleaned
        assert "Some paragraph text." in cleaned
        assert "## Section" in cleaned
        assert "More content here." in cleaned
        assert "import" not in cleaned
        assert "<Button" not in cleaned

    def test_preserves_html_lowercase(self):
        """HTML tags in lowercase (<div>, <span>) must be preserved."""
        content = "# Hello\n\n<div>content</div>"
        cleaned = clean_mdx(content)
        assert "<div>" in cleaned

    def test_empty_content(self):
        assert clean_mdx("") == ""

    def test_in_jsx(self):
        content = "# Just Markdown\n\nNo JSX here."
        assert clean_mdx(content) == content


class TestParseMdx:
    def test_parse_simple_mdx(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        mdx_file = vault / "test.mdx"
        mdx_file.write_text(
            """---
title: Test MDX
tags: [mdx, test]
---

import { Button } from './ui'

# Introduction

This is a test MDX file.

<Button>Click me</Button>

## Features

- Feature 1
- Feature 2
""",
            encoding="utf-8",
        )

        chunks, links, aliases = parse_mdx(mdx_file, vault)

        assert len(chunks) > 0
        assert chunks[0]["note_title"] == "Test MDX"
        assert "mdx" in chunks[0]["tags"]

        # Checks that JSX was removed of the text
        all_text = " ".join(c["text"] for c in chunks)
        assert "<Button" not in all_text
        assert "import" not in all_text
        assert "Introduction" in all_text
        assert "Features" in all_text

    def test_parse_mdx_in_frontmatter(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        mdx_file = vault / "simple.mdx"
        mdx_file.write_text("# Hello World\n\nSome content.", encoding="utf-8")

        chunks, links, aliases = parse_mdx(mdx_file, vault)

        assert len(chunks) > 0
        assert chunks[0]["note_title"] == "simple"  # stem of the file

    def test_parse_mdx_file_not_found(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        fake_path = vault / "not_exists.mdx"

        chunks, links, aliases = parse_mdx(fake_path, vault)
        assert chunks == []

    def test_parse_mdx_headers_extracted(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        mdx_file = vault / "headers.mdx"
        mdx_file.write_text(
            """# Main

Text under main.

## Sub

Text under sub.
""",
            encoding="utf-8",
        )

        chunks, links, aliases = parse_mdx(mdx_file, vault)

        # Checks that headers were extracted
        headers_found = [c["headers"] for c in chunks]
        assert any("Main" in h for h in headers_found)
        assert any("Sub" in h for h in headers_found)
