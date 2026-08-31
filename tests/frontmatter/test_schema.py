"""
Tests for frontmatter validation with Pydantic schemas.

Covers:
- Pydantic models (FieldSchema, FrontmatterSchemaConfig)
- type coercion functions
- FrontmatterValidator
- Integration with CRUD
"""

from datetime import date, datetime

import pytest

from vault_search.frontmatter.coercion import (
    coerce_bool,
    coerce_date,
    coerce_datetime,
    coerce_enum,
    coerce_float,
    coerce_int,
    coerce_list,
    coerce_string,
    coerce_url,
    coerce_uuid,
)
from vault_search.frontmatter.schema import FieldSchema, FrontmatterSchemaConfig
from vault_search.frontmatter.types import FieldType, OnMissingBehavior, ValidationMode
from vault_search.frontmatter.validator import FrontmatterValidator

# =============================================================================
# Tests for FieldSchema
# =============================================================================


class TestFieldSchema:
    """Tests for the FieldSchema Pydantic model."""

    def test_minimal_schema(self):
        """A minimal schema requires only a type."""
        schema = FieldSchema(type="string")
        assert schema.type == "string"
        assert schema.on_missing == "ignore"
        assert schema.default is None

    def test_all_field_types(self):
        """All the types of field must be valid."""
        types: list[FieldType] = [
            "string",
            "int",
            "float",
            "bool",
            "date",
            "datetime",
            "uuid",
            "url",
            "enum",
            "list",
        ]
        for t in types:
            if t == "enum":
                schema = FieldSchema(type=t, values=["a", "b"])
            else:
                schema = FieldSchema(type=t)
            assert schema.type == t

    def test_on_missing_behaviors(self):
        """Every on_missing behavior must be valid."""
        behaviors: list[OnMissingBehavior] = ["auto", "suggest", "require", "ignore"]
        for behavior in behaviors:
            if behavior == "auto":
                # auto supports only uuid and datetime.
                schema = FieldSchema(type="uuid", on_missing=behavior)
            else:
                schema = FieldSchema(type="string", on_missing=behavior)
            assert schema.on_missing == behavior

    def test_enum_requires_values(self):
        """type='enum' without values must fail."""
        with pytest.raises(ValueError, match="requires 'values'"):
            FieldSchema(type="enum")

    def test_enum_with_empty_values_fails(self):
        """type='enum' with values empty must fail."""
        with pytest.raises(ValueError, match="cannot be an empty list"):
            FieldSchema(type="enum", values=[])

    def test_auto_only_for_uuid_datetime(self):
        """on_missing='auto' only is valid for uuid and datetime."""
        # uuid ok
        FieldSchema(type="uuid", on_missing="auto")
        # datetime ok
        FieldSchema(type="datetime", on_missing="auto")

        # other types must fail
        with pytest.raises(ValueError, match="supported only"):
            FieldSchema(type="string", on_missing="auto")

    def test_string_constraints(self):
        """String constraints must be validated."""
        schema = FieldSchema(
            type="string",
            min_length=5,
            max_length=100,
            pattern=r"^[A-Z]",
        )
        assert schema.min_length == 5
        assert schema.max_length == 100
        assert schema.pattern == r"^[A-Z]"

    def test_numeric_constraints(self):
        """Numeric constraints must be validated."""
        schema = FieldSchema(type="int", minimum=0, maximum=100)
        assert schema.minimum == 0
        assert schema.maximum == 100

    def test_numeric_constraints_invalid_for_string(self):
        """minimum/maximum are not valid for string."""
        with pytest.raises(ValueError, match="apply only to"):
            FieldSchema(type="string", minimum=0)

    def test_string_constraints_invalid_for_int(self):
        """min_length/max_length are not valid for int."""
        with pytest.raises(ValueError, match="apply only to"):
            FieldSchema(type="int", min_length=5)

    def test_aliases(self):
        """Aliases are accepted."""
        schema = FieldSchema(
            type="datetime",
            aliases=["created", "date", "created_at"],
        )
        assert schema.aliases == ["created", "date", "created_at"]

    def test_list_with_item_type(self):
        """A list with a specified item_type is supported."""
        schema = FieldSchema(type="list", item_type="int", max_items=10)
        assert schema.item_type == "int"
        assert schema.max_items == 10

    def test_list_defaults_to_string_item_type(self):
        """A list without item_type defaults to strings."""
        schema = FieldSchema(type="list")
        assert schema.item_type == "string"


