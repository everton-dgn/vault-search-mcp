"""
Testes para o módulo de validação de frontmatter com schema Pydantic.

Cobre:
- Modelos Pydantic (FieldSchema, FrontmatterSchemaConfig)
- Funções de coerção de tipos
- FrontmatterValidator
- Integração com CRUD
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
    """Testes para modelo Pydantic FieldSchema."""

    def test_minimal_schema(self):
        """Schema mínimo só requer type."""
        schema = FieldSchema(type="string")
        assert schema.type == "string"
        assert schema.on_missing == "ignore"
        assert schema.default is None

    def test_all_field_types(self):
        """Todos os tipos de campo devem ser válidos."""
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
        """Todos os comportamentos on_missing devem ser válidos."""
        behaviors: list[OnMissingBehavior] = ["auto", "suggest", "require", "ignore"]
        for behavior in behaviors:
            if behavior == "auto":
                # auto só suporta uuid e datetime
                schema = FieldSchema(type="uuid", on_missing=behavior)
            else:
                schema = FieldSchema(type="string", on_missing=behavior)
            assert schema.on_missing == behavior

    def test_enum_requires_values(self):
        """type='enum' sem values deve falhar."""
        with pytest.raises(ValueError, match="requer 'values'"):
            FieldSchema(type="enum")

    def test_enum_with_empty_values_fails(self):
        """type='enum' com values vazia deve falhar."""
        with pytest.raises(ValueError, match="não pode ser lista vazia"):
            FieldSchema(type="enum", values=[])

    def test_auto_only_for_uuid_datetime(self):
        """on_missing='auto' só é válido para uuid e datetime."""
        # uuid ok
        FieldSchema(type="uuid", on_missing="auto")
        # datetime ok
        FieldSchema(type="datetime", on_missing="auto")

        # outros tipos devem falhar
        with pytest.raises(ValueError, match="só é suportado"):
            FieldSchema(type="string", on_missing="auto")

    def test_string_constraints(self):
        """Constraints de string devem ser validadas."""
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
        """Constraints numéricas devem ser validadas."""
        schema = FieldSchema(type="int", minimum=0, maximum=100)
        assert schema.minimum == 0
        assert schema.maximum == 100

    def test_numeric_constraints_invalid_for_string(self):
        """minimum/maximum não são válidos para string."""
        with pytest.raises(ValueError, match="só são válidos para"):
            FieldSchema(type="string", minimum=0)

    def test_string_constraints_invalid_for_int(self):
        """min_length/max_length não são válidos para int."""
        with pytest.raises(ValueError, match="só são válidos para"):
            FieldSchema(type="int", min_length=5)

    def test_aliases(self):
        """Aliases devem ser aceitos."""
        schema = FieldSchema(
            type="datetime",
            aliases=["created", "date", "created_at"],
        )
        assert schema.aliases == ["created", "date", "created_at"]

    def test_list_with_item_type(self):
        """Lista com item_type especificado."""
        schema = FieldSchema(type="list", item_type="int", max_items=10)
        assert schema.item_type == "int"
        assert schema.max_items == 10

    def test_list_defaults_to_string_item_type(self):
        """Lista sem item_type deve usar string como default."""
        schema = FieldSchema(type="list")
        assert schema.item_type == "string"


# =============================================================================
# Tests for FrontmatterSchemaConfig
# =============================================================================


class TestFrontmatterSchemaConfig:
    """Testes para modelo Pydantic FrontmatterSchemaConfig."""

    def test_default_config(self):
        """Config padrão deve estar desabilitada."""
        config = FrontmatterSchemaConfig()
        assert config.enabled is False
        assert config.mode == "lenient"
        assert config.allow_extra_fields is True
        assert config.schema == {}

    def test_config_with_schema(self):
        """Config com schema deve parsear campos."""
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
        """Todos os modos de validação devem ser válidos."""
        modes: list[ValidationMode] = ["strict", "lenient", "warn_only"]
        for mode in modes:
            config = FrontmatterSchemaConfig(mode=mode)
            assert config.mode == mode


# =============================================================================
# Tests for Coercion Functions
# =============================================================================


class TestCoerceString:
    """Testes para coerção de string."""

    def test_string_passthrough(self):
        """String passa sem modificação."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string("hello", schema)
        assert result == "hello"
        assert warning is None

    def test_int_to_string(self):
        """Int é convertido para string."""
        schema = FieldSchema(type="string")
        result, warning = coerce_string(42, schema)
        assert result == "42"
        assert "Convertido" in warning

    def test_min_length_validation(self):
        """String muito curta deve falhar."""
        schema = FieldSchema(type="string", min_length=5)
        with pytest.raises(ValueError, match="muito curta"):
            coerce_string("abc", schema)

    def test_max_length_validation(self):
        """String muito longa deve falhar."""
        schema = FieldSchema(type="string", max_length=5)
        with pytest.raises(ValueError, match="muito longa"):
            coerce_string("abcdefgh", schema)

    def test_pattern_validation(self):
        """String deve corresponder ao pattern."""
        schema = FieldSchema(type="string", pattern=r"^[A-Z][a-z]+$")

        result, _ = coerce_string("Hello", schema)
        assert result == "Hello"

        with pytest.raises(ValueError, match="pattern"):
            coerce_string("hello", schema)


