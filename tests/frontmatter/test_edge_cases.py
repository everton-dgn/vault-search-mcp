"""
Testes para edge cases críticos de frontmatter validation.

Cobre gaps identificados na auditoria:
- String "null" vs null value
- Timezone aware/naive datetimes
- Caracteres invisíveis (BOM, NBSP, zero-width)
- Formato de data com "/" (deve rejeitar)
- Múltiplos erros simultâneos
"""

from datetime import UTC, datetime

import pytest

from vault_search.frontmatter.coercion import (
    coerce_date,
    coerce_datetime,
    coerce_string,
)
from vault_search.frontmatter.schema import FieldSchema, FrontmatterSchemaConfig
from vault_search.frontmatter.validator import FrontmatterValidator


class TestNullStringVsNullValue:
    """Testes para distinguir string "null" de null value."""

    def test_string_null_is_string(self):
        """String literal 'null' deve permanecer como string."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string("null", schema)
        assert result == "null"
        assert warning is None

    def test_string_null_uppercase(self):
        """String 'NULL' deve permanecer como string."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string("NULL", schema)
        assert result == "NULL"

    def test_string_none_is_string(self):
        """String 'None' deve permanecer como string."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string("None", schema)
        assert result == "None"

    def test_python_none_to_string(self):
        """Python None coercido para string vira 'None'."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string(None, schema)
        assert result == "None"
        assert warning is not None
        assert "Convertido" in warning

    def test_validator_with_none_value(self):
        """Validator deve tratar None como campo ausente."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
            },
        )
        validator = FrontmatterValidator(config)

        # Campo com valor None explícito
        result = validator.validate({"title": None})

        # None é coercido para string "None"
        assert result["valid"] is True
        assert result["validated_data"]["title"] == "None"


class TestTimezoneHandling:
    """Testes para datetime com timezone."""

    def test_datetime_with_utc_timezone(self):
        """Datetime com timezone UTC deve ser aceito."""
        schema = FieldSchema(type="datetime")
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        result, warning = coerce_datetime(dt, schema)
        assert "2024-01-15" in result
        assert "10:30" in result

    def test_datetime_string_with_z_suffix(self):
        """String ISO com Z suffix (UTC) deve ser aceita."""
        schema = FieldSchema(type="datetime")
        result, warning = coerce_datetime("2024-01-15T10:30:00Z", schema)
        assert "2024-01-15" in result

    def test_datetime_string_with_offset(self):
        """String ISO com offset (+03:00) deve ser aceita."""
        schema = FieldSchema(type="datetime")
        result, warning = coerce_datetime("2024-01-15T10:30:00+03:00", schema)
        assert "2024-01-15" in result

    def test_datetime_naive_accepted(self):
        """Datetime naive (sem timezone) deve ser aceito."""
        schema = FieldSchema(type="datetime")
        dt = datetime(2024, 1, 15, 10, 30, 0)  # naive
        result, warning = coerce_datetime(dt, schema)
        assert result == "2024-01-15T10:30:00"


class TestInvisibleCharacters:
    """Testes para caracteres invisíveis problemáticos."""

    def test_string_with_bom(self):
        """String com BOM (Byte Order Mark) deve ser aceita."""
        schema = FieldSchema(type="string")
        # BOM UTF-8: \ufeff
        result, warning = coerce_string("\ufeffhello", schema)
        # BOM é mantido (coerção não faz strip)
        assert "hello" in result

    def test_string_with_nbsp(self):
        """String com non-breaking space deve ser aceita."""
        schema = FieldSchema(type="string")
        # NBSP: \u00a0
        result, warning = coerce_string("hello\u00a0world", schema)
        assert "hello" in result
        assert "world" in result

    def test_string_with_zero_width_space(self):
        """String com zero-width space deve ser aceita."""
        schema = FieldSchema(type="string")
        # Zero-width space: \u200b
        result, warning = coerce_string("hello\u200bworld", schema)
        assert "hello" in result

    def test_int_with_nbsp_fails(self):
        """Int com NBSP no meio deve falhar."""
        from vault_search.frontmatter.coercion import coerce_int

        schema = FieldSchema(type="int")
        # "12 34" com NBSP não é número válido
        with pytest.raises(ValueError):
            coerce_int("12\u00a034", schema)

    def test_date_with_bom_prefix(self):
        """Date com BOM prefix deve falhar (não é ISO válido)."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="data válida"):
            coerce_date("\ufeff2024-01-15", schema)


