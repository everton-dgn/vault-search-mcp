"""
Unit tests for parse_canvas.py — parser of Canvas.

Fast tests that do not require ML models or LanceDB.
"""

import json
from pathlib import Path

from vault_search.parsers.canvas import parse_canvas


def _write_canvas(vault: Path, name: str, data: dict) -> Path:
    """Helper: writes a file .canvas in the vault."""
    path = vault / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestParseCanvasTextNodes:
    def test_simple_text_node(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "text",
                    "text": "Hello World",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                }
            ],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "test.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "Hello World"
        assert chunks[0]["note_path"] == "test.canvas"
        assert chunks[0]["note_title"] == "test"
        assert chunks[0]["tags"] == ""

    def test_multiple_text_nodes(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "text",
                    "text": "First",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                },
                {
                    "id": "n2",
                    "type": "text",
                    "text": "Second",
                    "x": 300,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                },
            ],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "multi.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 2
        texts = {c["text"] for c in chunks}
        assert texts == {"First", "Second"}

    def test_empty_text_node_is_ignored(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "text",
                    "text": "",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                },
                {
                    "id": "n2",
                    "type": "text",
                    "text": "   ",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                },
            ],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "empty.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 0

    def test_text_node_with_markdown(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "text",
                    "text": "# Title\n\nText with **bold** and *italic*.",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                }
            ],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "md.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 1
        assert "**bold**" in chunks[0]["text"]

    def test_long_text_node_is_chunked(self, tmp_vault):
        """Text larger than CHUNK_SIZE must be split into multiple chunks."""
        long_text = "Paragraph of test. " * 200  # ~4000 chars
        data = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "text",
                    "text": long_text,
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                }
            ],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "long.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) > 1
        for c in chunks:
            assert c["headers"] == "Text node: n1"

    def test_text_node_unicode(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "text",
                    "text": "Unicode sample: áéíóú ãõ ç",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                }
            ],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "unicode.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 1
        assert "ç" in chunks[0]["text"]


class TestParseCanvasGroupNodes:
    def test_group_label(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "g1",
                    "type": "group",
                    "label": "My Group",
                    "x": 0,
                    "y": 0,
                    "width": 400,
                    "height": 400,
                }
            ],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "group.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "My Group"
        assert chunks[0]["headers"] == "Group: My Group"

    def test_group_without_label_is_ignored(self, tmp_vault):
        data = {
            "nodes": [{"id": "g1", "type": "group", "x": 0, "y": 0, "width": 400, "height": 400}],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "nogroup.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 0


class TestParseCanvasEdges:
    def test_edge_with_label(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "text",
                    "text": "A",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                },
                {
                    "id": "n2",
                    "type": "text",
                    "text": "B",
                    "x": 300,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                },
            ],
            "edges": [{"id": "e1", "fromNode": "n1", "toNode": "n2", "label": "depends on"}],
        }
        path = _write_canvas(tmp_vault, "edges.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        edge_chunks = [c for c in chunks if "Edge:" in c["headers"]]
        assert len(edge_chunks) == 1
        assert edge_chunks[0]["text"] == "depends on"
        assert "n1" in edge_chunks[0]["headers"]
        assert "n2" in edge_chunks[0]["headers"]

    def test_edge_without_label_is_ignored(self, tmp_vault):
        data = {
            "nodes": [],
            "edges": [{"id": "e1", "fromNode": "n1", "toNode": "n2"}],
        }
        path = _write_canvas(tmp_vault, "noedge.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 0


class TestParseCanvasIgnoredNodes:
    def test_file_node_is_ignored(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "f1",
                    "type": "file",
                    "file": "notes/sample.md",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                }
            ],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "file.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 0

    def test_link_node_is_ignored(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "l1",
                    "type": "link",
                    "url": "https://example.com",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                }
            ],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "link.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 0


class TestParseCanvasEdgeCases:
    def test_json_invalid(self, tmp_vault):
        path = tmp_vault / "bad.canvas"
        path.write_text("{invalid json", encoding="utf-8")
        chunks = parse_canvas(path, tmp_vault)
        assert chunks == []

    def test_canvas_empty(self, tmp_vault):
        data = {"nodes": [], "edges": []}
        path = _write_canvas(tmp_vault, "empty.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert chunks == []

    def test_canvas_without_nodes_key(self, tmp_vault):
        data = {"other": "data"}
        path = _write_canvas(tmp_vault, "nokeys.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert chunks == []

    def test_node_without_type(self, tmp_vault):
        data = {"nodes": [{"id": "n1", "text": "orphan"}], "edges": []}
        path = _write_canvas(tmp_vault, "notype.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert chunks == []

    def test_subfolder(self, tmp_vault):
        sub = tmp_vault / "diagrams"
        sub.mkdir()
        data = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "text",
                    "text": "In subfolder",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                }
            ],
            "edges": [],
        }
        path = _write_canvas(sub, "diagram.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 1
        assert chunks[0]["folder"] == "diagrams"
        assert chunks[0]["note_path"] == "diagrams/diagram.canvas"

    def test_non_dict_node_is_ignored(self, tmp_vault):
        """Nodes that are not dict must be ignored."""
        data = {
            "nodes": [
                "string instead of dict",
                123,
                None,
                {
                    "id": "n1",
                    "type": "text",
                    "text": "Valid",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                },
            ],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "mixed.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "Valid"

    def test_non_dict_edge_is_ignored(self, tmp_vault):
        """Edges that are not dict must be ignored."""
        data = {
            "nodes": [],
            "edges": [
                "string edge",
                None,
                {"id": "e1", "fromNode": "n1", "toNode": "n2", "label": "valid"},
            ],
        }
        path = _write_canvas(tmp_vault, "bad_edges.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "valid"

    def test_file_nonexistent(self, tmp_vault):
        """A missing file returns an empty list."""
        path = tmp_vault / "does_not_exist.canvas"
        chunks = parse_canvas(path, tmp_vault)
        assert chunks == []