class TestCoerceInt:
    """Testes para coerção de int."""

    def test_int_passthrough(self):
        """Int passa sem modificação."""
        schema = FieldSchema(type="int")
        result, warning = coerce_int(42, schema)
        assert result == 42
        assert warning is None

    def test_float_truncated(self):
        """Float é truncado para int."""
        schema = FieldSchema(type="int")
        result, warning = coerce_int(3.7, schema)
        assert result == 3
        assert "truncado" in warning.lower()

    def test_string_to_int(self):
        """String numérica é convertida."""
        schema = FieldSchema(type="int")
        result, warning = coerce_int("42", schema)
        assert result == 42
        # Warning pode ser None se conversão foi direta (int() success)

    def test_bool_to_int(self):
        """Bool é convertido para 0/1."""
        schema = FieldSchema(type="int")
        result_true, _ = coerce_int(True, schema)
        result_false, _ = coerce_int(False, schema)
        assert result_true == 1
        assert result_false == 0

    def test_minimum_validation(self):
        """Valor abaixo do mínimo deve falhar."""
        schema = FieldSchema(type="int", minimum=10)
        with pytest.raises(ValueError, match="menor que mínimo"):
            coerce_int(5, schema)

    def test_maximum_validation(self):
        """Valor acima do máximo deve falhar."""
        schema = FieldSchema(type="int", maximum=100)
        with pytest.raises(ValueError, match="maior que máximo"):
            coerce_int(150, schema)


class TestCoerceFloat:
    """Testes para coerção de float."""

    def test_float_passthrough(self):
        """Float passa sem modificação."""
        schema = FieldSchema(type="float")
        result, warning = coerce_float(3.14, schema)
        assert result == 3.14
        assert warning is None

    def test_int_to_float(self):
        """Int é convertido para float."""
        schema = FieldSchema(type="float")
        result, warning = coerce_float(42, schema)
        assert result == 42.0
        assert warning is None  # Conversão sem perda

    def test_string_to_float(self):
        """String numérica é convertida."""
        schema = FieldSchema(type="float")
        result, warning = coerce_float("3.14", schema)
        assert result == 3.14
        assert "convertida" in warning.lower()


class TestCoerceBool:
    """Testes para coerção de bool."""

    def test_bool_passthrough(self):
        """Bool passa sem modificação."""
        schema = FieldSchema(type="bool")
        result_true, _ = coerce_bool(True, schema)
        result_false, _ = coerce_bool(False, schema)
        assert result_true is True
        assert result_false is False

    def test_string_truthy(self):
        """Strings truthy são convertidas para True."""
        schema = FieldSchema(type="bool")
        for value in ["true", "yes", "1", "on", "sim", "True", "YES"]:
            result, _ = coerce_bool(value, schema)
            assert result is True, f"'{value}' deveria ser True"

    def test_string_falsy(self):
        """Strings falsy são convertidas para False."""
        schema = FieldSchema(type="bool")
        for value in ["false", "no", "0", "off", "nao", "não", "falso"]:
            result, _ = coerce_bool(value, schema)
            assert result is False, f"'{value}' deveria ser False"

    def test_int_0_1(self):
        """Int 0/1 são convertidos para bool."""
        schema = FieldSchema(type="bool")
        result_1, _ = coerce_bool(1, schema)
        result_0, _ = coerce_bool(0, schema)
        assert result_1 is True
        assert result_0 is False

    def test_invalid_int(self):
        """Int diferente de 0/1 deve falhar."""
        schema = FieldSchema(type="bool")
        with pytest.raises(ValueError, match="use 0 ou 1"):
            coerce_bool(42, schema)


