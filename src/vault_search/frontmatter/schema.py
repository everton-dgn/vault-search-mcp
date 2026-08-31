"""
Modelos Pydantic para schema de frontmatter.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from vault_search.frontmatter.types import (
    FieldType,
    OnMissingBehavior,
    ValidationMode,
)


class FieldSchema(BaseModel):
    """
    Schema de um campo do frontmatter.

    Define tipo, comportamento quando ausente, e validações específicas.
    """

    type: FieldType = Field(description="Tipo do campo")
    on_missing: OnMissingBehavior = Field(
        default="ignore",
        description="Comportamento quando campo está ausente",
    )

    # Default value (usado apenas se on_missing != auto)
    default: Any = Field(default=None, description="Valor default se ausente")

    # Validações de string
    min_length: int | None = Field(default=None, ge=0, description="Comprimento mínimo")
    max_length: int | None = Field(default=None, ge=1, description="Comprimento máximo")
    pattern: str | None = Field(default=None, description="Regex pattern para validação")

    # Validações de enum
    values: list[str] | None = Field(default=None, description="Valores permitidos (enum)")
    case_insensitive: bool = Field(
        default=True,
        description="Comparação case-insensitive para enum",
    )

    # Validações de lista
    item_type: Literal["string", "int", "float"] | None = Field(
        default=None,
        description="Tipo dos itens da lista",
    )
    min_items: int | None = Field(default=None, ge=0, description="Mínimo de itens na lista")
    max_items: int | None = Field(default=None, ge=1, description="Máximo de itens na lista")

    # Validações numéricas (int/float)
    minimum: float | None = Field(default=None, description="Valor mínimo")
    maximum: float | None = Field(default=None, description="Valor máximo")

    # Aliases (nomes alternativos para o campo)
    aliases: list[str] = Field(
        default_factory=list,
        description="Nomes alternativos aceitos para este campo",
    )

    @field_validator("values")
    @classmethod
    def values_not_empty(cls, v: list[str] | None) -> list[str] | None:
        """Valida que values não é lista vazia."""
        if v is not None and len(v) == 0:
            raise ValueError("values não pode ser lista vazia")
        return v

    @model_validator(mode="after")
    def validate_type_constraints(self) -> FieldSchema:
        """Valida que constraints são compatíveis com o tipo."""
        # enum requer values
        if self.type == "enum" and not self.values:
            raise ValueError("type='enum' requer 'values' definido")

        # list pode ter item_type
        if self.type == "list" and self.item_type is None:
            # Default para string se não especificado
            object.__setattr__(self, "item_type", "string")

        # auto só faz sentido para uuid e datetime
        if self.on_missing == "auto" and self.type not in ("uuid", "datetime"):
            raise ValueError(
                f"on_missing='auto' só é suportado para 'uuid' e 'datetime', não para '{self.type}'"
            )

        # Validações numéricas só para int/float
        if self.type not in ("int", "float"):
            if self.minimum is not None or self.maximum is not None:
                raise ValueError(
                    f"minimum/maximum só são válidos para 'int' ou 'float', não para '{self.type}'"
                )

        # Validações de string só para string
        if self.type != "string":
            if self.min_length is not None or self.max_length is not None:
                raise ValueError(
                    f"min_length/max_length só são válidos para 'string', não para '{self.type}'"
                )

        return self


class FrontmatterSchemaConfig(BaseModel):
    """
    Configuração completa do schema de frontmatter.

    Define campos, modo de validação e comportamento geral.
    """

    enabled: bool = Field(
        default=False,
        description="Habilitar validação de schema",
    )
    mode: ValidationMode = Field(
        default="lenient",
        description="Modo de validação: strict (bloqueia), lenient (avisa), warn_only",
    )
    allow_extra_fields: bool = Field(
        default=True,
        description="Permitir campos não definidos no schema",
    )
    schema_fields: dict[str, FieldSchema] = Field(
        default_factory=dict,
        description="Schema dos campos do frontmatter",
        alias="schema",  # Aceita "schema" no YAML para compatibilidade
    )

    # Propriedade para acessar como .schema (conveniência)
    @property
    def schema(self) -> dict[str, FieldSchema]:  # type: ignore[override]
        """Alias para schema_fields (conveniência)."""
        return self.schema_fields

    @field_validator("schema_fields", mode="before")
    @classmethod
    def parse_schema_dict(cls, v: object) -> dict[str, FieldSchema]:
        """Converte dicts aninhados para FieldSchema."""
        if not v:
            return {}
        if not isinstance(v, dict):
            raise ValueError("schema deve ser um dicionário")
        result: dict[str, FieldSchema] = {}
        for field_name, field_config in v.items():
            if not isinstance(field_name, str):
                raise ValueError("Nomes de campos do schema devem ser strings")
            if isinstance(field_config, FieldSchema):
                result[field_name] = field_config
            elif isinstance(field_config, dict):
                result[field_name] = FieldSchema(**field_config)
            else:
                raise ValueError(
                    f"Campo '{field_name}' deve ser dict ou FieldSchema, "
                    f"recebido {type(field_config).__name__}"
                )
        return result
