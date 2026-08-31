"""
Validador de frontmatter com schema Pydantic.
"""

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
    Validador de frontmatter com suporte a schema configurável.

    Features:
    - Aliases: múltiplos nomes para o mesmo campo
    - on_missing: auto, suggest, require, ignore
    - Coerção de tipos com warnings
    - Modo strict/lenient/warn_only
    """

    def __init__(self, config: FrontmatterSchemaConfig):
        """
        Inicializa validador com configuração de schema.

        Parâmetros:
            config: configuração do schema de frontmatter
        """
        self.config = config
        self._alias_map = self._build_alias_map()

    def _build_alias_map(self) -> dict[str, str]:
        """
        Constrói mapa de alias -> campo canônico.

        Retorna:
            Dict mapeando cada alias para o nome canônico do campo.
        """
        alias_map = {}
        for field_name, field_schema in self.config.schema.items():
            # Campo canônico mapeia para si mesmo
            alias_map[field_name.lower()] = field_name
            # Aliases mapeiam para o canônico
            for alias in field_schema.aliases:
                alias_map[alias.lower()] = field_name
        return alias_map

    def _resolve_aliases(
        self, data: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, str], list[tuple[str, str, str]]]:
        """
        Resolve aliases nos dados, retornando dict com nomes canônicos.

        Se há conflito (campo e alias ambos presentes), usa o canônico e reporta.

        Retorna:
            Tupla (resolved_data, used_aliases, conflicts) onde:
            - resolved_data: frontmatter com nomes canônicos
            - used_aliases: mapa de alias usados -> nome canônico
            - conflicts: lista de (alias, canonical, ignored_value) para conflitos
        """
        resolved = {}
        used_aliases = {}  # Track quais aliases foram usados
        conflicts = []  # Track conflitos (alias ignorado porque canônico já existe)

        for key, value in data.items():
            key_lower = key.lower()
            if key_lower in self._alias_map:
                canonical = self._alias_map[key_lower]
                if canonical not in resolved:
                    resolved[canonical] = value
                    if key_lower != canonical.lower():
                        used_aliases[key] = canonical
                else:
                    # Conflito: canonical já existe, alias ignorado
                    if key_lower != canonical.lower():
                        conflicts.append((key, canonical, value))
            else:
                # Campo não está no schema, mantém como está
                resolved[key] = value

        return resolved, used_aliases, conflicts

    def _auto_generate(self, field_name: str, field_schema: FieldSchema) -> Any:
        """
        Gera valor automático para campo com on_missing=auto.

        Suporta: uuid, datetime.
        """
        if field_schema.type == "uuid":
            return generate_uuid7()
        elif field_schema.type == "datetime":
            return datetime.now().isoformat()
        else:
            raise ValueError(f"Auto-geração não suportada para tipo '{field_schema.type}'")

    def validate(self, frontmatter: dict[str, Any] | None) -> ValidationResult:
        """
        Valida frontmatter contra o schema configurado.

        Parâmetros:
            frontmatter: dados do frontmatter (pode ser None)

        Retorna:
            ValidationResult com valid, errors, warnings, suggestions, auto_generated, validated_data
        """
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []
        suggestions: list[ValidationError] = []
        auto_generated: dict[str, Any] = {}
        validated_data: dict[str, Any] = {}

        # Se schema não está habilitado, retorna dados como estão
        if not self.config.enabled:
            return ValidationResult(
                valid=True,
                errors=[],
                warnings=[],
                suggestions=[],
                auto_generated={},
                validated_data=frontmatter or {},
            )

        # Normaliza input
        data = frontmatter or {}

        # Resolve aliases
        data, used_aliases, conflicts = self._resolve_aliases(data)

        # Adiciona warnings para aliases usados
        for alias, canonical in used_aliases.items():
            warnings.append(
                ValidationError(
                    field=canonical,
                    message=f"Alias '{alias}' resolvido para '{canonical}'",
                    code="alias_resolved",
                    value=alias,
                )
            )

        # Adiciona warnings para conflitos (alias ignorado)
        for alias, canonical, ignored_value in conflicts:
            warnings.append(
                ValidationError(
                    field=canonical,
                    message=f"Conflito: alias '{alias}' ignorado porque campo '{canonical}' já existe",
                    code="alias_conflict",
                    value=ignored_value,
                )
            )

        # Processa cada campo do schema
        for field_name, field_schema in self.config.schema.items():
            if field_name in data:
                # Campo presente - validar e coercir
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
                # Campo ausente - verificar on_missing
                behavior = field_schema.on_missing

                if behavior == "require":
                    errors.append(
                        ValidationError(
                            field=field_name,
                            message=f"Campo obrigatório '{field_name}' não encontrado",
                            code="required_missing",
                            value=None,
                        )
                    )

                elif behavior == "auto":
                    # Gerar automaticamente
                    try:
                        generated = self._auto_generate(field_name, field_schema)
                        auto_generated[field_name] = generated
                        validated_data[field_name] = generated
                    except ValueError as e:
                        errors.append(
                            ValidationError(
                                field=field_name,
                                message=f"Falha ao gerar valor automático: {e}",
                                code="auto_generate_failed",
                                value=None,
                            )
                        )

                elif behavior == "suggest":
                    suggestions.append(
                        ValidationError(
                            field=field_name,
                            message=f"Sugestão: adicionar campo '{field_name}' (tipo: {field_schema.type})",
                            code="field_suggested",
                            value=field_schema.default,
                        )
                    )
                    # Se tem default, usa
                    if field_schema.default is not None:
                        validated_data[field_name] = field_schema.default

                # behavior == "ignore": não faz nada

        # Campos extras (não no schema)
        for key, value in data.items():
            if key not in self.config.schema and key not in validated_data:
                if self.config.allow_extra_fields:
                    validated_data[key] = value
                else:
                    errors.append(
                        ValidationError(
                            field=key,
                            message=f"Campo '{key}' não permitido pelo schema",
                            code="extra_field_not_allowed",
                            value=value,
                        )
                    )

        # Determina validade baseado no modo
        if self.config.mode == "strict":
            valid = len(errors) == 0
        elif self.config.mode == "lenient":
            valid = len(errors) == 0
        elif self.config.mode == "warn_only":
            # warn_only: sempre válido, erros viram warnings
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
        Merge frontmatter original com campos auto-gerados.

        Retorna dict pronto para serialização.
        """
        result = {}

        # Auto-gerados primeiro (aparecem no topo do frontmatter)
        for key, value in validation_result["auto_generated"].items():
            result[key] = value

        # Depois campos originais (não sobrescreve auto-gerados)
        if frontmatter:
            for key, value in frontmatter.items():
                if key not in result:
                    result[key] = value

        return result