# =============================================================================
# Tests for FrontmatterSchemaConfig
# =============================================================================


class TestFrontmatterSchemaConfig:
    """Tests for model Pydantic FrontmatterSchemaConfig."""

    def test_default_config(self):
        """Config default must be disabled."""
        config = FrontmatterSchemaConfig()
        assert config.enabled is False
        assert config.mode == "lenient"
        assert config.allow_extra_fields is True
        assert config.schema == {}

    def test_config_with_schema(self):
        """A configuration with a schema parses its fields."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            mode="strict",
            schema={
                "title": {"type": "string", "on_missing": "require"},
                "status": {"type": "enum", "values": ["draft", "published"]},
            },
        )
        assert config.enabled is True
        assert config.mode == "strict"
        assert "title" in config.schema
        assert config.schema["title"].type == "string"
        assert config.schema["status"].values == ["draft", "published"]

    def test_validation_modes(self):
        """Every validation mode must be valid."""
        modes: list[ValidationMode] = ["strict", "lenient", "warn_only"]
        for mode in modes:
            config = FrontmatterSchemaConfig(mode=mode)
            assert config.mode == mode


# =============================================================================
# Tests for Coercion Functions
# =============================================================================


class TestCoerceString:
    """Tests for coercion of string."""

    def test_string_passthrough(self):
        """String passes without modification."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string("hello", schema)
        assert result == "hello"
        assert warning is None

    def test_int_to_string(self):
        """An integer is converted to a string."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string(42, schema)
        assert result == "42"
        assert "Converted" in warning

    def test_min_length_validation(self):
        """String very short must fail."""
        schema = FieldSchema(type="string", min_length=5)
        with pytest.raises(ValueError, match="too short"):
            coerce_string("abc", schema)

    def test_max_length_validation(self):
        """String very long must fail."""
        schema = FieldSchema(type="string", max_length=5)
        with pytest.raises(ValueError, match="too long"):
            coerce_string("abcdefgh", schema)

    def test_pattern_validation(self):
        """A string must match the pattern."""
        schema = FieldSchema(type="string", pattern=r"^[A-Z][a-z]+$")

        result, _ = coerce_string("Hello", schema)
        assert result == "Hello"

        with pytest.raises(ValueError, match="pattern"):
            coerce_string("hello", schema)


class TestCoerceInt:
    """Tests for coercion of int."""

    def test_int_passthrough(self):
        """Int passes without modification."""
        schema = FieldSchema(type="int")
        result, warning = coerce_int(42, schema)
        assert result == 42
        assert warning is None

    def test_float_truncated(self):
        """Float is truncated for int."""
        schema = FieldSchema(type="int")
        result, warning = coerce_int(3.7, schema)
        assert result == 3
        assert "truncated" in warning.lower()

    def test_string_to_int(self):
        """String numeric is converted."""
        schema = FieldSchema(type="int")
        result, warning = coerce_int("42", schema)
        assert result == 42
        # The warning may be None when int() converts directly.

    def test_bool_to_int(self):
        """A boolean is converted to 0 or 1."""
        schema = FieldSchema(type="int")
        result_true, _ = coerce_int(True, schema)
        result_false, _ = coerce_int(False, schema)
        assert result_true == 1
        assert result_false == 0

    def test_minimum_validation(self):
        """A value below the minimum is rejected."""
        schema = FieldSchema(type="int", minimum=10)
        with pytest.raises(ValueError, match="below minimum"):
            coerce_int(5, schema)

    def test_maximum_validation(self):
        """A value above the maximum is rejected."""
        schema = FieldSchema(type="int", maximum=100)
        with pytest.raises(ValueError, match="above maximum"):
            coerce_int(150, schema)


class TestCoerceFloat:
    """Tests for coercion of float."""

    def test_float_passthrough(self):
        """Float passes without modification."""
        schema = FieldSchema(type="float")
        result, warning = coerce_float(3.14, schema)
        assert result == 3.14
        assert warning is None

    def test_int_to_float(self):
        """An integer is converted to a float."""
        schema = FieldSchema(type="float")
        result, warning = coerce_float(42, schema)
        assert result == 42.0
        assert warning is None  # Conversion without loss

    def test_string_to_float(self):
        """String numeric is converted."""
        schema = FieldSchema(type="float")
        result, warning = coerce_float("3.14", schema)
        assert result == 3.14
        assert "converted" in warning.lower()


class TestCoerceBool:
    """Tests for coercion of bool."""

    def test_bool_passthrough(self):
        """Bool passes without modification."""
        schema = FieldSchema(type="bool")
        result_true, _ = coerce_bool(True, schema)
        result_false, _ = coerce_bool(False, schema)
        assert result_true is True
        assert result_false is False

    def test_string_truthy(self):
        """Truthy strings are converted to True."""
        schema = FieldSchema(type="bool")
        for value in ["true", "yes", "1", "on", "True", "YES"]:
            result, _ = coerce_bool(value, schema)
            assert result is True, f"'{value}' should be True"

    def test_string_falsy(self):
        """Falsy strings are converted to False."""
        schema = FieldSchema(type="bool")
        for value in ["false", "no", "0", "off", "False", "NO"]:
            result, _ = coerce_bool(value, schema)
            assert result is False, f"'{value}' should be False"

    def test_portuguese_boolean_strings_remain_supported(self):
        """Legacy Portuguese boolean strings preserve their public behavior."""
        schema = FieldSchema(type="bool")
        for value in ["sim", "verdadeiro", "SIM"]:
            result, _ = coerce_bool(value, schema)
            assert result is True, f"'{value}' should be True"
        for value in ["não", "nao", "falso", "NÃO"]:
            result, _ = coerce_bool(value, schema)
            assert result is False, f"'{value}' should be False"

    def test_int_0_1(self):
        """The integers 0 and 1 are converted to booleans."""
        schema = FieldSchema(type="bool")
        result_1, _ = coerce_bool(1, schema)
        result_0, _ = coerce_bool(0, schema)
        assert result_1 is True
        assert result_0 is False

    def test_invalid_int(self):
        """Integers other than 0 and 1 are rejected."""
        schema = FieldSchema(type="bool")
        with pytest.raises(ValueError, match="use 0 or 1"):
            coerce_bool(42, schema)


class TestCoerceDate:
    """Tests for coercion of date."""

    def test_date_passthrough(self):
        """A date is formatted as ISO."""
        schema = FieldSchema(type="date")
        result, warning = coerce_date(date(2024, 1, 15), schema)
        assert result == "2024-01-15"
        assert warning is None

    def test_datetime_truncated(self):
        """Datetime is truncated for date."""
        schema = FieldSchema(type="date")
        result, warning = coerce_date(datetime(2024, 1, 15, 10, 30), schema)
        assert result == "2024-01-15"
        assert "truncated" in warning.lower()

    def test_string_iso(self):
        """An ISO string is parsed."""
        schema = FieldSchema(type="date")
        result, _ = coerce_date("2024-01-15", schema)
        assert result == "2024-01-15"

    def test_invalid_string(self):
        """An invalid string is rejected."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="valid date"):
            coerce_date("not-a-date", schema)


