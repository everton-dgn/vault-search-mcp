"""Regressões dos contratos públicos de navegação e links."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vault_search.server.search_tools import register_search_tools


class _Batch:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def to_pylist(self) -> list[dict]:
        return self._rows


class _Query:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._columns: list[str] | None = None

    def where(self, clause: str):
        if "from_note_path LIKE" in clause:
            prefix = clause.split("LIKE '", 1)[1].split("/%'", 1)[0]
            self._rows = [
                row for row in self._rows if row["from_note_path"].startswith(f"{prefix}/")
            ]
        if "is_resolved = false" in clause:
            self._rows = [
                row
                for row in self._rows
                if not row.get("is_resolved", False) and row.get("link_type") != "external"
            ]
        return self

    def select(self, columns: list[str]):
        self._columns = columns
        return self

    def limit(self, value):
        assert value is None
        return self

    def to_batches(self, batch_size: int = 1000):
        rows = self._rows
        if self._columns is not None:
            rows = [{column: row.get(column) for column in self._columns} for row in rows]
        for start in range(0, len(rows), batch_size):
            yield _Batch(rows[start : start + batch_size])


class _Table:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def search(self):
        return _Query(list(self._rows))


class _MCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator


def _registered_tools(*, links: list[dict] | None = None):
    mcp = _MCP()
    indexer = MagicMock()
    indexer._ensure_links_table.return_value = _Table(links or [])
    register_search_tools(mcp, indexer, MagicMock())
    return mcp.tools


@pytest.mark.parametrize("folder_kind", ["traversal", "absolute", "symlink"])
def test_daily_note_never_reads_outside_vault(tmp_path: Path, monkeypatch, folder_kind: str):
    from vault_search.crud import validation

    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    (outside / "2024-01-15.md").write_text("private", encoding="utf-8")
    (vault / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(validation, "VAULT_PATH", vault)
    folder = {
        "traversal": "../../outside",
        "absolute": str(outside),
        "symlink": "escape",
    }[folder_kind]
    catalog = MagicMock()
    catalog.is_available.return_value = False

    with patch("vault_search.server.search_tools.get_catalog", return_value=catalog):
        result = _registered_tools()["daily_note"]("2024-01-15", folder)

    assert isinstance(result, str)
    assert "size_bytes" not in result
    assert "private" not in result


def test_daily_note_uses_canonical_nested_path(tmp_path: Path, monkeypatch):
    from vault_search.crud import validation

    vault = tmp_path / "vault"
    folder = vault / "journals" / "daily"
    folder.mkdir(parents=True)
    note = folder / "2024-01-15.md"
    note.write_text("daily", encoding="utf-8")
    monkeypatch.setattr(validation, "VAULT_PATH", vault)
    catalog = MagicMock()
    catalog.is_available.return_value = False

    with patch("vault_search.server.search_tools.get_catalog", return_value=catalog):
        result = _registered_tools()["daily_note"]("2024-01-15", "journals/./daily")

    assert result["exists"] is True
    assert result["path"] == "journals/daily/2024-01-15.md"
    assert result["size_bytes"] == len("daily")


def test_find_broken_links_reports_global_totals_before_pagination():
    links = []
    for note_path, count in (("a.md", 1), ("b.md", 6)):
        for index in range(count):
            links.append(
                {
                    "from_note_path": note_path,
                    "from_note_title": note_path.removesuffix(".md").upper(),
                    "link_type": "wikilink",
                    "link_target": f"missing-{index}",
                    "link_target_normalized": f"missing-{index}",
                    "to_note_path": "",
                    "context": "",
                    "is_resolved": False,
                }
            )

    result = _registered_tools(links=links)["find_broken_links"](limit=1)

    assert result["total_broken_links"] == 7
    assert result["notes_with_broken_links"] == 2
    assert result["returned_notes"] == 1
    assert result["has_more"] is True
    assert result["notes"][0]["path"] == "b.md"
    assert len(result["notes"][0]["broken_links"]) == 6


def test_find_orphan_notes_keeps_exact_total_when_page_is_limited():
    notes = [
        {"path": "linked.md", "title": "Linked", "folder": "", "modified_at": "2024-04-01"},
        {"path": "new.md", "title": "New", "folder": "", "modified_at": "2024-03-01"},
        {"path": "middle.md", "title": "Middle", "folder": "", "modified_at": "2024-02-01"},
        {"path": "old.md", "title": "Old", "folder": "", "modified_at": "2024-01-01"},
    ]
    links = [
        {
            "from_note_path": "source.md",
            "from_note_title": "Source",
            "link_type": "wikilink",
            "link_target": "linked",
            "link_target_normalized": "linked",
            "to_note_path": "linked.md",
            "context": "",
            "is_resolved": True,
        }
    ]
    catalog = SimpleNamespace(
        is_available=lambda: True,
        list_notes=lambda **kwargs: (
            notes[kwargs.get("offset", 0) : kwargs.get("offset", 0) + kwargs["limit"]],
            len(notes),
        ),
    )

    with patch("vault_search.server.search_tools.get_catalog", return_value=catalog):
        result = _registered_tools(links=links)["find_orphan_notes"](limit=1)

    assert result["total_notes"] == 4
    assert result["total_orphans"] == 3
    assert result["orphan_percentage"] == 75.0
    assert result["returned_notes"] == 1
    assert result["has_more"] is True
    assert result["notes"][0]["path"] == "old.md"
