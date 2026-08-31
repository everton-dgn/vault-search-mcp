"""Redação de dados privados para logs e diagnósticos."""

from __future__ import annotations

import re
from os import PathLike
from pathlib import Path
from typing import Any

_QUOTED_PATH = re.compile(r"(?P<quote>['\"])(?:file://|/|~[/\\]|[a-zA-Z]:\\)[^'\"\r\n]+(?P=quote)")
_POSIX_PATH = re.compile(r"(?<![\w.])/(?:[^:/\r\n]+/)+[^:\r\n,;)\]}]*")
_WINDOWS_PATH = re.compile(r"(?i)(?<![\w])(?:[a-z]:\\)(?:[^\\:\r\n]+\\)*[^:\r\n,;)\]}]*")
_HOME_PATH = re.compile(r"(?<![\w])~[/\\][^:\r\n,;)\]}]*")
_FILE_URI = re.compile(r"(?i)file://[^\s]+")


def redact_text(value: Any) -> str:
    """Remove caminhos absolutos e caracteres de controle de um valor textual."""
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = _QUOTED_PATH.sub(
        lambda match: f"{match.group('quote')}[REDACTED_PATH]{match.group('quote')}",
        text,
    )
    text = _FILE_URI.sub("[REDACTED_PATH]", text)
    text = _WINDOWS_PATH.sub("[REDACTED_PATH]", text)
    text = _HOME_PATH.sub("[REDACTED_PATH]", text)
    return _POSIX_PATH.sub("[REDACTED_PATH]", text)


def relative_module_path(pathname: Any) -> str:
    """Retorna apenas o nome do módulo, sem revelar o checkout local."""
    return Path(str(pathname)).name


def redact_mapping(value: Any) -> Any:
    """Redige recursivamente campos sensíveis e caminhos em estruturas de log."""
    sensitive_keys = {
        "authorization",
        "content",
        "note_body",
        "password",
        "prompt",
        "query",
        "secret",
        "token",
    }

    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(sensitive in normalized for sensitive in sensitive_keys):
                redacted[key] = "[REDACTED]"
            elif normalized == "pathname":
                redacted[key] = relative_module_path(item)
            else:
                redacted[key] = redact_mapping(item)
        return redacted
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_mapping(item) for item in value)
    if isinstance(value, PathLike):
        return "[REDACTED_PATH]"
    if isinstance(value, str):
        return redact_text(value)
    return value
