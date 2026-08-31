"""
Security utilities for queries and path validation.
"""

from pathlib import Path


def escape_sql_string(value: str) -> str:
    """
    Escape a string for safe use in SQL and LanceDB queries.

    Prevent SQL injection by escaping single quotes.

    Parameters:
        value: String to escape.

    Returns:
        The string with duplicated single quotes.

    Example:
        "O'Brien" -> "O''Brien"
    """
    if not value:
        return value
    # Escape single quotes by duplicating them, following SQL conventions.
    return value.replace("'", "''")


def escape_like_pattern(value: str) -> str:
    """
    Escape special characters in LIKE patterns.

    Prevent unintended wildcard matches.

    Parameters:
        value: Pattern to escape.

    Returns:
        The string with ``%``, ``_``, and ``\\`` escaped.
    """
    if not value:
        return value
    # Escape LIKE special characters.
    value = value.replace("\\", "\\\\")
    value = value.replace("%", "\\%")
    value = value.replace("_", "\\_")
    # Escape single quotes as well.
    value = value.replace("'", "''")
    return value


def validate_relative_path(relative_path: str) -> bool:
    """
    Validate that a relative path does not contain traversal.

    Prevent path traversal attacks such as ``../../etc/passwd``.

    Parameters:
        relative_path: Relative path to validate.

    Returns:
        ``True`` when the path is safe.
    """
    if not relative_path:
        return False

    # Reject absolute paths.
    if relative_path.startswith("/") or relative_path.startswith("\\"):
        return False

    # Normalize and inspect components.
    path = Path(relative_path)

    # Reject ``..`` in any component.
    for part in path.parts:
        if part == "..":
            return False
        # Reject null characters.
        if "\x00" in part:
            return False

    # Verify that the normalized path remains inside the base directory.
    try:
        # Resolve against a synthetic base directory.
        base = Path("/safe/base")
        resolved = (base / relative_path).resolve()
        # The resolved path must remain inside the base.
        return str(resolved).startswith(str(base))
    except ValueError, OSError:
        return False
