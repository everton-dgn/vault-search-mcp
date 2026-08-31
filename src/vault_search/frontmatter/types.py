"""
TypedDicts para o módulo de validação de frontmatter.
"""

from typing import Any, Literal, TypedDict


class ValidationError(TypedDict):
    """Erro ou aviso de validação."""

    field: str
    message: str
    code: str  # unique_error_code para i18n/debugging
    value: Any  # valor que causou o erro (pode ser None)


class ValidationResult(TypedDict):
    """Resultado completo da validação de frontmatter."""

    valid: bool
    errors: list[ValidationError]  # Bloqueiam operação (on_missing: require, tipo inválido)
    warnings: list[ValidationError]  # Coerções aplicadas (tipo convertido)
    suggestions: list[ValidationError]  # Campos sugeridos (on_missing: suggest)
    auto_generated: dict[str, Any]  # Campos gerados (on_missing: auto)
    validated_data: dict[str, Any]  # Dados finais após coerção


# Tipos literais para reuso
FieldType = Literal[
    "string", "int", "float", "bool", "date", "datetime", "uuid", "url", "enum", "list"
]

OnMissingBehavior = Literal["auto", "suggest", "require", "ignore"]

ValidationMode = Literal["strict", "lenient", "warn_only"]