class TestCoerceDatetime:
    """Tests for coercion of datetime."""

    def test_datetime_passthrough(self):
        """A datetime is formatted as ISO."""
        schema = FieldSchema(type="datetime")
        result, _ = coerce_datetime(datetime(2024, 1, 15, 10, 30, 45), schema)
        assert result == "2024-01-15T10:30:45"

    def test_date_expanded(self):
        """A date expands to a datetime at 00:00:00."""
        schema = FieldSchema(type="datetime")
        result, warning = coerce_datetime(date(2024, 1, 15), schema)
        assert result == "2024-01-15T00:00:00"
        assert "expanded" in warning.lower()

    def test_string_iso(self):
        """An ISO string is parsed."""
        schema = FieldSchema(type="datetime")
        result, _ = coerce_datetime("2024-01-15T10:30:00", schema)
        assert result == "2024-01-15T10:30:00"


class TestCoerceUuid:
    """Tests for coercion of UUID."""

    def test_valid_uuid(self):
        """UUID valid passes."""
        schema = FieldSchema(type="uuid")
        result, _ = coerce_uuid("550e8400-e29b-41d4-a716-446655440000", schema)
        assert result == "550e8400-e29b-41d4-a716-446655440000"

    def test_uuid_normalized(self):
        """UUID with case different is normalized."""
        schema = FieldSchema(type="uuid")
        result, warning = coerce_uuid("550E8400-E29B-41D4-A716-446655440000", schema)
        assert result == "550e8400-e29b-41d4-a716-446655440000"
        # Warning can be generated if value was normalized
        if warning:
            assert "normalized" in warning.lower()

    def test_invalid_uuid(self):
        """UUID invalid must fail."""
        schema = FieldSchema(type="uuid")
        with pytest.raises(ValueError, match="valid UUID"):
            coerce_uuid("not-a-uuid", schema)


