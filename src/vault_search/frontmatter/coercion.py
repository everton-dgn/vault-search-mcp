"""
Funções de coerção de tipos para frontmatter.

Cada função retorna tupla (valor_coercido, warning_message | None).
Se coerção falhar, levanta ValueError.
"""

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from vault_search.frontmatter.schema import FieldSchema


def coerce_string(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce valor para string.

    Qualquer valor pode ser convertido para string.
    """
    if isinstance(value, str):
        result = value
        warning = None
    else:
        result = str(value)
        warning = f"Convertido de {type(value).__name__} para string"

    # Validações
    if schema.min_length is not None and len(result) < schema.min_length:
        raise ValueError(f"String muito curta: {len(result)} < {schema.min_length}")

    if schema.max_length is not None and len(result) > schema.max_length:
        raise ValueError(f"String muito longa: {len(result)} > {schema.max_length}")

    if schema.pattern is not None:
        if not re.match(schema.pattern, result):
            raise ValueError(f"String não corresponde ao pattern: {schema.pattern}")

    return result, warning


def coerce_int(value: Any, schema: FieldSchema) -> tuple[int, str | None]:
    """
    Coerce valor para int.

    Aceita: int, float (trunca), string numérica, bool.
    Rejeita: NaN, Infinity, valores fora do range de int.
    """
    import math

    warning = None

    if isinstance(value, bool):
        # bool é subclasse de int, mas queremos tratar separadamente
        result = 1 if value else 0
        warning = "Convertido de bool para int"
    elif isinstance(value, int):
        result = value
    elif isinstance(value, float):
        # Rejeitar NaN e Infinity
        if math.isnan(value):
            raise ValueError("NaN não pode ser convertido para int")
        if math.isinf(value):
            raise ValueError("Infinity não pode ser convertido para int")
        try:
            result = int(value)
        except (OverflowError, ValueError) as e:
            raise ValueError(f"Float {value} fora do range de int: {e}") from e
        if value != result:
            warning = f"Float truncado de {value} para {result}"
    elif isinstance(value, str):
        value = value.strip()
        try:
            # Tenta int direto
            result = int(value)
        except ValueError:
            # Tenta float e trunca
            try:
                float_val = float(value)
                # Rejeitar NaN e Infinity de strings
                if math.isnan(float_val):
                    raise ValueError("NaN não pode ser convertido para int")
                if math.isinf(float_val):
                    raise ValueError("Infinity não pode ser convertido para int")
                result = int(float_val)
                warning = f"String '{value}' convertida para int {result}"
            except (ValueError, OverflowError) as e:
                raise ValueError(f"Não é possível converter '{value}' para int: {e}") from e
        except OverflowError as e:
            raise ValueError(f"Valor '{value}' fora do range de int: {e}") from e
    else:
        raise ValueError(f"Tipo {type(value).__name__} não pode ser convertido para int")

    # Validações numéricas
    if schema.minimum is not None and result < schema.minimum:
        raise ValueError(f"Valor {result} menor que mínimo {schema.minimum}")

    if schema.maximum is not None and result > schema.maximum:
        raise ValueError(f"Valor {result} maior que máximo {schema.maximum}")

    return result, warning


def coerce_float(value: Any, schema: FieldSchema) -> tuple[float, str | None]:
    """
    Coerce valor para float.

    Aceita: float, int, string numérica, bool.
    Rejeita: NaN, Infinity (por padrão, para evitar problemas em JSON/busca).
    """
    import math

    warning = None

    if isinstance(value, bool):
        result = 1.0 if value else 0.0
        warning = "Convertido de bool para float"
    elif isinstance(value, float):
        # Rejeitar NaN e Infinity
        if math.isnan(value):
            raise ValueError("NaN não é um valor float válido para frontmatter")
        if math.isinf(value):
            raise ValueError("Infinity não é um valor float válido para frontmatter")
        result = value
    elif isinstance(value, int):
        result = float(value)
    elif isinstance(value, str):
        value = value.strip()
        try:
            result = float(value)
            # Rejeitar NaN e Infinity de strings
            if math.isnan(result):
                raise ValueError("NaN não é um valor float válido para frontmatter")
            if math.isinf(result):
                raise ValueError("Infinity não é um valor float válido para frontmatter")
            warning = f"String '{value}' convertida para float"
        except ValueError as e:
            raise ValueError(f"Não é possível converter '{value}' para float: {e}") from e
    else:
        raise ValueError(f"Tipo {type(value).__name__} não pode ser convertido para float")

    # Validações numéricas
    if schema.minimum is not None and result < schema.minimum:
        raise ValueError(f"Valor {result} menor que mínimo {schema.minimum}")

    if schema.maximum is not None and result > schema.maximum:
        raise ValueError(f"Valor {result} maior que máximo {schema.maximum}")

    return result, warning


def coerce_bool(value: Any, schema: FieldSchema) -> tuple[bool, str | None]:
    """
    Coerce valor para bool.

    Aceita: bool, int (0/1), string ("true"/"false"/"yes"/"no"/"1"/"0").
    """
    warning = None

    if isinstance(value, bool):
        result = value
    elif isinstance(value, int):
        if value == 0:
            result = False
        elif value == 1:
            result = True
        else:
            raise ValueError(f"Int {value} não pode ser convertido para bool (use 0 ou 1)")
        warning = f"Int {value} convertido para bool"
    elif isinstance(value, str):
        value_lower = value.strip().lower()
        truthy = {"true", "yes", "1", "on", "sim", "verdadeiro"}
        falsy = {"false", "no", "0", "off", "nao", "não", "falso"}
        if value_lower in truthy:
            result = True
            warning = f"String '{value}' convertida para True"
        elif value_lower in falsy:
            result = False
            warning = f"String '{value}' convertida para False"
        else:
            raise ValueError(
                f"String '{value}' não pode ser convertida para bool. Use: true/false, yes/no, 1/0"
            )
    else:
        raise ValueError(f"Tipo {type(value).__name__} não pode ser convertido para bool")

    return result, warning


def coerce_date(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce valor para date (retorna string ISO).

    Aceita: date, datetime, string ISO (YYYY-MM-DD).
    """
    warning = None

    if isinstance(value, date) and not isinstance(value, datetime):
        result = value.isoformat()
    elif isinstance(value, datetime):
        result = value.date().isoformat()
        warning = "Datetime truncado para date"
    elif isinstance(value, str):
        value = value.strip()
        # Tenta parsear ISO
        try:
            parsed = date.fromisoformat(value[:10])  # Pega só YYYY-MM-DD
            result = parsed.isoformat()
            # Gera warning se tinha conteúdo extra (ex: datetime ou sufixo)
            if len(value) > 10:
                warning = f"Date truncado de '{value}' para '{result}' (conteúdo extra ignorado)"
            elif value != result:
                warning = f"Date normalizado de '{value}' para '{result}'"
        except ValueError:
            raise ValueError(
                f"String '{value}' não é uma data válida. Use formato YYYY-MM-DD"
            ) from None
    else:
        raise ValueError(f"Tipo {type(value).__name__} não pode ser convertido para date")

    return result, warning


def coerce_datetime(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce valor para datetime (retorna string ISO).

    Aceita: datetime, date (adiciona 00:00:00), string ISO.
    """
    warning = None

    if isinstance(value, datetime):
        result = value.isoformat()
    elif isinstance(value, date):
        # date sem hora -> adiciona 00:00:00
        result = datetime.combine(value, datetime.min.time()).isoformat()
        warning = "Date expandido para datetime com hora 00:00:00"
    elif isinstance(value, str):
        value = value.strip()
        # Tenta parsear ISO datetime
        try:
            # datetime.fromisoformat é flexível
            parsed = datetime.fromisoformat(value)
            result = parsed.isoformat()
        except ValueError:
            # Tenta só date
            try:
                parsed_date = date.fromisoformat(value[:10])
                parsed = datetime.combine(parsed_date, datetime.min.time())
                result = parsed.isoformat()
                warning = f"String '{value}' interpretada como date, expandida para datetime"
            except ValueError:
                raise ValueError(
                    f"String '{value}' não é um datetime válido. "
                    f"Use formato ISO (YYYY-MM-DDTHH:MM:SS)"
                ) from None
    else:
        raise ValueError(f"Tipo {type(value).__name__} não pode ser convertido para datetime")

    return result, warning


def coerce_uuid(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce valor para UUID (retorna string).

    Aceita: string UUID válida.
    """
    import uuid as uuid_module

    warning = None

    if isinstance(value, str):
        value = value.strip()
        try:
            # Valida formato UUID
            parsed = uuid_module.UUID(value)
            result = str(parsed)
            if value.lower() != result:
                warning = f"UUID normalizado de '{value}' para '{result}'"
        except ValueError:
            raise ValueError(f"String '{value}' não é um UUID válido") from None
    else:
        raise ValueError(f"Tipo {type(value).__name__} não pode ser convertido para UUID")

    return result, warning


def coerce_url(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce valor para URL (retorna string validada).

    Aceita: string URL válida (http, https).
    """
    warning = None

    if isinstance(value, str):
        value = value.strip()
        parsed = urlparse(value)

        if not parsed.scheme:
            raise ValueError(f"URL '{value}' não tem scheme (http/https)")

        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"URL '{value}' tem scheme inválido '{parsed.scheme}'. Use http ou https"
            )

        if not parsed.netloc:
            raise ValueError(f"URL '{value}' não tem domínio")

        result = value
    else:
        raise ValueError(f"Tipo {type(value).__name__} não pode ser convertido para URL")

    return result, warning


def coerce_enum(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce valor para enum (retorna valor canônico da lista).

    Aceita: string que está na lista de valores permitidos.
    """
    if not schema.values:
        raise ValueError("Schema de enum não tem 'values' definido")

    warning = None

    if not isinstance(value, str):
        original_type = type(value).__name__
        value = str(value)
        warning = f"Convertido de {original_type} para string para comparação enum"

    value = value.strip()

    # Comparação case-insensitive ou não
    if schema.case_insensitive:
        value_lower = value.lower()
        for allowed in schema.values:
            if allowed.lower() == value_lower:
                if allowed != value:
                    warning = f"Enum normalizado de '{value}' para '{allowed}'"
                return allowed, warning
        raise ValueError(
            f"Valor '{value}' não está na lista de valores permitidos: {schema.values}"
        )
    else:
        if value in schema.values:
            return value, warning
        raise ValueError(
            f"Valor '{value}' não está na lista de valores permitidos: {schema.values}"
        )


def coerce_list(
    value: Any,
    schema: FieldSchema,
) -> tuple[list[str | int | float], str | None]:
    """
    Coerce valor para lista.

    Aceita: list, tuple, set, string separada por vírgula.
    """
    warnings: list[str] = []

    # Converte para lista
    if isinstance(value, list):
        items = value
    elif isinstance(value, (tuple, set)):
        items = list(value)
        warnings.append(f"Convertido de {type(value).__name__} para list")
    elif isinstance(value, str):
        # String separada por vírgula
        items = [item.strip() for item in value.split(",") if item.strip()]
        warnings.append(f"String convertida para lista com {len(items)} itens")
    else:
        raise ValueError(f"Tipo {type(value).__name__} não pode ser convertido para lista")

    # Coerce itens para o tipo esperado
    item_type = schema.item_type or "string"
    result: list[str | int | float] = []

    for i, item in enumerate(items):
        try:
            if item_type == "string":
                result.append(str(item))
            elif item_type == "int":
                if isinstance(item, bool):
                    result.append(1 if item else 0)
                elif isinstance(item, (int, float)):
                    result.append(int(item))
                elif isinstance(item, str):
                    result.append(int(item.strip()))
                else:
                    raise ValueError("Não é possível converter para int")
            elif item_type == "float":
                if isinstance(item, bool):
                    result.append(1.0 if item else 0.0)
                elif isinstance(item, (int, float)):
                    result.append(float(item))
                elif isinstance(item, str):
                    result.append(float(item.strip()))
                else:
                    raise ValueError("Não é possível converter para float")
        except (ValueError, TypeError) as e:
            raise ValueError(
                f"Item {i} da lista não pode ser convertido para {item_type}: {e}"
            ) from e

    # Validações de tamanho
    if schema.min_items is not None and len(result) < schema.min_items:
        raise ValueError(f"Lista com {len(result)} itens, mínimo é {schema.min_items}")

    if schema.max_items is not None and len(result) > schema.max_items:
        raise ValueError(f"Lista com {len(result)} itens, máximo é {schema.max_items}")

    warning = "; ".join(warnings) if warnings else None
    return result, warning


# Mapeamento de tipo para função de coerção
COERCION_FUNCTIONS = {
    "string": coerce_string,
    "int": coerce_int,
    "float": coerce_float,
    "bool": coerce_bool,
    "date": coerce_date,
    "datetime": coerce_datetime,
    "uuid": coerce_uuid,
    "url": coerce_url,
    "enum": coerce_enum,
    "list": coerce_list,
}


def coerce_value(
    value: Any,
    schema: FieldSchema,
) -> tuple[Any, str | None]:
    """
    Coerce valor para o tipo definido no schema.

    Parâmetros:
        value: valor a coercir
        schema: schema do campo

    Retorna:
        Tupla (valor_coercido, warning_message | None)

    Raises:
        ValueError: se coerção falhar
    """
    coerce_fn = COERCION_FUNCTIONS.get(schema.type)
    if not coerce_fn:
        raise ValueError(f"Tipo '{schema.type}' não suportado")

    return coerce_fn(value, schema)
