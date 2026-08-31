"""
Typed dictionaries for frontmatter validation.
"""

from typing import Any, Literal, TypedDict


class ValidationError(TypedDict):
    """Validation error or warning."""

    field: str
    message: str
    code: str  # Stable error code for clients and debugging.
    value: Any  # Value that caused the issue, or None.


class ValidationResult(TypedDict):
    """Complete frontmatter validation result."""

    valid: bool
    errors: list[ValidationError]  # Blocking validation failures.
    warnings: list[ValidationError]  # Applied coercions and non-blocking issues.
    suggestions: list[ValidationError]  # Suggested fields (on_missing: suggest).
    auto_generated: dict[str, Any]  # Generated fields (on_missing: auto).
    validated_data: dict[str, Any]  # Final data after coercion.


# Reusable literal types.
FieldType = Literal[
    "string", "int", "float", "bool", "date", "datetime", "uuid", "url", "enum", "list"
]

OnMissingBehavior = Literal["auto", "suggest", "require", "ignore"]

ValidationMode = Literal["strict", "lenient", "warn_only"]