class TestCoerceUrl:
    """Tests for coercion of URL."""

    def test_valid_url(self):
        """URL valid passes."""
        schema = FieldSchema(type="url")
        result, _ = coerce_url("https://example.com/path", schema)
        assert result == "https://example.com/path"

    def test_http_url(self):
        """HTTP also is valid."""
        schema = FieldSchema(type="url")
        result, _ = coerce_url("http://example.com", schema)
        assert result == "http://example.com"

    def test_url_without_scheme(self):
        """URL without scheme must fail."""
        schema = FieldSchema(type="url")
        with pytest.raises(ValueError, match="scheme"):
            coerce_url("example.com", schema)

    def test_url_invalid_scheme(self):
        """URL with scheme invalid must fail."""
        schema = FieldSchema(type="url")
        with pytest.raises(ValueError, match="invalid scheme"):
            coerce_url("ftp://example.com", schema)


class TestCoerceEnum:
    """Tests for coercion of enum."""

    def test_exact_match(self):
        """Value exact passes."""
        schema = FieldSchema(type="enum", values=["draft", "published"])
        result, _ = coerce_enum("draft", schema)
        assert result == "draft"

    def test_case_insensitive(self):
        """Case insensitive by default."""
        schema = FieldSchema(type="enum", values=["draft", "published"])
        result, warning = coerce_enum("DRAFT", schema)
        assert result == "draft"
        assert "normalized" in warning.lower()

    def test_case_sensitive(self):
        """Matching is case-sensitive when configured."""
        schema = FieldSchema(type="enum", values=["Draft", "Published"], case_insensitive=False)

        result, _ = coerce_enum("Draft", schema)
        assert result == "Draft"

        with pytest.raises(ValueError, match="not in the allowed value list"):
            coerce_enum("draft", schema)

    def test_invalid_value(self):
        """Value not in the list must fail."""
        schema = FieldSchema(type="enum", values=["draft", "published"])
        with pytest.raises(ValueError, match="not in the allowed value list"):
            coerce_enum("archived", schema)


