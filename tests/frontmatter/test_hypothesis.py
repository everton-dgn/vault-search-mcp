"""
Property-based tests for frontmatter validation using Hypothesis.

Generates data random for find edge cases not covered by tests manual.
"""

from datetime import date, datetime
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from vault_search.frontmatter.coercion import (
    coerce_bool,
    coerce_date,
    coerce_datetime,
    coerce_float,
    coerce_int,
    coerce_list,
    coerce_string,
)
from vault_search.frontmatter.schema import FieldSchema, FrontmatterSchemaConfig
from vault_search.frontmatter.validator import FrontmatterValidator

# =============================================================================
# Strategies for generate data of test
# =============================================================================

# Valid frontmatter strings without problematic control characters.
frontmatter_strings = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S", "Z"),
        blacklist_characters="\x00",  # null byte
    ),
    min_size=0,
    max_size=1000,
)

# Inteiros reasonable (avoids overflow)
reasonable_ints = st.integers(min_value=-(2**31), max_value=2**31)

# Floats valid (without NaN/Inf)
valid_floats = st.floats(
    allow_nan=False,
    allow_infinity=False,
    min_value=-1e10,
    max_value=1e10,
)

# Valid dates
valid_dates = st.dates(
    min_value=date(1900, 1, 1),
    max_value=date(2100, 12, 31),
)

# Datetimes valid
valid_datetimes = st.datetimes(
    min_value=datetime(1900, 1, 1),
    max_value=datetime(2100, 12, 31),
)


# =============================================================================
# Property-based tests for coercion
# =============================================================================