class TestAmbiguousDateFormats:
    """Testes para formatos de data ambíguos (devem ser rejeitados)."""

    def test_slash_format_mmddyyyy_rejected(self):
        """Formato MM/DD/YYYY deve ser rejeitado."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            coerce_date("01/15/2024", schema)

    def test_slash_format_ddmmyyyy_rejected(self):
        """Formato DD/MM/YYYY deve ser rejeitado."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            coerce_date("15/01/2024", schema)

    def test_slash_format_yyyymmdd_rejected(self):
        """Formato YYYY/MM/DD com barras deve ser rejeitado."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            coerce_date("2024/01/15", schema)

    def test_dot_format_rejected(self):
        """Formato DD.MM.YYYY deve ser rejeitado."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            coerce_date("15.01.2024", schema)

    def test_iso_format_accepted(self):
        """Formato ISO YYYY-MM-DD deve ser aceito."""
        schema = FieldSchema(type="date")
        result, warning = coerce_date("2024-01-15", schema)
        assert result == "2024-01-15"


class TestMultipleErrorsAggregation:
    """Testes para agregação de múltiplos erros."""

    def test_multiple_validation_errors(self):
        """Múltiplos erros devem ser coletados, não fail-fast."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            mode="strict",
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
                "status": FieldSchema(type="enum", values=["draft", "published"]),
                "priority": FieldSchema(type="int", minimum=1, maximum=5),
            },
        )
        validator = FrontmatterValidator(config)

        # Dados com múltiplos erros
        result = validator.validate(
            {
                # title ausente (required)
                "status": "invalid_status",  # enum inválido
                "priority": 100,  # fora do range
            }
        )

        assert result["valid"] is False
        # Deve ter 3 erros, não apenas 1
        assert len(result["errors"]) == 3

        error_fields = {e["field"] for e in result["errors"]}
        assert "title" in error_fields
        assert "status" in error_fields
        assert "priority" in error_fields

    def test_errors_have_distinct_codes(self):
        """Cada tipo de erro deve ter código distinto."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            mode="strict",
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
                "count": FieldSchema(type="int"),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate(
            {
                # title ausente
                "count": "not-a-number",
            }
        )

        assert len(result["errors"]) == 2
        error_codes = {e["code"] for e in result["errors"]}
        assert "required_missing" in error_codes
        assert "validation_error" in error_codes

    def test_warnings_collected_with_errors(self):
        """Warnings devem ser coletados mesmo com erros."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            mode="strict",
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
                "status": FieldSchema(
                    type="enum",
                    values=["draft", "published"],
                ),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate(
            {
                # title ausente (erro)
                "status": "DRAFT",  # case diferente (warning de coerção)
            }
        )

        assert result["valid"] is False
        assert len(result["errors"]) == 1
        # Warning de coerção deve estar presente
        assert len(result["warnings"]) >= 1


class TestEmptyAndWhitespaceValues:
    """Testes para valores vazios e whitespace."""

    def test_empty_frontmatter_dict(self):
        """Dict vazio deve processar campos required/auto."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "id": FieldSchema(type="uuid", on_missing="auto"),
                "title": FieldSchema(type="string", on_missing="require"),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({})

        # id deve ser auto-gerado
        assert "id" in result["auto_generated"]
        # title deve gerar erro
        assert any(e["field"] == "title" for e in result["errors"])

    def test_whitespace_only_string(self):
        """String só com whitespace deve passar (não faz trim)."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string("   ", schema)
        assert result == "   "

    def test_whitespace_string_with_min_length(self):
        """String whitespace conta para min_length."""
        schema = FieldSchema(type="string", min_length=3)
        result, warning = coerce_string("   ", schema)
        assert result == "   "  # 3 espaços >= min_length 3