class TestCoerceDate:
    """Testes para coerção de date."""

    def test_date_passthrough(self):
        """Date é formatado como ISO."""
        schema = FieldSchema(type="date")
        result, warning = coerce_date(date(2024, 1, 15), schema)
        assert result == "2024-01-15"
        assert warning is None

    def test_datetime_truncated(self):
        """Datetime é truncado para date."""
        schema = FieldSchema(type="date")
        result, warning = coerce_date(datetime(2024, 1, 15, 10, 30), schema)
        assert result == "2024-01-15"
        assert "truncado" in warning.lower()

    def test_string_iso(self):
        """String ISO é parseada."""
        schema = FieldSchema(type="date")
        result, _ = coerce_date("2024-01-15", schema)
        assert result == "2024-01-15"

    def test_invalid_string(self):
        """String inválida deve falhar."""
        schema = FieldSchema(type="date")
        with pytest.raises(ValueError, match="data válida"):
            coerce_date("not-a-date", schema)


class TestCoerceDatetime:
    """Testes para coerção de datetime."""

    def test_datetime_passthrough(self):
        """Datetime é formatado como ISO."""
        schema = FieldSchema(type="datetime")
        result, _ = coerce_datetime(datetime(2024, 1, 15, 10, 30, 45), schema)
        assert result == "2024-01-15T10:30:45"

    def test_date_expanded(self):
        """Date é expandido para datetime com hora 00:00:00."""
        schema = FieldSchema(type="datetime")
        result, warning = coerce_datetime(date(2024, 1, 15), schema)
        assert result == "2024-01-15T00:00:00"
        assert "expandido" in warning.lower()

    def test_string_iso(self):
        """String ISO é parseada."""
        schema = FieldSchema(type="datetime")
        result, _ = coerce_datetime("2024-01-15T10:30:00", schema)
        assert result == "2024-01-15T10:30:00"


class TestCoerceUuid:
    """Testes para coerção de UUID."""

    def test_valid_uuid(self):
        """UUID válido passa."""
        schema = FieldSchema(type="uuid")
        result, _ = coerce_uuid("550e8400-e29b-41d4-a716-446655440000", schema)
        assert result == "550e8400-e29b-41d4-a716-446655440000"

    def test_uuid_normalized(self):
        """UUID com case diferente é normalizado."""
        schema = FieldSchema(type="uuid")
        result, warning = coerce_uuid("550E8400-E29B-41D4-A716-446655440000", schema)
        assert result == "550e8400-e29b-41d4-a716-446655440000"
        # Warning pode ser gerado se valor foi normalizado
        if warning:
            assert "normalizado" in warning.lower()

    def test_invalid_uuid(self):
        """UUID inválido deve falhar."""
        schema = FieldSchema(type="uuid")
        with pytest.raises(ValueError, match="UUID válido"):
            coerce_uuid("not-a-uuid", schema)


class TestCoerceUrl:
    """Testes para coerção de URL."""

    def test_valid_url(self):
        """URL válida passa."""
        schema = FieldSchema(type="url")
        result, _ = coerce_url("https://example.com/path", schema)
        assert result == "https://example.com/path"

    def test_http_url(self):
        """HTTP também é válido."""
        schema = FieldSchema(type="url")
        result, _ = coerce_url("http://example.com", schema)
        assert result == "http://example.com"

    def test_url_without_scheme(self):
        """URL sem scheme deve falhar."""
        schema = FieldSchema(type="url")
        with pytest.raises(ValueError, match="scheme"):
            coerce_url("example.com", schema)

    def test_url_invalid_scheme(self):
        """URL com scheme inválido deve falhar."""
        schema = FieldSchema(type="url")
        with pytest.raises(ValueError, match="scheme inválido"):
            coerce_url("ftp://example.com", schema)