class TestCoercionProperties:
    """Property-based tests for coercion functions."""

    @given(st.text())
    def test_string_coercion_never_crashes(self, value: str):
        """Coercion of string never must crash with input string."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string(value, schema)
        assert isinstance(result, str)

    @given(reasonable_ints)
    def test_int_roundtrip(self, value: int):
        """Int coerced must keep value."""
        schema = FieldSchema(type="int")
        result, warning = coerce_int(value, schema)
        assert result == value

    @given(valid_floats)
    def test_float_roundtrip(self, value: float):
        """Float coerced must keep value (approximate)."""
        schema = FieldSchema(type="float")
        result, warning = coerce_float(value, schema)
        assert abs(result - value) < 1e-10 or result == value

    @given(st.booleans())
    def test_bool_roundtrip(self, value: bool):
        """Bool coerced must keep value."""
        schema = FieldSchema(type="bool")
        result, warning = coerce_bool(value, schema)
        assert result is value

    @given(valid_dates)
    def test_date_roundtrip(self, value: date):
        """A coerced date must produce a valid ISO string."""
        schema = FieldSchema(type="date")
        result, warning = coerce_date(value, schema)
        # Result must be ISO parseable
        parsed = date.fromisoformat(result)
        assert parsed == value

    @given(valid_datetimes)
    def test_datetime_roundtrip(self, value: datetime):
        """A coerced datetime must produce a valid ISO string."""
        schema = FieldSchema(type="datetime")
        result, warning = coerce_datetime(value, schema)
        # Result must be ISO parseable
        parsed = datetime.fromisoformat(result)
        assert parsed == value

    @settings(deadline=None)
    @given(st.lists(st.text(min_size=1, max_size=50), min_size=0, max_size=20))
    def test_list_roundtrip(self, value: list[str]):
        """List of strings coerced must keep items, without measure warmup of the runtime."""
        schema = FieldSchema(type="list", item_type="string")
        result, warning = coerce_list(value, schema)
        assert result == value


class TestCoercionConstraints:
    """Tests for constraints of coercion."""

    @given(st.text(min_size=10, max_size=100))
    def test_string_min_length_constraint(self, value: str):
        """A string must respect min_length."""
        schema = FieldSchema(type="string", min_length=5)
        if len(value) >= 5:
            result, _ = coerce_string(value, schema)
            assert len(result) >= 5
        else:
            with pytest.raises(ValueError):
                coerce_string(value, schema)

    @given(reasonable_ints)
    def test_int_range_constraint(self, value: int):
        """An integer must respect minimum and maximum."""
        schema = FieldSchema(type="int", minimum=0, maximum=100)
        if 0 <= value <= 100:
            result, _ = coerce_int(value, schema)
            assert 0 <= result <= 100
        else:
            with pytest.raises(ValueError):
                coerce_int(value, schema)


# =============================================================================
# Property-based tests for Validator
# =============================================================================


class TestValidatorProperties:
    """Property-based tests for FrontmatterValidator."""

    @given(
        st.dictionaries(
            keys=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
            values=st.text(min_size=0, max_size=100),
            min_size=0,
            max_size=10,
        )
    )
    def test_validator_never_crashes(self, data: dict[str, str]):
        """Validator never must crash with input arbitrary."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            mode="lenient",
            allow_extra_fields=True,
            schema={},
        )
        validator = FrontmatterValidator(config)

        result = validator.validate(data)

        # Every generated case must return a valid result.
        assert "valid" in result
        assert "errors" in result
        assert "warnings" in result
        assert "validated_data" in result

    @given(st.text(min_size=1, max_size=100))
    def test_required_field_always_checked(self, title: str):
        """A required field must always be validated."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
            },
        )
        validator = FrontmatterValidator(config)

        # With title present
        result = validator.validate({"title": title})
        assert result["valid"] is True
        assert result["validated_data"]["title"] == title

        # Without title
        result = validator.validate({})
        assert result["valid"] is False
        assert any(e["field"] == "title" for e in result["errors"])

    @given(st.sampled_from(["draft", "published", "archived"]))
    def test_enum_valid_values_accepted(self, status: str):
        """Valid enum values are accepted."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "status": FieldSchema(
                    type="enum",
                    values=["draft", "published", "archived"],
                ),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({"status": status})
        assert result["valid"] is True

    @settings(max_examples=50)
    @given(
        st.dictionaries(
            keys=st.text(min_size=1, max_size=10, alphabet="abcdefghijklmnopqrstuvwxyz"),
            values=st.one_of(
                st.text(max_size=50),
                st.integers(),
                st.booleans(),
                st.lists(st.text(max_size=20), max_size=5),
            ),
            min_size=0,
            max_size=5,
        )
    )
    def test_extra_fields_preserved(self, extra: dict[str, Any]):
        """Extra fields are preserved when allowed."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            allow_extra_fields=True,
            schema={},
        )
        validator = FrontmatterValidator(config)

        result = validator.validate(extra)

        assert result["valid"] is True
        # All the fields must be in validated_data
        for key in extra:
            assert key in result["validated_data"]


class TestAutoGenerationProperties:
    """Tests for auto-generation of fields."""

    @given(st.just({}))  # Always dict empty
    def test_uuid_auto_generation_format(self, _):
        """UUID auto-generated must have format valid."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "id": FieldSchema(type="uuid", on_missing="auto"),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({})

        assert result["valid"] is True
        uuid = result["auto_generated"]["id"]
        # UUID v7 has version 7 in the position 14
        assert uuid[14] == "7"
        # Format: 8-4-4-4-12 (36 chars with hyphens)
        assert len(uuid) == 36
        assert uuid.count("-") == 4

    @given(st.just({}))
    def test_datetime_auto_generation_format(self, _):
        """Datetime auto-generated must be ISO valid."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "created_at": FieldSchema(type="datetime", on_missing="auto"),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({})

        assert result["valid"] is True
        dt_str = result["auto_generated"]["created_at"]
        # Must be parseable
        parsed = datetime.fromisoformat(dt_str)
        assert isinstance(parsed, datetime)


class TestAliasProperties:
    """Tests for resolution of aliases."""

    @given(st.sampled_from(["created", "date", "timestamp"]))
    def test_any_alias_resolves(self, alias: str):
        """Any alias must resolve to the canonical field."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "created_at": FieldSchema(
                    type="datetime",
                    aliases=["created", "date", "timestamp"],
                ),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({alias: "2024-01-15T10:00:00"})

        assert result["valid"] is True
        assert "created_at" in result["validated_data"]
        # Alias used must generate warning
        alias_warnings = [w for w in result["warnings"] if w["code"] == "alias_resolved"]
        assert len(alias_warnings) == 1
