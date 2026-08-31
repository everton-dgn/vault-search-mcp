"""
Tests for edge cases critical of frontmatter validation.

Covers gaps identified during the audit:
- String "null" vs null value
- Timezone aware/naive datetimes
- Characters invisible (BOM, NBSP, zero-width)
- Format of data with "/" (must reject)
- Multiple errors simultaneous
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
    """Tests distinguish the string "null" from a null value."""

    def test_string_null_is_string(self):
        """String literal 'null' must remain as string."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string("null", schema)
        assert result == "null"
        assert warning is None

    def test_string_null_uppercase(self):
        """String 'NULL' must remain as string."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string("NULL", schema)
        assert result == "NULL"

    def test_string_none_is_string(self):
        """String 'None' must remain as string."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string("None", schema)
        assert result == "None"

    def test_python_none_to_string(self):
        """Python None coerced to a string becomes 'None'."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string(None, schema)
        assert result == "None"
        assert warning is not None
        assert "Converted" in warning

    def test_validator_with_none_value(self):
        """The validator treats None as a missing field."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
            },
        )
        validator = FrontmatterValidator(config)

        # Field with value None explicit
        result = validator.validate({"title": None})

        # None is coerced for string "None"
        assert result["valid"] is True
        assert result["validated_data"]["title"] == "None"


class TestTimezoneHandling:
    """Tests for datetime with timezone."""

    def test_datetime_with_utc_timezone(self):
        """A datetime with the UTC timezone is accepted."""
        schema = FieldSchema(type="datetime")
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        result, warning = coerce_datetime(dt, schema)
        assert "2024-01-15" in result
        assert "10:30" in result

    def test_datetime_string_with_z_suffix(self):
        """An ISO string with a Z suffix is accepted as UTC."""
        schema = FieldSchema(type="datetime")
        result, warning = coerce_datetime("2024-01-15T10:30:00Z", schema)
        assert "2024-01-15" in result

    def test_datetime_string_with_offset(self):
        """An ISO string with a +03:00 offset is accepted."""
        schema = FieldSchema(type="datetime")
        result, warning = coerce_datetime("2024-01-15T10:30:00+03:00", schema)
        assert "2024-01-15" in result

    def test_datetime_naive_accepted(self):
        """Datetime naive (without timezone) must be accepted."""
        schema = FieldSchema(type="datetime")
        dt = datetime(2024, 1, 15, 10, 30, 0)  # naive
        result, warning = coerce_datetime(dt, schema)
        assert result == "2024-01-15T10:30:00"


class TestInvisibleCharacters:
    """Tests for characters invisible problematic."""

    def test_string_with_bom(self):
        """A string with a byte-order mark is accepted."""
        schema = FieldSchema(type="string")
        # BOM UTF-8: \ufeff
        result, warning = coerce_string("\ufeffhello", schema)
        # BOM is preserved (coercion does not strip)
        assert "hello" in result

    def test_string_with_nbsp(self):
        """A string containing a nonbreaking space is accepted."""
        schema = FieldSchema(type="string")
        # NBSP: \u00a0
        result, warning = coerce_string("hello\u00a0world", schema)
        assert "hello" in result
        assert "world" in result

    def test_string_with_zero_width_space(self):
        """A string containing a zero-width space is accepted."""
        schema = FieldSchema(type="string")
        # Zero-width space: \u200b
        result, warning = coerce_string("hello\u200bworld", schema)
        assert "hello" in result

    def test_int_with_nbsp_fails(self):
        """An integer containing an internal nonbreaking space is rejected."""
        from vault_search.frontmatter.coercion import coerce_int

        schema = FieldSchema(type="int")
        # "12 34" with NBSP is not number valid
        with pytest.raises(ValueError):
            coerce_int("12\u00a034", schema)

    def test_date_with_bom_prefix(self):
        """A date with a byte-order-mark prefix is not valid ISO input."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="valid date"):
            coerce_date("\ufeff2024-01-15", schema)


class TestAmbiguousDateFormats:
    """Tests for ambiguous date formats that must be rejected."""

    def test_slash_format_mmddyyyy_rejected(self):
        """Format MM/DD/YYYY must be rejected."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            coerce_date("01/15/2024", schema)

    def test_slash_format_ddmmyyyy_rejected(self):
        """Format DD/MM/YYYY must be rejected."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            coerce_date("15/01/2024", schema)

    def test_slash_format_yyyymmdd_rejected(self):
        """Format YYYY/MM/DD with slashes must be rejected."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            coerce_date("2024/01/15", schema)

    def test_dot_format_rejected(self):
        """Format DD.MM.YYYY must be rejected."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            coerce_date("15.01.2024", schema)

    def test_iso_format_accepted(self):
        """Format ISO YYYY-MM-DD must be accepted."""
        schema = FieldSchema(type="date")
        result, warning = coerce_date("2024-01-15", schema)
        assert result == "2024-01-15"


class TestMultipleErrorsAggregation:
    """Tests for aggregation of multiple errors."""

    def test_multiple_validation_errors(self):
        """Multiple errors must be collected instead of failing fast."""
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

        # Data with multiple errors
        result = validator.validate(
            {
                # title missing (required)
                "status": "invalid_status",  # enum invalid
                "priority": 100,  # outside of the range
            }
        )

        assert result["valid"] is False
        # Must have 3 errors, not only 1
        assert len(result["errors"]) == 3

        error_fields = {e["field"] for e in result["errors"]}
        assert "title" in error_fields
        assert "status" in error_fields
        assert "priority" in error_fields

    def test_errors_have_distinct_codes(self):
        """Each error type must have a distinct code."""
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
                # title missing
                "count": "not-a-number",
            }
        )

        assert len(result["errors"]) == 2
        error_codes = {e["code"] for e in result["errors"]}
        assert "required_missing" in error_codes
        assert "validation_error" in error_codes

    def test_warnings_collected_with_errors(self):
        """Warnings must be collected even when errors exist."""
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
                # title missing (error)
                "status": "DRAFT",  # case different (warning of coercion)
            }
        )

        assert result["valid"] is False
        assert len(result["errors"]) == 1
        # Warning of coercion must be present
        assert len(result["warnings"]) >= 1


class TestEmptyAndWhitespaceValues:
    """Tests for values empty and whitespace."""

    def test_empty_frontmatter_dict(self):
        """An empty mapping still processes required and automatic fields."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "id": FieldSchema(type="uuid", on_missing="auto"),
                "title": FieldSchema(type="string", on_missing="require"),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({})

        # id must be auto-generated
        assert "id" in result["auto_generated"]
        # title must generate error
        assert any(e["field"] == "title" for e in result["errors"])

    def test_whitespace_only_string(self):
        """A whitespace-only string must pass because coercion does not trim."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string("   ", schema)
        assert result == "   "

    def test_whitespace_string_with_min_length(self):
        """String whitespace counts for min_length."""
        schema = FieldSchema(type="string", min_length=3)
        result, warning = coerce_string("   ", schema)
        assert result == "   "  # 3 spaces >= min_length 3