class TestCoerceEnum:
    """Testes para coerção de enum."""

    def test_exact_match(self):
        """Valor exato passa."""
        schema = FieldSchema(type="enum", values=["draft", "published"])
        result, _ = coerce_enum("draft", schema)
        assert result == "draft"

    def test_case_insensitive(self):
        """Case insensitive por padrão."""
        schema = FieldSchema(type="enum", values=["draft", "published"])
        result, warning = coerce_enum("DRAFT", schema)
        assert result == "draft"
        assert "normalizado" in warning.lower()

    def test_case_sensitive(self):
        """Case sensitive quando configurado."""
        schema = FieldSchema(type="enum", values=["Draft", "Published"], case_insensitive=False)

        result, _ = coerce_enum("Draft", schema)
        assert result == "Draft"

        with pytest.raises(ValueError, match="não está na lista"):
            coerce_enum("draft", schema)

    def test_invalid_value(self):
        """Valor não na lista deve falhar."""
        schema = FieldSchema(type="enum", values=["draft", "published"])
        with pytest.raises(ValueError, match="não está na lista"):
            coerce_enum("archived", schema)


class TestCoerceList:
    """Testes para coerção de lista."""

    def test_list_passthrough(self):
        """Lista passa."""
        schema = FieldSchema(type="list", item_type="string")
        result, _ = coerce_list(["a", "b", "c"], schema)
        assert result == ["a", "b", "c"]

    def test_tuple_to_list(self):
        """Tuple é convertido para lista."""
        schema = FieldSchema(type="list", item_type="string")
        result, warning = coerce_list(("a", "b"), schema)
        assert result == ["a", "b"]
        assert "Convertido" in warning

    def test_string_to_list(self):
        """String separada por vírgula é convertida."""
        schema = FieldSchema(type="list", item_type="string")
        result, warning = coerce_list("a, b, c", schema)
        assert result == ["a", "b", "c"]
        assert "convertida" in warning.lower()

    def test_list_item_type_int(self):
        """Itens são convertidos para int."""
        schema = FieldSchema(type="list", item_type="int")
        result, _ = coerce_list(["1", "2", "3"], schema)
        assert result == [1, 2, 3]

    def test_max_items_validation(self):
        """Lista com muitos itens deve falhar."""
        schema = FieldSchema(type="list", item_type="string", max_items=3)
        with pytest.raises(ValueError, match="máximo"):
            coerce_list(["a", "b", "c", "d", "e"], schema)


# =============================================================================
# Tests for FrontmatterValidator
# =============================================================================


