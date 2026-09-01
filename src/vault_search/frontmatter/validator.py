"""Frontmatter validation backed by a Pydantic schema."""

import logging
from datetime import datetime
from typing import Any

from vault_search.frontmatter.coercion import coerce_value
from vault_search.frontmatter.schema import FieldSchema, FrontmatterSchemaConfig
from vault_search.frontmatter.types import ValidationError, ValidationResult
from vault_search.utils.uuid import generate_uuid7

logger = logging.getLogger(__name__)


class FrontmatterValidator:
    """
    Validate frontmatter against a configurable schema.

    Features:
    - Aliases: multiple names for one canonical field
    - on_missing: auto, suggest, require, ignore
    - Type coercion with warnings
    - strict, lenient, and warn_only modes
    """

    def __init__(self, config: FrontmatterSchemaConfig):
        """
        Initialize the validator with a frontmatter schema.

        Parameters:
            config: frontmatter schema configuration
        """
        self.config = config
        self._alias_map = self._build_alias_map()

    def _build_alias_map(self) -> dict[str, str]:
        """
        Build an alias-to-canonical-field map.

        Returns:
            Mapping from every accepted alias to its canonical field name.
        """
        alias_map = {}
        for field_name, field_schema in self.config.schema.items():
            # A canonical field maps to itself.
            alias_map[field_name.lower()] = field_name
            # Aliases map to the canonical field.
            for alias in field_schema.aliases:
                alias_map[alias.lower()] = field_name
        return alias_map

    def _resolve_aliases(
        self, data: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, str], list[tuple[str, str, str]]]:
        """
        Resolve aliases and return data with canonical field names.

        When both an alias and its canonical field are present, keep the canonical value.

        Returns:
            Tuple of resolved data, used aliases, and conflicts.
        """
        resolved = {}
        used_aliases = {}
        conflicts = []

        for key, value in data.items():
            key_lower = key.lower()
            if key_lower in self._alias_map:
                canonical = self._alias_map[key_lower]
                if canonical not in resolved:
                    resolved[canonical] = value
                    if key_lower != canonical.lower():
                        used_aliases[key] = canonical
                else:
                    # Ignore an alias when its canonical field already exists.
                    if key_lower != canonical.lower():
                        conflicts.append((key, canonical, value))
            else:
                # Preserve fields outside the schema for later policy handling.
                resolved[key] = value

        return resolved, used_aliases, conflicts

    def _auto_generate(self, field_name: str, field_schema: FieldSchema) -> Any:
        """
        Generate a value for a field with on_missing=auto.

        Supports UUID and datetime fields.
        """
        if field_schema.type == "uuid":
            return generate_uuid7()
        elif field_schema.type == "datetime":
            return datetime.now().isoformat()
        else:
            raise ValueError(f"Automatic generation is not supported for '{field_schema.type}'")

    def validate(self, frontmatter: dict[str, Any] | None) -> ValidationResult:
        """
        Validate frontmatter against the configured schema.

        Parameters:
            frontmatter: frontmatter data, or None

        Returns:
            ValidationResult with errors, warnings, suggestions, generated values, and final data
        """
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []
        suggestions: list[ValidationError] = []
        auto_generated: dict[str, Any] = {}
        validated_data: dict[str, Any] = {}

        # Preserve data unchanged when schema validation is disabled.
        if not self.config.enabled:
            return ValidationResult(
                valid=True,
                errors=[],
                warnings=[],
                suggestions=[],
                auto_generated={},
                validated_data=frontmatter or {},
            )

        # Normalize input.
        data = frontmatter or {}

        # Resolve aliases.
        data, used_aliases, conflicts = self._resolve_aliases(data)

        # Report aliases that were resolved.
        for alias, canonical in used_aliases.items():
            warnings.append(
                ValidationError(
                    field=canonical,
                    message=f"Alias '{alias}' resolved to '{canonical}'",
                    code="alias_resolved",
                    value=alias,
                )
            )

        # Report aliases ignored because their canonical fields are present.
        for alias, canonical, ignored_value in conflicts:
            warnings.append(
                ValidationError(
                    field=canonical,
                    message=(
                        f"Conflict: alias '{alias}' was ignored because field "
                        f"'{canonical}' is already present"
                    ),
                    code="alias_conflict",
                    value=ignored_value,
                )
            )

        # Process every schema field.
        for field_name, field_schema in self.config.schema.items():
            if field_name in data:
                # Validate and coerce a present field.
                value = data[field_name]
                try:
                    coerced, warning = coerce_value(value, field_schema)
                    validated_data[field_name] = coerced

                    if warning:
                        warnings.append(
                            ValidationError(
                                field=field_name,
                                message=warning,
                                code="coercion_warning",
                                value=value,
                            )
                        )
                except ValueError as e:
                    errors.append(
                        ValidationError(
                            field=field_name,
                            message=str(e),
                            code="validation_error",
                            value=value,
                        )
                    )
            else:
                # Apply the configured missing-field behavior.
                behavior = field_schema.on_missing

                if behavior == "require":
                    errors.append(
                        ValidationError(
                            field=field_name,
                            message=f"Required field '{field_name}' is missing",
                            code="required_missing",
                            value=None,
                        )
                    )

                elif behavior == "auto":
                    # Generate the value automatically.
                    try:
                        generated = self._auto_generate(field_name, field_schema)
                        auto_generated[field_name] = generated
                        validated_data[field_name] = generated
                    except ValueError as e:
                        errors.append(
                            ValidationError(
                                field=field_name,
                                message=f"Failed to generate a value automatically: {e}",
                                code="auto_generate_failed",
                                value=None,
                            )
                        )

                elif behavior == "suggest":
                    suggestions.append(
                        ValidationError(
                            field=field_name,
                            message=(
                                f"Suggestion: add field '{field_name}' (type: {field_schema.type})"
                            ),
                            code="field_suggested",
                            value=field_schema.default,
                        )
                    )
                    # Use the configured default when available.
                    if field_schema.default is not None:
                        validated_data[field_name] = field_schema.default

                # behavior == "ignore": leave the field absent.

        # Handle fields that are not in the schema.
        for key, value in data.items():
            if key not in self.config.schema and key not in validated_data:
                if self.config.allow_extra_fields:
                    validated_data[key] = value
                else:
                    errors.append(
                        ValidationError(
                            field=key,
                            message=f"Field '{key}' is not allowed by the schema",
                            code="extra_field_not_allowed",
                            value=value,
                        )
                    )

        # Determine validity from the configured mode.
        if self.config.mode == "strict":
            valid = len(errors) == 0
        elif self.config.mode == "lenient":
            valid = len(errors) == 0
        elif self.config.mode == "warn_only":
            # warn_only converts every error into a warning.
            warnings.extend(errors)
            errors = []
            valid = True
        else:
            valid = len(errors) == 0

        return ValidationResult(
            valid=valid,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
            auto_generated=auto_generated,
            validated_data=validated_data,
        )

    def merge_auto_generated(
        self,
        frontmatter: dict[str, Any] | None,
        validation_result: ValidationResult,
    ) -> dict[str, Any]:
        """
        Merge original frontmatter with automatically generated fields.

        Return a dictionary ready for serialization.
        """
        result = {}

        # Generated fields come first in serialized frontmatter.
        for key, value in validation_result["auto_generated"].items():
            result[key] = value

        # Original fields follow and never overwrite generated values.
        if frontmatter:
            for key, value in frontmatter.items():
                if key not in result:
                    result[key] = value

        return result
