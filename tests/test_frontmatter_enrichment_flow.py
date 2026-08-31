"""
Tests for flow for defer/enrichment of frontmatter required.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from vault_search.frontmatter.schema import FieldSchema, FrontmatterSchemaConfig
from vault_search.frontmatter.validator import FrontmatterValidator
from vault_search.parsers.frontmatter import parse_frontmatter


def _make_config(enabled: bool, ai_enabled: bool, allow_defer: bool):
    return SimpleNamespace(
        frontmatter=SimpleNamespace(
            enabled=enabled,
            ai=SimpleNamespace(
                enabled=ai_enabled,
                allow_external_processing=ai_enabled,
                provider="test-provider" if ai_enabled else None,
                command=["test-provider"] if ai_enabled else [],
                allow_defer_required_on_create=allow_defer,
            ),
        )
    )


def test_create_note_defer_required_missing_when_ai_enabled(tmp_path, monkeypatch):
    """create_note must accept required missing when defer is enabled."""
    monkeypatch.setattr("vault_search.crud.validation.VAULT_PATH", tmp_path)
    monkeypatch.setattr(
        "vault_search.crud.write.get_config",
        lambda: _make_config(enabled=True, ai_enabled=True, allow_defer=True),
    )
    monkeypatch.setattr(
        "vault_search.crud.write.validate_frontmatter_schema_result",
        lambda _: {
            "valid": False,
            "errors": [
                {
                    "field": "title",
                    "message": "Required field 'title' was not found",
                    "code": "required_missing",
                    "value": None,
                }
            ],
            "warnings": [],
            "suggestions": [],
            "auto_generated": {},
            "validated_data": {},
        },
    )

    from vault_search.crud.write import create_note

    result = create_note("note.md", "Content")

    assert result["success"] is True
    assert (tmp_path / "note.md").exists()
    assert "id:" in (tmp_path / "note.md").read_text(encoding="utf-8")


def test_create_note_blocks_required_missing_when_defer_disabled(tmp_path, monkeypatch):
    """create_note must fail when required missing and defer not allowed."""
    monkeypatch.setattr("vault_search.crud.validation.VAULT_PATH", tmp_path)
    monkeypatch.setattr(
        "vault_search.crud.write.get_config",
        lambda: _make_config(enabled=True, ai_enabled=False, allow_defer=False),
    )
    monkeypatch.setattr(
        "vault_search.crud.write.validate_frontmatter_schema_result",
        lambda _: {
            "valid": False,
            "errors": [
                {
                    "field": "title",
                    "message": "Field required 'title' not found",
                    "code": "required_missing",
                    "value": None,
                }
            ],
            "warnings": [],
            "suggestions": [],
            "auto_generated": {},
            "validated_data": {},
        },
    )

    from vault_search.crud.write import create_note

    result = create_note("note.md", "Content")

    assert result["success"] is False
    assert "Frontmatter validation failed" in result["message"]
    assert not (tmp_path / "note.md").exists()


def test_enrich_note_marks_required_missing_when_ai_returns_empty(tmp_path, monkeypatch):
    """enrich_note_frontmatter_required reports required_missing without useful data."""
    monkeypatch.setattr("vault_search.crud.validation.VAULT_PATH", tmp_path)
    note = tmp_path / "note.md"
    note.write_text("Content", encoding="utf-8")

    validator = FrontmatterValidator(
        FrontmatterSchemaConfig(
            enabled=True,
            mode="strict",
            allow_extra_fields=True,
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
            },
        )
    )
    monkeypatch.setattr(
        "vault_search.crud.write.get_frontmatter_validator",
        lambda: validator,
    )
    monkeypatch.setattr(
        "vault_search.crud.write.generate_required_fields_with_ai",
        lambda **_: {},
    )

    from vault_search.crud.write import enrich_note_frontmatter_required

    result = enrich_note_frontmatter_required("note.md")

    assert result["success"] is False
    assert result["error_code"] == "required_missing"


def test_enrich_note_fills_required_field_when_value_is_empty(tmp_path, monkeypatch):
    """An empty required field must be enriched instead of treated as existing."""
    monkeypatch.setattr("vault_search.crud.validation.VAULT_PATH", tmp_path)
    note = tmp_path / "note.md"
    note.write_text('---\ntitle: ""\n---\nContent', encoding="utf-8")

    validator = FrontmatterValidator(
        FrontmatterSchemaConfig(
            enabled=True,
            mode="strict",
            allow_extra_fields=True,
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
            },
        )
    )
    monkeypatch.setattr(
        "vault_search.crud.write.get_frontmatter_validator",
        lambda: validator,
    )
    monkeypatch.setattr(
        "vault_search.crud.write.generate_required_fields_with_ai",
        lambda **_: {"title": "Generated title"},
    )
    monkeypatch.setattr(
        "vault_search.crud.write.validate_frontmatter_schema",
        lambda data: (data, [], [], []),
    )

    from vault_search.crud.write import enrich_note_frontmatter_required

    result = enrich_note_frontmatter_required("note.md")

    assert result["success"] is True
    assert result.get("frontmatter_enriched") is True
    assert result.get("frontmatter_fields_filled") == 1

    raw = note.read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(raw)
    assert fm["title"] == "Generated title"


def test_enrich_note_persists_partial_when_strict_still_has_required_missing(tmp_path, monkeypatch):
    """When AI returns partial, must save fields generated and not fail."""
    monkeypatch.setattr("vault_search.crud.validation.VAULT_PATH", tmp_path)
    note = tmp_path / "note.md"
    note.write_text("Content", encoding="utf-8")

    validator = FrontmatterValidator(
        FrontmatterSchemaConfig(
            enabled=True,
            mode="strict",
            allow_extra_fields=True,
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
                "description": FieldSchema(type="string", on_missing="require"),
            },
        )
    )
    monkeypatch.setattr(
        "vault_search.crud.write.get_frontmatter_validator",
        lambda: validator,
    )
    monkeypatch.setattr(
        "vault_search.crud.write.generate_required_fields_with_ai",
        lambda **_: {"title": "Partial title"},
    )

    def fail_with_required(_):
        raise ValueError(
            "Frontmatter validation failed: description: Required field 'description' was not found"
        )

    monkeypatch.setattr(
        "vault_search.crud.write.validate_frontmatter_schema",
        fail_with_required,
    )

    from vault_search.crud.write import enrich_note_frontmatter_required

    result = enrich_note_frontmatter_required("note.md")

    assert result["success"] is True
    assert result.get("frontmatter_enriched") is True
    assert result.get("frontmatter_fields_filled") == 1
    assert result.get("_validation_warnings")
    assert result["_validation_warnings"][0]["code"] == "required_missing_partial"

    fm, _ = parse_frontmatter(note.read_text(encoding="utf-8"))
    assert fm["title"] == "Partial title"


def test_reindex_note_does_not_call_frontmatter_enrichment(tmp_path, monkeypatch):
    """reindex_note must not more call enrichment of frontmatter."""
    note = tmp_path / "note.md"
    note.write_text("---\ntitle: Test\n---\nContent", encoding="utf-8")

    monkeypatch.setattr("vault_search.core.indexer.VAULT_PATH", tmp_path)
    monkeypatch.setattr("vault_search.core.indexer.validate_relative_path", lambda _: True)

    from vault_search.core.indexer import VaultIndexer

    indexer = VaultIndexer()
    mock_table = MagicMock()
    mock_table.version = 1
    mock_links = MagicMock()
    mock_links.version = 1
    mock_aliases = MagicMock()
    mock_aliases.version = 1
    indexer._ensure_table = lambda data=None: mock_table
    indexer._ensure_links_table = lambda: mock_links
    indexer._ensure_aliases_table = lambda: mock_aliases
    indexer._models = MagicMock()
    indexer._models.embed_corpus.return_value = [[0.1] * 1024]

    result = indexer.reindex_note("note.md")

    # The note is indexed without attempting automatic enrichment.
    assert result["status"] in ("updated", "empty")
    assert result.get("frontmatter_enriched", False) is False


def test_reindex_note_in_longer_logs_enrichment_warning(tmp_path, monkeypatch, caplog):
    """reindex_note must not emit warning of enrichment automatic."""
    import logging

    note = tmp_path / "note.md"
    note.write_text("---\ntitle: Test\n---\nContent", encoding="utf-8")

    monkeypatch.setattr("vault_search.core.indexer.VAULT_PATH", tmp_path)
    monkeypatch.setattr("vault_search.core.indexer.validate_relative_path", lambda _: True)

    from vault_search.core.indexer import VaultIndexer

    indexer = VaultIndexer()
    mock_table = MagicMock()
    mock_table.version = 1
    mock_links = MagicMock()
    mock_links.version = 1
    mock_aliases = MagicMock()
    mock_aliases.version = 1
    indexer._ensure_table = lambda data=None: mock_table
    indexer._ensure_links_table = lambda: mock_links
    indexer._ensure_aliases_table = lambda: mock_aliases
    indexer._models = MagicMock()
    indexer._models.embed_corpus.return_value = [[0.1] * 1024]

    with caplog.at_level(logging.WARNING):
        indexer.reindex_note("note.md")

    assert "frontmatter_enrichment_failed_continuing" not in caplog.text
