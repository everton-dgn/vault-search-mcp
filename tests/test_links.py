"""
Tests for utils/links.py — extraction and analysis of links in markdown.
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
    """Tests for the regex WIKILINK_PATTERN."""

    def test_simple_wikilink(self):
        text = "See [[my note]] for details."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["my note"]

    def test_wikilink_with_alias(self):
        text = "See [[my note|alias]] for details."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["my note"]

    def test_wikilink_with_heading(self):
        text = "See [[my note#section]] for details."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["my note"]

    def test_wikilink_with_alias_and_heading(self):
        text = "See [[my note#section|alias]] for details."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["my note"]

    def test_wikilink_with_path(self):
        text = "See [[folder/subfolder/note]] here."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["folder/subfolder/note"]

    def test_multiple_wikilinks(self):
        text = "See [[note1]] and [[note2]] and [[note3]]."
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["note1", "note2", "note3"]

    def test_wikilink_multiline(self):
        text = "Line 1 [[note1]]\nLinha 2 [[note2]]"
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["note1", "note2"]

    def test_pattern_not_captures_embeds(self):
        """
        Regression test: WIKILINK_PATTERN must not capture embeds.

        Bug fixed: the pattern [[...]] captured wikilinks inside of embeds ![[...]].
        Solution: use negative lookbehind (?<!!) for exclude embeds.
        """
        text = "Image ![[image.png]] and link [[Note]]"
        matches = WIKILINK_PATTERN.findall(text)
        assert matches == ["Note"], f"BUG: obtained {matches}, expected ['Note']"


class TestMarkdownLinkPattern:
    """Tests for the regex MARKDOWN_LINK_PATTERN."""

    def test_simple_markdown_link(self):
        text = "See [text](note.md) for details."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == [("text", "note.md")]

    def test_markdown_link_without_extension(self):
        text = "See [text](note) for details."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == [("text", "note")]

    def test_markdown_link_with_path(self):
        text = "See [text](folder/note.md) here."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == [("text", "folder/note.md")]

    def test_ignores_url_http(self):
        text = "See [Google](https://google.com) external."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == []

    def test_ignores_url_http_without_s(self):
        text = "See [site](http://example.com) external."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == []

    def test_ignores_mailto(self):
        text = "Contact [email](mailto:test@test.com)."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == []

    def test_ignores_anchor(self):
        text = "See [section](#ancora) here."
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == []

    def test_multiple_links(self):
        text = "[a](note1.md) and [b](note2.md)"
        matches = MARKDOWN_LINK_PATTERN.findall(text)
        assert matches == [("a", "note1.md"), ("b", "note2.md")]


class TestEmbedPattern:
    """Tests for the regex EMBED_PATTERN."""

    def test_simple_embed(self):
        text = "Image: ![[image.png]]"
        matches = EMBED_PATTERN.findall(text)
        assert matches == ["image.png"]

    def test_embed_with_size(self):
        text = "Image: ![[image.png|500]]"
        matches = EMBED_PATTERN.findall(text)
        assert matches == ["image.png"]

    def test_embed_note(self):
        text = "Content: ![[my note]]"
        matches = EMBED_PATTERN.findall(text)
        assert matches == ["my note"]

    def test_embed_with_path(self):
        text = "![[assets/images/image.png]]"
        matches = EMBED_PATTERN.findall(text)
        assert matches == ["assets/images/image.png"]

    def test_multiple_embeds(self):
        text = "![[a.png]] and ![[b.png]]"
        matches = EMBED_PATTERN.findall(text)
        assert matches == ["a.png", "b.png"]


class TestExtractWikilinks:
    """Tests for extract_wikilinks()."""

    def test_text_empty(self):
        assert extract_wikilinks("") == []

    def test_none(self):
        assert extract_wikilinks(None) == []

    def test_without_links(self):
        assert extract_wikilinks("Text without links.") == []

    def test_extracts_unique_links(self):
        text = "[[note]] and [[note]] and [[NOTE]]"
        result = extract_wikilinks(text)
        # Case-insensitive deduplication, keeps first occurrence
        assert len(result) == 1
        assert result[0] == "note"

    def test_preserves_original_case(self):
        text = "[[My Note Importante]]"
        result = extract_wikilinks(text)
        assert result == ["My Note Importante"]

    def test_remove_extra_spaces(self):
        text = "[[  note  ]]"
        result = extract_wikilinks(text)
        assert result == ["note"]

    def test_not_captures_embeds(self):
        """
        Regression test: embeds (![[...]]) must not be captured as wikilinks.

        Bug fixed: the regex [[...]] captured wikilinks inside of embeds ![[...]].
        """
        text = "Image ![[image.png]] and link [[Note]]"
        result = extract_wikilinks(text)

        assert "image.png" not in result, "BUG: image.png must not be captured as wikilink"
        assert "Note" in result, "Note must be captured as wikilink"
        assert len(result) == 1, "Must have exactly 1 wikilink"


class TestExtractMarkdownLinks:
    """Tests for extract_markdown_links()."""

    def test_text_empty(self):
        assert extract_markdown_links("") == []

    def test_none(self):
        assert extract_markdown_links(None) == []

    def test_without_links(self):
        assert extract_markdown_links("Text without links.") == []

    def test_extracts_path(self):
        text = "[text](folder/note.md)"
        result = extract_markdown_links(text)
        assert result == ["folder/note.md"]

    def test_remove_anchor(self):
        text = "[text](note.md#section)"
        result = extract_markdown_links(text)
        assert result == ["note.md"]

    def test_deduplicates(self):
        text = "[a](note.md) and [b](note.md)"
        result = extract_markdown_links(text)
        assert len(result) == 1

    def test_ignores_external_links(self):
        text = "[local](note.md) and [external](https://google.com)"
        result = extract_markdown_links(text)
        assert result == ["note.md"]


class TestExtractEmbeds:
    """Tests for extract_embeds()."""

    def test_text_empty(self):
        assert extract_embeds("") == []

    def test_none(self):
        assert extract_embeds(None) == []

    def test_without_embeds(self):
        assert extract_embeds("Text without embeds.") == []

    def test_extracts_embeds(self):
        text = "![[image.png]] and ![[note]]"
        result = extract_embeds(text)
        assert "image.png" in result
        assert "note" in result

    def test_deduplicates(self):
        text = "![[img.png]] and ![[IMG.PNG]]"
        result = extract_embeds(text)
        assert len(result) == 1


class TestExtractAllLinks:
    """Tests for extract_all_links()."""

    def test_text_empty(self):
        result = extract_all_links("")
        assert result == {"wikilinks": [], "markdown_links": [], "embeds": []}

    def test_all_types(self):
        text = """
        Wikilink: [[note1]]
        Markdown: [text](note2.md)
        Embed: ![[image.png]]
        """
        result = extract_all_links(text)

        # Wikilinks now are dicts with field 'target'
        wikilink_targets = [w["target"] for w in result["wikilinks"]]
        assert "note1" in wikilink_targets

        # Markdown links are dicts with field 'target'
        markdown_targets = [m["target"] for m in result["markdown_links"]]
        assert "note2.md" in markdown_targets

        # Embeds are dicts with field 'target'
        embed_targets = [e["target"] for e in result["embeds"]]
        assert "image.png" in embed_targets

    def test_return_structure(self):
        result = extract_all_links("test")
        assert "wikilinks" in result
        assert "markdown_links" in result
        assert "embeds" in result
        assert isinstance(result["wikilinks"], list)
        assert isinstance(result["markdown_links"], list)
        assert isinstance(result["embeds"], list)

    def test_wikilink_dict_structure(self):
        """Checks that wikilinks return structure complete."""
        result = extract_all_links("Link for [[Note#Heading|alias]]")
        assert len(result["wikilinks"]) == 1
        wl = result["wikilinks"][0]
        assert wl["target"] == "Note"
        assert wl["alias"] == "alias"
        assert wl["heading"] == "Heading"
        assert wl["raw"] == "[[Note#Heading|alias]]"

    def test_markdown_link_dict_structure(self):
        """Checks that markdown links return structure complete."""
        result = extract_all_links("[text of the link](path/to/note.md)")
        assert len(result["markdown_links"]) == 1
        ml = result["markdown_links"][0]
        assert ml["target"] == "path/to/note.md"
        assert ml["text"] == "text of the link"

    def test_embed_dict_structure(self):
        """Checks that embeds return structure complete."""
        result = extract_all_links("Image: ![[image.png|400]]")
        assert len(result["embeds"]) == 1
        emb = result["embeds"][0]
        # target is extracted without the size
        assert emb["target"] == "image.png"
        assert "raw" in emb

    def test_embeds_not_appear_in_wikilinks(self):
        """
        Regression test: embeds (![[...]]) must not appear in wikilinks.

        Bug fixed: the regex of wikilinks captured [[...]] inside of ![[...]],
        causing duplication of the target in wikilinks and embeds.
        """
        text = "Image ![[image.png]] and link [[Note]]"
        result = extract_all_links(text)

        # Embeds only must appear in 'embeds', not in 'wikilinks'
        embed_targets = [e["target"] for e in result["embeds"]]
        wikilink_targets = [w["target"] for w in result["wikilinks"]]

        assert "image.png" in embed_targets, "image.png must be in embeds"
        assert "image.png" not in wikilink_targets, "BUG: image.png must not appear in wikilinks"
        assert "Note" in wikilink_targets, "Note must be in wikilinks"
        assert len(result["wikilinks"]) == 1, "Must have exactly 1 wikilink"
        assert len(result["embeds"]) == 1, "Must have exactly 1 embed"


class TestNormalizeLinkTarget:
    """Tests for normalize_link_target()."""

    def test_lowercase(self):
        assert normalize_link_target("NOTE") == "note"

    def test_remove_extension_md(self):
        assert normalize_link_target("note.md") == "note"

    def test_remove_extension_MD_uppercase(self):
        assert normalize_link_target("note.MD") == "note"

    def test_strip_spaces(self):
        assert normalize_link_target("  note  ") == "note"

    def test_path_with_extension(self):
        assert normalize_link_target("folder/note.md") == "folder/note"

    def test_without_extension(self):
        assert normalize_link_target("note") == "note"


class TestMatchesNote:
    """Tests for matches_note()."""

    def test_exact_name_match(self):
        assert matches_note("my-note", "folder/my-note.md") is True

    def test_match_case_insensitive(self):
        assert matches_note("My-Note", "folder/my-note.md") is True

    def test_match_with_extension(self):
        assert matches_note("my-note.md", "folder/my-note.md") is True

    def test_match_complete_path(self):
        assert matches_note("folder/my-note", "folder/my-note.md") is True

    def test_matches_spaces_to_hyphens(self):
        assert matches_note("my note", "folder/my-note.md") is True

    def test_matches_hyphens_to_spaces(self):
        assert matches_note("my-note", "folder/my note.md") is True

    def test_does_not_match_different_text(self):
        assert matches_note("other-note", "folder/my-note.md") is False

    def test_not_match_partial(self):
        # "note" must not give match in "my-note"
        assert matches_note("note", "folder/my-note.md") is False

    def test_match_complete_filename(self):
        assert matches_note("my-note.md", "my-note.md") is True

    def test_match_subfolder(self):
        # Match partial: "sub/note" corresponds a "folder/sub/note.md"
        # because the path ends with "/sub/note"
        assert matches_note("sub/note", "folder/sub/note.md") is True
        assert matches_note("folder/sub/note", "folder/sub/note.md") is True
        # But "other/note" not corresponds
        assert matches_note("other/note", "folder/sub/note.md") is False


class TestRealWorldCases:
    """Tests with real-world Obsidian cases."""

    def test_obsidian_daily_note(self):
        text = "See [[2024-01-15]] for context."
        result = extract_wikilinks(text)
        assert result == ["2024-01-15"]

    def test_obsidian_heading_link(self):
        text = "See [[Project#Requisitos]] for details."
        result = extract_wikilinks(text)
        assert result == ["Project"]

    def test_obsidian_block_reference(self):
        text = "See [[Note^abc123]] for reference."
        # Block references use ^ before the ID.
        # The regex must capture the note name.
        result = WIKILINK_PATTERN.findall(text)
        # The ^ is not in the default, then captures "Note^abc123"
        # This may need adjustment if block references should be ignored.
        assert len(result) == 1

    def test_mixed_links_document(self):
        text = """
# My Document

This document references [[Project A]] and [[Project B|PB]].

For more information, see [documentation](docs/manual.md).

Images: ![[diagram.png|500]]

External links are ignored: [Google](https://google.com)
"""
        result = extract_all_links(text)

        # Extract targets of the wikilinks
        wikilink_targets = [w["target"] for w in result["wikilinks"]]
        assert "Project A" in wikilink_targets
        assert "Project B" in wikilink_targets

        # Extract targets of the markdown links
        markdown_targets = [m["target"] for m in result["markdown_links"]]
        assert "docs/manual.md" in markdown_targets

        # Extract targets of the embeds
        embed_targets = [e["target"] for e in result["embeds"]]
        assert "diagram.png" in embed_targets

        # Google was ignored (is external)
        assert len(result["markdown_links"]) == 1