class TestCoerceList:
    """Tests for coercion of list."""

    def test_list_passthrough(self):
        """List passes."""
        schema = FieldSchema(type="list", item_type="string")
        result, _ = coerce_list(["a", "b", "c"], schema)
        assert result == ["a", "b", "c"]

    def test_tuple_to_list(self):
        """A tuple is converted to a list."""
        schema = FieldSchema(type="list", item_type="string")
        result, warning = coerce_list(("a", "b"), schema)
        assert result == ["a", "b"]
        assert "Converted" in warning

    def test_string_to_list(self):
        """A comma-separated string is converted."""
        schema = FieldSchema(type="list", item_type="string")
        result, warning = coerce_list("a, b, c", schema)
        assert result == ["a", "b", "c"]
        assert "converted" in warning.lower()

    def test_list_item_type_int(self):
        """Items are converted to integers."""
        schema = FieldSchema(type="list", item_type="int")
        result, _ = coerce_list(["1", "2", "3"], schema)
        assert result == [1, 2, 3]

    def test_max_items_validation(self):
        """A list containing too many items is rejected."""
        schema = FieldSchema(type="list", item_type="string", max_items=3)
        with pytest.raises(ValueError, match="maximum"):
            coerce_list(["a", "b", "c", "d", "e"], schema)


# =============================================================================
# Tests for FrontmatterValidator
# =============================================================================


