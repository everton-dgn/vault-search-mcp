"""Pydantic models for the frontmatter schema."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from vault_search.frontmatter.types import (
    FieldType,
    OnMissingBehavior,
    ValidationMode,
)


class FieldSchema(BaseModel):
    """
    Schema for one frontmatter field.

    Defines the type, missing-field behavior, and type-specific constraints.
    """

    type: FieldType = Field(description="Field type")
    on_missing: OnMissingBehavior = Field(
        default="ignore",
        description="Behavior when the field is missing",
    )

    # Default value (used only when on_missing != auto).
    default: Any = Field(default=None, description="Default value when missing")

    # String constraints.
    min_length: int | None = Field(default=None, ge=0, description="Minimum length")
    max_length: int | None = Field(default=None, ge=1, description="Maximum length")
    pattern: str | None = Field(default=None, description="Validation regex pattern")

    # Enum constraints.
    values: list[str] | None = Field(default=None, description="Allowed enum values")
    case_insensitive: bool = Field(
        default=True,
        description="Use case-insensitive enum matching",
    )

    # List constraints.
    item_type: Literal["string", "int", "float"] | None = Field(
        default=None,
        description="List item type",
    )
    min_items: int | None = Field(default=None, ge=0, description="Minimum list length")
    max_items: int | None = Field(default=None, ge=1, description="Maximum list length")

    # Numeric constraints (int/float).
    minimum: float | None = Field(default=None, description="Minimum value")
    maximum: float | None = Field(default=None, description="Maximum value")

    # Aliases (alternative names for the field).
    aliases: list[str] = Field(
        default_factory=list,
        description="Accepted alternative names for this field",
    )

    @field_validator("values")
    @classmethod
    def values_not_empty(cls, v: list[str] | None) -> list[str] | None:
        """Reject an explicitly empty enum value list."""
        if v is not None and len(v) == 0:
            raise ValueError("values cannot be an empty list")
        return v

    @model_validator(mode="after")
    def validate_type_constraints(self) -> FieldSchema:
        """Ensure constraints are compatible with the selected field type."""
        # Enum fields require values.
        if self.type == "enum" and not self.values:
            raise ValueError("type='enum' requires 'values'")

        # List fields may specify an item type.
        if self.type == "list" and self.item_type is None:
            # Default to strings when no item type is specified.
            object.__setattr__(self, "item_type", "string")

        # Automatic generation is defined only for UUIDs and datetimes.
        if self.on_missing == "auto" and self.type not in ("uuid", "datetime"):
            raise ValueError(
                f"on_missing='auto' is supported only for 'uuid' and 'datetime', not '{self.type}'"
            )

        # Numeric constraints apply only to int and float fields.
        if self.type not in ("int", "float"):
            if self.minimum is not None or self.maximum is not None:
                raise ValueError(
                    f"minimum/maximum apply only to 'int' or 'float', not '{self.type}'"
                )

        # String-length constraints apply only to string fields.
        if self.type != "string":
            if self.min_length is not None or self.max_length is not None:
                raise ValueError(f"min_length/max_length apply only to 'string', not '{self.type}'")

        return self


class FrontmatterSchemaConfig(BaseModel):
    """
    Complete frontmatter schema configuration.

    Defines fields, validation mode, and extra-field behavior.
    """

    enabled: bool = Field(
        default=False,
        description="Enable schema validation",
    )
    mode: ValidationMode = Field(
        default="lenient",
        description="Validation mode: strict, lenient, or warn_only",
    )
    allow_extra_fields: bool = Field(
        default=True,
        description="Allow fields that are not defined in the schema",
    )
    schema_fields: dict[str, FieldSchema] = Field(
        default_factory=dict,
        description="Frontmatter field schema",
        alias="schema",  # Accept "schema" in YAML for compatibility.
    )

    # Convenience property for access through .schema.
    @property
    def schema(self) -> dict[str, FieldSchema]:  # type: ignore[override]
        """Expose schema_fields through the public .schema alias."""
        return self.schema_fields

    @field_validator("schema_fields", mode="before")
    @classmethod
    def parse_schema_dict(cls, v: object) -> dict[str, FieldSchema]:
        """Convert nested dictionaries to FieldSchema instances."""
        if not v:
            return {}
        if not isinstance(v, dict):
            raise ValueError("schema must be a dictionary")
        result: dict[str, FieldSchema] = {}
        for field_name, field_config in v.items():
            if not isinstance(field_name, str):
                raise ValueError("schema field names must be strings")
            if isinstance(field_config, FieldSchema):
                result[field_name] = field_config
            elif isinstance(field_config, dict):
                result[field_name] = FieldSchema(**field_config)
            else:
                raise ValueError(
                    f"Field '{field_name}' must be a dict or FieldSchema, "
                    f"got {type(field_config).__name__}"
                )
        return result
