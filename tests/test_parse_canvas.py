"""
Testes unitários para parse_canvas.py — parser de Canvas.

Testes rápidos que NÃO precisam de modelos ML nem LanceDB.
"""

import json
from pathlib import Path

from vault_search.parsers.canvas import parse_canvas


def _write_canvas(vault: Path, name: str, data: dict) -> Path:
    """Helper: escreve um arquivo .canvas no vault."""
    path = vault / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestParseCanvasTextNodes:
    def test_text_node_simples(self, tmp_vault):
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

    def test_multiplos_text_nodes(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "text",
                    "text": "Primeiro",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 100,
                },
                {
                    "id": "n2",
                    "type": "text",
                    "text": "Segundo",
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
        assert texts == {"Primeiro", "Segundo"}

    def test_text_node_vazio_ignorado(self, tmp_vault):
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

    def test_text_node_com_markdown(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "text",
                    "text": "# Título\n\nTexto com **bold** e *italic*.",
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

    def test_text_node_longo_chunked(self, tmp_vault):
        """Texto > CHUNK_SIZE deve ser dividido em múltiplos chunks."""
        long_text = "Parágrafo de teste. " * 200  # ~4000 chars
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
                    "text": "Programação em português: áéíóú ãõ ç",
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
                    "label": "Meu Grupo",
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
        assert chunks[0]["text"] == "Meu Grupo"
        assert chunks[0]["headers"] == "Group: Meu Grupo"

    def test_group_sem_label_ignorado(self, tmp_vault):
        data = {
            "nodes": [{"id": "g1", "type": "group", "x": 0, "y": 0, "width": 400, "height": 400}],
            "edges": [],
        }
        path = _write_canvas(tmp_vault, "nogroup.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 0


class TestParseCanvasEdges:
    def test_edge_com_label(self, tmp_vault):
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
            "edges": [{"id": "e1", "fromNode": "n1", "toNode": "n2", "label": "depende de"}],
        }
        path = _write_canvas(tmp_vault, "edges.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        edge_chunks = [c for c in chunks if "Edge:" in c["headers"]]
        assert len(edge_chunks) == 1
        assert edge_chunks[0]["text"] == "depende de"
        assert "n1" in edge_chunks[0]["headers"]
        assert "n2" in edge_chunks[0]["headers"]

    def test_edge_sem_label_ignorado(self, tmp_vault):
        data = {
            "nodes": [],
            "edges": [{"id": "e1", "fromNode": "n1", "toNode": "n2"}],
        }
        path = _write_canvas(tmp_vault, "noedge.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 0


class TestParseCanvasIgnoredNodes:
    def test_file_node_ignorado(self, tmp_vault):
        data = {
            "nodes": [
                {
                    "id": "f1",
                    "type": "file",
                    "file": "notas/algo.md",
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

    def test_link_node_ignorado(self, tmp_vault):
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
    def test_json_invalido(self, tmp_vault):
        path = tmp_vault / "bad.canvas"
        path.write_text("{invalid json", encoding="utf-8")
        chunks = parse_canvas(path, tmp_vault)
        assert chunks == []

    def test_canvas_vazio(self, tmp_vault):
        data = {"nodes": [], "edges": []}
        path = _write_canvas(tmp_vault, "empty.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert chunks == []

    def test_canvas_sem_nodes_key(self, tmp_vault):
        data = {"other": "data"}
        path = _write_canvas(tmp_vault, "nokeys.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert chunks == []

    def test_node_sem_type(self, tmp_vault):
        data = {"nodes": [{"id": "n1", "text": "orphan"}], "edges": []}
        path = _write_canvas(tmp_vault, "notype.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert chunks == []

    def test_subpasta(self, tmp_vault):
        sub = tmp_vault / "diagrams"
        sub.mkdir()
        data = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "text",
                    "text": "Em subpasta",
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

    def test_node_nao_dict_ignorado(self, tmp_vault):
        """Nodes que não são dict devem ser ignorados."""
        data = {
            "nodes": [
                "string instead of dict",
                123,
                None,
                {
                    "id": "n1",
                    "type": "text",
                    "text": "Válido",
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
        assert chunks[0]["text"] == "Válido"

    def test_edge_nao_dict_ignorado(self, tmp_vault):
        """Edges que não são dict devem ser ignorados."""
        data = {
            "nodes": [],
            "edges": [
                "string edge",
                None,
                {"id": "e1", "fromNode": "n1", "toNode": "n2", "label": "válido"},
            ],
        }
        path = _write_canvas(tmp_vault, "bad_edges.canvas", data)
        chunks = parse_canvas(path, tmp_vault)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "válido"

    def test_arquivo_inexistente(self, tmp_vault):
        """Arquivo que não existe deve retornar lista vazia."""
        path = tmp_vault / "nao_existe.canvas"
        chunks = parse_canvas(path, tmp_vault)
        assert chunks == []
