"""
Frontmatter validation with a Pydantic schema.

Exports:
    - FrontmatterValidator: primary validator
    - FieldSchema: schema for an individual field
    - FrontmatterSchemaConfig: schema configuration
    - ValidationResult: complete validation result
    - ValidationError: validation error or warning
"""

from vault_search.frontmatter.enrichment import (
    FrontmatterEnrichmentConfigError,
    FrontmatterEnrichmentError,
    generate_required_fields_with_ai,
    get_required_schema_fields,
)
from vault_search.frontmatter.schema import (
    FieldSchema,
    FrontmatterSchemaConfig,
)
from vault_search.frontmatter.types import (
    ValidationError,
    ValidationResult,
)
from vault_search.frontmatter.validator import FrontmatterValidator

__all__ = [
    "FrontmatterValidator",
    "FieldSchema",
    "FrontmatterSchemaConfig",
    "ValidationError",
    "ValidationResult",
    "FrontmatterEnrichmentError",
    "FrontmatterEnrichmentConfigError",
    "generate_required_fields_with_ai",
    "get_required_schema_fields",
]