class TestFrontmatterValidator:
    """Tests for FrontmatterValidator."""

    @pytest.fixture
    def basic_config(self) -> FrontmatterSchemaConfig:
        """Config basic for tests."""
        return FrontmatterSchemaConfig(
            enabled=True,
            mode="lenient",
            allow_extra_fields=True,
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
                "status": FieldSchema(
                    type="enum",
                    values=["draft", "published"],
                    on_missing="suggest",
                ),
                "tags": FieldSchema(type="list", item_type="string", on_missing="ignore"),
            },
        )

    @pytest.fixture
    def auto_generate_config(self) -> FrontmatterSchemaConfig:
        """Return a test configuration with automatic generation."""
        return FrontmatterSchemaConfig(
            enabled=True,
            mode="lenient",
            schema={
                "id": FieldSchema(type="uuid", on_missing="auto"),
                "created_at": FieldSchema(
                    type="datetime",
                    on_missing="auto",
                    aliases=["created", "date"],
                ),
            },
        )

    def test_disabled_config_passthrough(self):
        """Config disabled passes data without modification."""
        config = FrontmatterSchemaConfig(enabled=False)
        validator = FrontmatterValidator(config)

        result = validator.validate({"any": "data"})

        assert result["valid"] is True
        assert result["validated_data"] == {"any": "data"}

    def test_required_field_missing(self, basic_config):
        """Field required missing generates error."""
        validator = FrontmatterValidator(basic_config)

        result = validator.validate({"status": "draft"})

        assert result["valid"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["field"] == "title"
        assert result["errors"][0]["code"] == "required_missing"

    def test_required_field_present(self, basic_config):
        """Field required present passes."""
        validator = FrontmatterValidator(basic_config)

        result = validator.validate({"title": "My Note", "status": "draft"})

        assert result["valid"] is True
        assert result["validated_data"]["title"] == "My Note"

    def test_suggest_generates_suggestion(self, basic_config):
        """A field with on_missing=suggest generates a suggestion."""
        validator = FrontmatterValidator(basic_config)

        result = validator.validate({"title": "My Note"})

        assert result["valid"] is True
        assert len(result["suggestions"]) == 1
        assert result["suggestions"][0]["field"] == "status"

    def test_auto_generates_uuid(self, auto_generate_config):
        """on_missing=auto generates UUID for field missing."""
        validator = FrontmatterValidator(auto_generate_config)

        result = validator.validate({})

        assert result["valid"] is True
        assert "id" in result["auto_generated"]
        assert "id" in result["validated_data"]
        # UUID v7 has format specific
        uuid = result["auto_generated"]["id"]
        assert uuid[14] == "7"  # Version 7

    def test_auto_generates_datetime(self, auto_generate_config):
        """on_missing=auto generates datetime for field missing."""
        validator = FrontmatterValidator(auto_generate_config)

        result = validator.validate({})

        assert "created_at" in result["auto_generated"]
        # Must be ISO datetime
        assert "T" in result["auto_generated"]["created_at"]

    def test_alias_resolved(self, auto_generate_config):
        """Alias is resolved for name canonical."""
        validator = FrontmatterValidator(auto_generate_config)

        # Using alias "created" to the instead of "created_at"
        result = validator.validate({"created": "2024-01-15T10:00:00"})

        assert result["valid"] is True
        assert "created_at" in result["validated_data"]
        assert result["validated_data"]["created_at"] == "2024-01-15T10:00:00"
        # Must have warning about alias
        alias_warnings = [w for w in result["warnings"] if w["code"] == "alias_resolved"]
        assert len(alias_warnings) == 1

    def test_coercion_warning(self, basic_config):
        """Coercion generates warning."""
        validator = FrontmatterValidator(basic_config)

        # status with case different
        result = validator.validate({"title": "Test", "status": "DRAFT"})

        assert result["valid"] is True
        assert result["validated_data"]["status"] == "draft"
        coercion_warnings = [w for w in result["warnings"] if w["code"] == "coercion_warning"]
        assert len(coercion_warnings) == 1

    def test_extra_fields_allowed(self, basic_config):
        """Extra fields are allowed by default."""
        validator = FrontmatterValidator(basic_config)

        result = validator.validate(
            {
                "title": "Test",
                "custom_field": "value",
            }
        )

        assert result["valid"] is True
        assert "custom_field" in result["validated_data"]

    def test_extra_fields_not_allowed(self):
        """Extra fields produce an error when disallowed."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            allow_extra_fields=False,
            schema={
                "title": FieldSchema(type="string"),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate(
            {
                "title": "Test",
                "custom_field": "value",
            }
        )

        assert result["valid"] is False
        assert any(e["code"] == "extra_field_not_allowed" for e in result["errors"])

    def test_warn_only_mode(self):
        """Mode warn_only converts errors in warnings."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            mode="warn_only",
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({})

        assert result["valid"] is True  # Always valid in warn_only
        assert len(result["errors"]) == 0
        assert len(result["warnings"]) >= 1  # The error became a warning.

    def test_merge_auto_generated(self, auto_generate_config):
        """merge_auto_generated combines values correctly."""
        validator = FrontmatterValidator(auto_generate_config)

        original = {"title": "Test"}
        validation_result = validator.validate(original)

        merged = validator.merge_auto_generated(original, validation_result)

        # Auto-generated must be present
        assert "id" in merged
        assert "created_at" in merged
        # Original also
        assert "title" in merged

    def test_null_frontmatter(self, basic_config):
        """None frontmatter must be handled as an empty dictionary."""
        validator = FrontmatterValidator(basic_config)

        result = validator.validate(None)

        # Must generate error of required missing for title
        assert result["valid"] is False
        assert any(e["field"] == "title" for e in result["errors"])


class TestValidationType:
    """Tests for validation of types specific."""

    def test_int_validation(self):
        """Validation of int with constraints."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "priority": FieldSchema(type="int", minimum=1, maximum=5),
            },
        )
        validator = FrontmatterValidator(config)

        # Value valid
        result = validator.validate({"priority": 3})
        assert result["valid"] is True
        assert result["validated_data"]["priority"] == 3

        # Value invalid
        result = validator.validate({"priority": 10})
        assert result["valid"] is False

    def test_url_validation(self):
        """Validation of URL."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "source": FieldSchema(type="url"),
            },
        )
        validator = FrontmatterValidator(config)

        # URL valid
        result = validator.validate({"source": "https://example.com"})
        assert result["valid"] is True

        # URL invalid
        result = validator.validate({"source": "not-a-url"})
        assert result["valid"] is False


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and scenarios boundary."""

    def test_coerce_enum_from_int(self):
        """An enum accepts an integer converted to a string."""
        schema = FieldSchema(type="enum", values=["1", "2", "3"])
        result, warning = coerce_enum(1, schema)
        assert result == "1"

    def test_coerce_list_min_items(self):
        """A list containing fewer than the minimum number of items is rejected."""
        schema = FieldSchema(type="list", item_type="string", min_items=3)
        with pytest.raises(ValueError, match="minimum"):
            coerce_list(["a", "b"], schema)

    def test_coerce_list_empty_allowed(self):
        """An empty list is allowed when min_items is undefined."""
        schema = FieldSchema(type="list", item_type="string")
        result, _ = coerce_list([], schema)
        assert result == []

    def test_coerce_int_from_float_string(self):
        """A decimal string is truncated when converted to an integer."""
        schema = FieldSchema(type="int")
        result, warning = coerce_int("3.7", schema)
        assert result == 3
        assert warning is not None

    def test_coerce_date_from_datetime_string(self):
        """String datetime complete must be truncated for date."""
        schema = FieldSchema(type="date")
        result, warning = coerce_date("2024-01-15T10:30:00", schema)
        assert result == "2024-01-15"

    def test_coerce_datetime_from_date_string(self):
        """A date-only string expands to a datetime."""
        schema = FieldSchema(type="datetime")
        result, _ = coerce_datetime("2024-01-15", schema)
        # Python 3.11+ datetime.fromisoformat accepts "YYYY-MM-DD"
        assert result == "2024-01-15T00:00:00"

    def test_coerce_url_without_domain(self):
        """URL without domain must fail."""
        schema = FieldSchema(type="url")
        with pytest.raises(ValueError, match="domain"):
            coerce_url("http://", schema)

    def test_coerce_float_from_int_in_warning(self):
        """Int for float must not generate warning (conversion without loss)."""
        schema = FieldSchema(type="float")
        result, warning = coerce_float(42, schema)
        assert result == 42.0
        assert warning is None

    def test_alias_and_canonical_both_present(self):
        """If alias and canonical both present, uses canonical."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "created_at": FieldSchema(
                    type="datetime",
                    aliases=["created"],
                ),
            },
        )
        validator = FrontmatterValidator(config)

        # When both are present, the canonical field has priority.
        result = validator.validate(
            {
                "created_at": "2024-01-15T10:00:00",
                "created": "2024-01-01T00:00:00",  # Ignored
            }
        )

        assert result["valid"] is True
        assert result["validated_data"]["created_at"] == "2024-01-15T10:00:00"

    def test_suggest_with_default_value(self):
        """Field suggest with default must use default in validated_data."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "status": FieldSchema(
                    type="enum",
                    values=["draft", "published"],
                    on_missing="suggest",
                    default="draft",
                ),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({})

        assert result["valid"] is True
        assert len(result["suggestions"]) == 1
        # Default is applied in validated_data
        assert result["validated_data"].get("status") == "draft"

    def test_empty_string_coercion(self):
        """An empty string passes when min_length is undefined."""
        schema = FieldSchema(type="string")
        result, _ = coerce_string("", schema)
        assert result == ""

    def test_whitespace_string_trimmed(self):
        """Strings preserve whitespace; only integer and float coercion strips it."""
        schema = FieldSchema(type="string")
        result, _ = coerce_string("  hello  ", schema)
        assert result == "  hello  "  # Does not trim

    def test_list_from_set_loses_order(self):
        """Converting a set to a list loses order as expected."""
        schema = FieldSchema(type="list", item_type="int")
        result, _ = coerce_list({3, 1, 2}, schema)
        # Order is not guaranteed, but every value is present.
        assert sorted(result) == [1, 2, 3]

    def test_negative_int_validation(self):
        """A negative integer must pass when the minimum allows it."""
        schema = FieldSchema(type="int", minimum=-10)
        result, _ = coerce_int(-5, schema)
        assert result == -5

    def test_float_nan_rejected(self):
        """NaN is rejected to prevent problems in JSON and search."""
        import math

        schema = FieldSchema(type="float", minimum=0, maximum=100)
        # NaN is rejected before of validation of range
        with pytest.raises(ValueError, match="NaN is not a valid frontmatter float"):
            coerce_float(math.nan, schema)

    def test_float_infinity_rejected(self):
        """Floating-point infinity is rejected."""
        import math

        schema = FieldSchema(type="float")
        with pytest.raises(ValueError, match="Infinity is not a valid frontmatter float"):
            coerce_float(math.inf, schema)
        with pytest.raises(ValueError, match="Infinity is not a valid frontmatter float"):
            coerce_float(-math.inf, schema)

    def test_pattern_with_multiline(self):
        """A pattern anchored with ^ and $ is applied correctly."""
        schema = FieldSchema(type="string", pattern=r"^[A-Z][a-z]+$")
        result, _ = coerce_string("Hello", schema)
        assert result == "Hello"

        with pytest.raises(ValueError):
            coerce_string("Hello\nWorld", schema)

    def test_multiple_aliases_same_field(self):
        """Multiple aliases must resolve to the same field."""
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

        # Any alias works
        for alias in ["created", "date", "timestamp"]:
            result = validator.validate({alias: "2024-01-15T10:00:00"})
            assert result["valid"] is True
            assert "created_at" in result["validated_data"]

    def test_strict_mode_blocks_on_error(self):
        """Mode strict must block if there are errors."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            mode="strict",
            schema={
                "priority": FieldSchema(type="int", minimum=1, maximum=5),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({"priority": 100})
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_lenient_mode_same_as_strict_for_errors(self):
        """Mode lenient must have same behavior that strict for errors."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            mode="lenient",
            schema={
                "priority": FieldSchema(type="int", minimum=1, maximum=5),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({"priority": 100})
        assert result["valid"] is False  # Lenient also blocks errors

    def test_int_from_very_large_float_rejected(self):
        """A float that becomes infinite must be rejected."""
        schema = FieldSchema(type="int")
        # float("1e309") = inf, that is rejected
        with pytest.raises(ValueError, match="Infinity cannot be converted"):
            coerce_int(float("1e309"), schema)

    def test_int_from_large_number_string_accepted(self):
        """Python accepts arbitrarily large integers parsed from strings."""
        schema = FieldSchema(type="int")
        # Python handles large integers.
        result, _ = coerce_int("99999999999999999999999", schema)
        assert result == 99999999999999999999999

    def test_int_from_nan_string_rejected(self):
        """String 'nan' for int must be rejected."""
        schema = FieldSchema(type="int")
        with pytest.raises(ValueError, match="NaN cannot be converted"):
            coerce_int("nan", schema)

    def test_int_from_inf_string_rejected(self):
        """String 'inf' for int must be rejected."""
        schema = FieldSchema(type="int")
        with pytest.raises(ValueError, match="Infinity cannot be converted"):
            coerce_int("inf", schema)

    def test_alias_conflict_warning(self):
        """A canonical field and its alias together produce a conflict warning."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            mode="lenient",
            schema={
                "created_at": FieldSchema(
                    type="datetime",
                    aliases=["created", "date"],
                ),
            },
        )
        validator = FrontmatterValidator(config)

        # When both are present, the canonical field wins and the alias warns.
        result = validator.validate(
            {
                "created_at": "2024-01-15T10:00:00",
                "created": "2024-01-20T12:00:00",
            }
        )
        assert result["valid"] is True
        assert result["validated_data"]["created_at"] == "2024-01-15T10:00:00"

        # A conflict warning must be present.
        conflict_warnings = [w for w in result["warnings"] if w["code"] == "alias_conflict"]
        assert len(conflict_warnings) == 1
        assert "created" in conflict_warnings[0]["message"]

    def test_date_suffix_truncation_warning(self):
        """A date with an extra suffix produces a truncation warning."""
        schema = FieldSchema(type="date")
        result, warning = coerce_date("2024-01-15T10:30:00Z", schema)
        assert result == "2024-01-15"
        assert warning is not None
        assert "truncated" in warning.lower() or "extra content" in warning.lower()

    def test_enum_from_non_string_preserves_type(self):
        """Conversion of not-string for enum must mention type original."""
        schema = FieldSchema(type="enum", values=["1", "2", "3"])
        result, warning = coerce_enum(2, schema)
        assert result == "2"
        assert warning is not None
        assert "int" in warning.lower()