class TestFrontmatterValidator:
    """Testes para FrontmatterValidator."""

    @pytest.fixture
    def basic_config(self) -> FrontmatterSchemaConfig:
        """Config básica para testes."""
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
        """Config com auto-geração para testes."""
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
        """Config desabilitada passa dados sem modificação."""
        config = FrontmatterSchemaConfig(enabled=False)
        validator = FrontmatterValidator(config)

        result = validator.validate({"any": "data"})

        assert result["valid"] is True
        assert result["validated_data"] == {"any": "data"}

    def test_required_field_missing(self, basic_config):
        """Campo obrigatório ausente gera erro."""
        validator = FrontmatterValidator(basic_config)

        result = validator.validate({"status": "draft"})

        assert result["valid"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["field"] == "title"
        assert result["errors"][0]["code"] == "required_missing"

    def test_required_field_present(self, basic_config):
        """Campo obrigatório presente passa."""
        validator = FrontmatterValidator(basic_config)

        result = validator.validate({"title": "My Note", "status": "draft"})

        assert result["valid"] is True
        assert result["validated_data"]["title"] == "My Note"

    def test_suggest_generates_suggestion(self, basic_config):
        """Campo com on_missing=suggest gera sugestão."""
        validator = FrontmatterValidator(basic_config)

        result = validator.validate({"title": "My Note"})

        assert result["valid"] is True
        assert len(result["suggestions"]) == 1
        assert result["suggestions"][0]["field"] == "status"

    def test_auto_generates_uuid(self, auto_generate_config):
        """on_missing=auto gera UUID para campo ausente."""
        validator = FrontmatterValidator(auto_generate_config)

        result = validator.validate({})

        assert result["valid"] is True
        assert "id" in result["auto_generated"]
        assert "id" in result["validated_data"]
        # UUID v7 tem formato específico
        uuid = result["auto_generated"]["id"]
        assert uuid[14] == "7"  # Versão 7

    def test_auto_generates_datetime(self, auto_generate_config):
        """on_missing=auto gera datetime para campo ausente."""
        validator = FrontmatterValidator(auto_generate_config)

        result = validator.validate({})

        assert "created_at" in result["auto_generated"]
        # Deve ser ISO datetime
        assert "T" in result["auto_generated"]["created_at"]

    def test_alias_resolved(self, auto_generate_config):
        """Alias é resolvido para nome canônico."""
        validator = FrontmatterValidator(auto_generate_config)

        # Usando alias "created" ao invés de "created_at"
        result = validator.validate({"created": "2024-01-15T10:00:00"})

        assert result["valid"] is True
        assert "created_at" in result["validated_data"]
        assert result["validated_data"]["created_at"] == "2024-01-15T10:00:00"
        # Deve ter warning sobre alias
        alias_warnings = [w for w in result["warnings"] if w["code"] == "alias_resolved"]
        assert len(alias_warnings) == 1

    def test_coercion_warning(self, basic_config):
        """Coerção gera warning."""
        validator = FrontmatterValidator(basic_config)

        # status com case diferente
        result = validator.validate({"title": "Test", "status": "DRAFT"})

        assert result["valid"] is True
        assert result["validated_data"]["status"] == "draft"
        coercion_warnings = [w for w in result["warnings"] if w["code"] == "coercion_warning"]
        assert len(coercion_warnings) == 1

    def test_extra_fields_allowed(self, basic_config):
        """Campos extras são permitidos por padrão."""
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
        """Campos extras geram erro quando não permitidos."""
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
        """Modo warn_only converte erros em warnings."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            mode="warn_only",
            schema={
                "title": FieldSchema(type="string", on_missing="require"),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({})

        assert result["valid"] is True  # Sempre válido em warn_only
        assert len(result["errors"]) == 0
        assert len(result["warnings"]) >= 1  # Erro virou warning

    def test_merge_auto_generated(self, auto_generate_config):
        """merge_auto_generated combina corretamente."""
        validator = FrontmatterValidator(auto_generate_config)

        original = {"title": "Test"}
        validation_result = validator.validate(original)

        merged = validator.merge_auto_generated(original, validation_result)

        # Auto-gerados devem estar presentes
        assert "id" in merged
        assert "created_at" in merged
        # Original também
        assert "title" in merged

    def test_null_frontmatter(self, basic_config):
        """Frontmatter None deve ser tratado como dict vazio."""
        validator = FrontmatterValidator(basic_config)

        result = validator.validate(None)

        # Deve gerar erro de required missing para title
        assert result["valid"] is False
        assert any(e["field"] == "title" for e in result["errors"])


class TestValidationType:
    """Testes para validação de tipos específicos."""

    def test_int_validation(self):
        """Validação de int com constraints."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "priority": FieldSchema(type="int", minimum=1, maximum=5),
            },
        )
        validator = FrontmatterValidator(config)

        # Valor válido
        result = validator.validate({"priority": 3})
        assert result["valid"] is True
        assert result["validated_data"]["priority"] == 3

        # Valor inválido
        result = validator.validate({"priority": 10})
        assert result["valid"] is False

    def test_url_validation(self):
        """Validação de URL."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            schema={
                "source": FieldSchema(type="url"),
            },
        )
        validator = FrontmatterValidator(config)

        # URL válida
        result = validator.validate({"source": "https://example.com"})
        assert result["valid"] is True

        # URL inválida
        result = validator.validate({"source": "not-a-url"})
        assert result["valid"] is False


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Testes para edge cases e cenários limítrofes."""

    def test_coerce_enum_from_int(self):
        """Enum deve aceitar int convertendo para string."""
        schema = FieldSchema(type="enum", values=["1", "2", "3"])
        result, warning = coerce_enum(1, schema)
        assert result == "1"

    def test_coerce_list_min_items(self):
        """Lista com menos itens que mínimo deve falhar."""
        schema = FieldSchema(type="list", item_type="string", min_items=3)
        with pytest.raises(ValueError, match="mínimo"):
            coerce_list(["a", "b"], schema)

    def test_coerce_list_empty_allowed(self):
        """Lista vazia é permitida se min_items não definido."""
        schema = FieldSchema(type="list", item_type="string")
        result, _ = coerce_list([], schema)
        assert result == []

    def test_coerce_int_from_float_string(self):
        """String com float deve ser truncada para int."""
        schema = FieldSchema(type="int")
        result, warning = coerce_int("3.7", schema)
        assert result == 3
        assert warning is not None

    def test_coerce_date_from_datetime_string(self):
        """String datetime completa deve ser truncada para date."""
        schema = FieldSchema(type="date")
        result, warning = coerce_date("2024-01-15T10:30:00", schema)
        assert result == "2024-01-15"

    def test_coerce_datetime_from_date_string(self):
        """String só com date deve ser expandida para datetime."""
        schema = FieldSchema(type="datetime")
        result, _ = coerce_datetime("2024-01-15", schema)
        # Python 3.11+ datetime.fromisoformat aceita "YYYY-MM-DD"
        assert result == "2024-01-15T00:00:00"

    def test_coerce_url_without_domain(self):
        """URL sem domínio deve falhar."""
        schema = FieldSchema(type="url")
        with pytest.raises(ValueError, match="domínio"):
            coerce_url("http://", schema)

    def test_coerce_float_from_int_no_warning(self):
        """Int para float não deve gerar warning (conversão sem perda)."""
        schema = FieldSchema(type="float")
        result, warning = coerce_float(42, schema)
        assert result == 42.0
        assert warning is None

    def test_alias_and_canonical_both_present(self):
        """Se alias e canônico ambos presentes, usa canônico."""
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

        # Ambos presentes - canônico tem prioridade
        result = validator.validate(
            {
                "created_at": "2024-01-15T10:00:00",
                "created": "2024-01-01T00:00:00",  # Ignorado
            }
        )

        assert result["valid"] is True
        assert result["validated_data"]["created_at"] == "2024-01-15T10:00:00"

    def test_suggest_with_default_value(self):
        """Campo suggest com default deve usar default em validated_data."""
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
        # Default é aplicado em validated_data
        assert result["validated_data"].get("status") == "draft"

    def test_empty_string_coercion(self):
        """String vazia deve passar validação se min_length não definido."""
        schema = FieldSchema(type="string")
        result, _ = coerce_string("", schema)
        assert result == ""

    def test_whitespace_string_trimmed(self):
        """Strings com whitespace devem ser mantidas (só strip em int/float)."""
        schema = FieldSchema(type="string")
        result, _ = coerce_string("  hello  ", schema)
        assert result == "  hello  "  # Não faz trim

    def test_list_from_set_loses_order(self):
        """Set para lista perde ordem (comportamento esperado)."""
        schema = FieldSchema(type="list", item_type="int")
        result, _ = coerce_list({3, 1, 2}, schema)
        # Ordem não garantida, mas todos presentes
        assert sorted(result) == [1, 2, 3]

    def test_negative_int_validation(self):
        """Int negativo deve passar se minimum permite."""
        schema = FieldSchema(type="int", minimum=-10)
        result, _ = coerce_int(-5, schema)
        assert result == -5

    def test_float_nan_rejected(self):
        """Float NaN deve ser rejeitado para evitar problemas em JSON/busca."""
        import math

        schema = FieldSchema(type="float", minimum=0, maximum=100)
        # NaN é rejeitado antes de validação de range
        with pytest.raises(ValueError, match="NaN não é um valor float válido"):
            coerce_float(math.nan, schema)

    def test_float_infinity_rejected(self):
        """Float Infinity deve ser rejeitado."""
        import math

        schema = FieldSchema(type="float")
        with pytest.raises(ValueError, match="Infinity não é um valor float válido"):
            coerce_float(math.inf, schema)
        with pytest.raises(ValueError, match="Infinity não é um valor float válido"):
            coerce_float(-math.inf, schema)

    def test_pattern_with_multiline(self):
        """Pattern com ^ e $ deve funcionar corretamente."""
        schema = FieldSchema(type="string", pattern=r"^[A-Z][a-z]+$")
        result, _ = coerce_string("Hello", schema)
        assert result == "Hello"

        with pytest.raises(ValueError):
            coerce_string("Hello\nWorld", schema)

    def test_multiple_aliases_same_field(self):
        """Múltiplos aliases devem resolver para mesmo campo."""
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

        # Qualquer alias funciona
        for alias in ["created", "date", "timestamp"]:
            result = validator.validate({alias: "2024-01-15T10:00:00"})
            assert result["valid"] is True
            assert "created_at" in result["validated_data"]

    def test_strict_mode_blocks_on_error(self):
        """Modo strict deve bloquear se houver erros."""
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
        """Modo lenient deve ter mesmo comportamento que strict para erros."""
        config = FrontmatterSchemaConfig(
            enabled=True,
            mode="lenient",
            schema={
                "priority": FieldSchema(type="int", minimum=1, maximum=5),
            },
        )
        validator = FrontmatterValidator(config)

        result = validator.validate({"priority": 100})
        assert result["valid"] is False  # Lenient também bloqueia erros

    def test_int_from_very_large_float_rejected(self):
        """Float que vira infinito deve ser rejeitado."""
        schema = FieldSchema(type="int")
        # float("1e309") = inf, que é rejeitado
        with pytest.raises(ValueError, match="Infinity não pode ser convertido"):
            coerce_int(float("1e309"), schema)

    def test_int_from_large_number_string_accepted(self):
        """Python 3 aceita inteiros arbitrariamente grandes de strings."""
        schema = FieldSchema(type="int")
        # Python 3 lida com inteiros grandes
        result, _ = coerce_int("99999999999999999999999", schema)
        assert result == 99999999999999999999999

    def test_int_from_nan_string_rejected(self):
        """String 'nan' para int deve ser rejeitada."""
        schema = FieldSchema(type="int")
        with pytest.raises(ValueError, match="NaN não pode ser convertido"):
            coerce_int("nan", schema)

    def test_int_from_inf_string_rejected(self):
        """String 'inf' para int deve ser rejeitada."""
        schema = FieldSchema(type="int")
        with pytest.raises(ValueError, match="Infinity não pode ser convertido"):
            coerce_int("inf", schema)

    def test_alias_conflict_warning(self):
        """Quando campo canônico e alias ambos presentes, gera warning de conflito."""
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

        # Ambos presentes: canônico ganha, alias gera warning
        result = validator.validate(
            {
                "created_at": "2024-01-15T10:00:00",
                "created": "2024-01-20T12:00:00",
            }
        )
        assert result["valid"] is True
        assert result["validated_data"]["created_at"] == "2024-01-15T10:00:00"

        # Deve ter warning de conflito
        conflict_warnings = [w for w in result["warnings"] if w["code"] == "alias_conflict"]
        assert len(conflict_warnings) == 1
        assert "created" in conflict_warnings[0]["message"]

    def test_date_suffix_truncation_warning(self):
        """Date com sufixo extra deve gerar warning sobre truncação."""
        schema = FieldSchema(type="date")
        result, warning = coerce_date("2024-01-15T10:30:00Z", schema)
        assert result == "2024-01-15"
        assert warning is not None
        assert "truncado" in warning.lower() or "conteúdo extra" in warning.lower()

    def test_enum_from_non_string_preserves_type(self):
        """Conversão de não-string para enum deve mencionar tipo original."""
        schema = FieldSchema(type="enum", values=["1", "2", "3"])
        result, warning = coerce_enum(2, schema)
        assert result == "2"
        assert warning is not None
        assert "int" in warning.lower()
