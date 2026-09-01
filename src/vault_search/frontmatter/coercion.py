"""
Type coercion functions for frontmatter values.

Each function returns a tuple of the coerced value and an optional warning.
Failed coercions raise ValueError.
"""

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from vault_search.frontmatter.schema import FieldSchema


def coerce_string(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce a value to a string.

    Any value can be converted to a string.
    """
    if isinstance(value, str):
        result = value
        warning = None
    else:
        result = str(value)
        warning = f"Converted from {type(value).__name__} to string"

    # Constraints.
    if schema.min_length is not None and len(result) < schema.min_length:
        raise ValueError(f"String is too short: {len(result)} < {schema.min_length}")

    if schema.max_length is not None and len(result) > schema.max_length:
        raise ValueError(f"String is too long: {len(result)} > {schema.max_length}")

    if schema.pattern is not None:
        if not re.match(schema.pattern, result):
            raise ValueError(f"String does not match pattern: {schema.pattern}")

    return result, warning


def coerce_int(value: Any, schema: FieldSchema) -> tuple[int, str | None]:
    """
    Coerce a value to an integer.

    Accepts integers, truncated floats, numeric strings, and booleans.
    Rejects NaN, infinity, and values outside the integer range.
    """
    import math

    warning = None

    if isinstance(value, bool):
        # bool is an int subclass, but it has an explicit conversion policy.
        result = 1 if value else 0
        warning = "Converted from bool to int"
    elif isinstance(value, int):
        result = value
    elif isinstance(value, float):
        # Reject NaN and infinity.
        if math.isnan(value):
            raise ValueError("NaN cannot be converted to int")
        if math.isinf(value):
            raise ValueError("Infinity cannot be converted to int")
        try:
            result = int(value)
        except (OverflowError, ValueError) as e:
            raise ValueError(f"Float {value} is outside the integer range: {e}") from e
        if value != result:
            warning = f"Float truncated from {value} to {result}"
    elif isinstance(value, str):
        value = value.strip()
        try:
            # Try an integer conversion first.
            result = int(value)
        except ValueError:
            # Fall back to a float and truncate it.
            try:
                float_val = float(value)
                # Reject string representations of NaN and infinity.
                if math.isnan(float_val):
                    raise ValueError("NaN cannot be converted to int")
                if math.isinf(float_val):
                    raise ValueError("Infinity cannot be converted to int")
                result = int(float_val)
                warning = f"String '{value}' converted to int {result}"
            except (ValueError, OverflowError) as e:
                raise ValueError(f"Cannot convert '{value}' to int: {e}") from e
        except OverflowError as e:
            raise ValueError(f"Value '{value}' is outside the integer range: {e}") from e
    else:
        raise ValueError(f"Type {type(value).__name__} cannot be converted to int")

    # Numeric constraints.
    if schema.minimum is not None and result < schema.minimum:
        raise ValueError(f"Value {result} is below minimum {schema.minimum}")

    if schema.maximum is not None and result > schema.maximum:
        raise ValueError(f"Value {result} is above maximum {schema.maximum}")

    return result, warning


def coerce_float(value: Any, schema: FieldSchema) -> tuple[float, str | None]:
    """
    Coerce a value to a float.

    Accepts floats, integers, numeric strings, and booleans.
    Rejects NaN and infinity to keep JSON and search behavior deterministic.
    """
    import math

    warning = None

    if isinstance(value, bool):
        result = 1.0 if value else 0.0
        warning = "Converted from bool to float"
    elif isinstance(value, float):
        # Reject NaN and infinity.
        if math.isnan(value):
            raise ValueError("NaN is not a valid frontmatter float")
        if math.isinf(value):
            raise ValueError("Infinity is not a valid frontmatter float")
        result = value
    elif isinstance(value, int):
        result = float(value)
    elif isinstance(value, str):
        value = value.strip()
        try:
            result = float(value)
            # Reject string representations of NaN and infinity.
            if math.isnan(result):
                raise ValueError("NaN is not a valid frontmatter float")
            if math.isinf(result):
                raise ValueError("Infinity is not a valid frontmatter float")
            warning = f"String '{value}' converted to float"
        except ValueError as e:
            raise ValueError(f"Cannot convert '{value}' to float: {e}") from e
    else:
        raise ValueError(f"Type {type(value).__name__} cannot be converted to float")

    # Numeric constraints.
    if schema.minimum is not None and result < schema.minimum:
        raise ValueError(f"Value {result} is below minimum {schema.minimum}")

    if schema.maximum is not None and result > schema.maximum:
        raise ValueError(f"Value {result} is above maximum {schema.maximum}")

    return result, warning


def coerce_bool(value: Any, schema: FieldSchema) -> tuple[bool, str | None]:
    """
    Coerce a value to a boolean.

    Accepts booleans, the integers 0 and 1, and common English and Portuguese
    boolean strings.
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
            raise ValueError(f"Int {value} cannot be converted to bool (use 0 or 1)")
        warning = f"Int {value} converted to bool"
    elif isinstance(value, str):
        value_lower = value.strip().lower()
        truthy = {"true", "yes", "1", "on", "sim", "verdadeiro"}
        falsy = {"false", "no", "0", "off", "nao", "não", "falso"}
        if value_lower in truthy:
            result = True
            warning = f"String '{value}' converted to True"
        elif value_lower in falsy:
            result = False
            warning = f"String '{value}' converted to False"
        else:
            raise ValueError(
                f"String '{value}' cannot be converted to bool. "
                "Use true/false, yes/no, sim/não, or 1/0"
            )
    else:
        raise ValueError(f"Type {type(value).__name__} cannot be converted to bool")

    return result, warning


def coerce_date(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce a value to a date represented as an ISO string.

    Accepts dates, datetimes, and ISO date strings (YYYY-MM-DD).
    """
    warning = None

    if isinstance(value, date) and not isinstance(value, datetime):
        result = value.isoformat()
    elif isinstance(value, datetime):
        result = value.date().isoformat()
        warning = "Datetime truncated to date"
    elif isinstance(value, str):
        value = value.strip()
        # Parse the ISO date prefix.
        try:
            parsed = date.fromisoformat(value[:10])  # Read only YYYY-MM-DD.
            result = parsed.isoformat()
            # Warn when a datetime or suffix was discarded.
            if len(value) > 10:
                warning = f"Date truncated from '{value}' to '{result}' (extra content ignored)"
            elif value != result:
                warning = f"Date normalized from '{value}' to '{result}'"
        except ValueError:
            raise ValueError(f"String '{value}' is not a valid date. Use YYYY-MM-DD") from None
    else:
        raise ValueError(f"Type {type(value).__name__} cannot be converted to date")

    return result, warning


def coerce_datetime(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce a value to a datetime represented as an ISO string.

    Accepts datetimes, dates (expanded with 00:00:00), and ISO strings.
    """
    warning = None

    if isinstance(value, datetime):
        result = value.isoformat()
    elif isinstance(value, date):
        # Expand a date with midnight.
        result = datetime.combine(value, datetime.min.time()).isoformat()
        warning = "Date expanded to datetime at 00:00:00"
    elif isinstance(value, str):
        value = value.strip()
        # Parse an ISO datetime first.
        try:
            # datetime.fromisoformat accepts standard ISO variants.
            parsed = datetime.fromisoformat(value)
            result = parsed.isoformat()
        except ValueError:
            # Fall back to a date.
            try:
                parsed_date = date.fromisoformat(value[:10])
                parsed = datetime.combine(parsed_date, datetime.min.time())
                result = parsed.isoformat()
                warning = f"String '{value}' interpreted as a date and expanded to datetime"
            except ValueError:
                raise ValueError(
                    f"String '{value}' is not a valid datetime. "
                    f"Use ISO format (YYYY-MM-DDTHH:MM:SS)"
                ) from None
    else:
        raise ValueError(f"Type {type(value).__name__} cannot be converted to datetime")

    return result, warning


def coerce_uuid(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce a value to a UUID represented as a string.

    Accepts valid UUID strings.
    """
    import uuid as uuid_module

    warning = None

    if isinstance(value, str):
        value = value.strip()
        try:
            # Validate and canonicalize the UUID.
            parsed = uuid_module.UUID(value)
            result = str(parsed)
            if value.lower() != result:
                warning = f"UUID normalized from '{value}' to '{result}'"
        except ValueError:
            raise ValueError(f"String '{value}' is not a valid UUID") from None
    else:
        raise ValueError(f"Type {type(value).__name__} cannot be converted to UUID")

    return result, warning


def coerce_url(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce a value to a validated URL string.

    Accepts valid HTTP and HTTPS URL strings.
    """
    warning = None

    if isinstance(value, str):
        value = value.strip()
        parsed = urlparse(value)

        if not parsed.scheme:
            raise ValueError(f"URL '{value}' has no scheme (http/https)")

        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"URL '{value}' has invalid scheme '{parsed.scheme}'. Use http or https"
            )

        if not parsed.netloc:
            raise ValueError(f"URL '{value}' has no domain")

        result = value
    else:
        raise ValueError(f"Type {type(value).__name__} cannot be converted to URL")

    return result, warning


def coerce_enum(value: Any, schema: FieldSchema) -> tuple[str, str | None]:
    """
    Coerce a value to an enum's canonical spelling.

    Accepts a string present in the allowed value list.
    """
    if not schema.values:
        raise ValueError("Enum schema has no 'values' definition")

    warning = None

    if not isinstance(value, str):
        original_type = type(value).__name__
        value = str(value)
        warning = f"Converted from {original_type} to string for enum comparison"

    value = value.strip()

    # Apply the configured case-sensitivity policy.
    if schema.case_insensitive:
        value_lower = value.lower()
        for allowed in schema.values:
            if allowed.lower() == value_lower:
                if allowed != value:
                    warning = f"Enum normalized from '{value}' to '{allowed}'"
                return allowed, warning
        raise ValueError(f"Value '{value}' is not in the allowed value list: {schema.values}")
    else:
        if value in schema.values:
            return value, warning
        raise ValueError(f"Value '{value}' is not in the allowed value list: {schema.values}")


def coerce_list(
    value: Any,
    schema: FieldSchema,
) -> tuple[list[str | int | float], str | None]:
    """
    Coerce a value to a list.

    Accepts lists, tuples, sets, and comma-separated strings.
    """
    warnings: list[str] = []

    # Convert the container to a list.
    if isinstance(value, list):
        items = value
    elif isinstance(value, (tuple, set)):
        items = list(value)
        warnings.append(f"Converted from {type(value).__name__} to list")
    elif isinstance(value, str):
        # Split a comma-separated string.
        items = [item.strip() for item in value.split(",") if item.strip()]
        warnings.append(f"String converted to a list with {len(items)} items")
    else:
        raise ValueError(f"Type {type(value).__name__} cannot be converted to a list")

    # Coerce every item to the configured type.
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
                    raise ValueError("Cannot convert to int")
            elif item_type == "float":
                if isinstance(item, bool):
                    result.append(1.0 if item else 0.0)
                elif isinstance(item, (int, float)):
                    result.append(float(item))
                elif isinstance(item, str):
                    result.append(float(item.strip()))
                else:
                    raise ValueError("Cannot convert to float")
        except (ValueError, TypeError) as e:
            raise ValueError(f"List item {i} cannot be converted to {item_type}: {e}") from e

    # Length constraints.
    if schema.min_items is not None and len(result) < schema.min_items:
        raise ValueError(f"List has {len(result)} items; minimum is {schema.min_items}")

    if schema.max_items is not None and len(result) > schema.max_items:
        raise ValueError(f"List has {len(result)} items; maximum is {schema.max_items}")

    warning = "; ".join(warnings) if warnings else None
    return result, warning


# Map field types to coercion functions.
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
    Coerce a value to the type defined by the schema.

    Parameters:
        value: value to coerce
        schema: field schema

    Returns:
        Tuple of coerced value and optional warning

    Raises:
        ValueError: if coercion fails
    """
    coerce_fn = COERCION_FUNCTIONS.get(schema.type)
    if not coerce_fn:
        raise ValueError(f"Unsupported type '{schema.type}'")

    return coerce_fn(value, schema)
