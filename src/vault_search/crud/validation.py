"""
Validation functions for CRUD operations.

Combines path, size, extension, and frontmatter validation.
"""

import logging
import os
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml

from vault_search.config.paths import VAULT_PATH
from vault_search.config.search import (
    IGNORED_FOLDERS,
    INDEXABLE_EXTENSIONS,
    READABLE_TEXT_EXTENSIONS,
)
from vault_search.config.security import (
    MAX_CONTENT_SIZE,
    MAX_FRONTMATTER_KEYS,
    MAX_PATH_LENGTH,
)
from vault_search.crud.locking import FileRevision, file_revision
from vault_search.crud.types import OperationResult, error_result
from vault_search.utils.security import validate_relative_path

logger = logging.getLogger(__name__)


def resolve_internal_path(*parts: str) -> Path:
    """Resolve an application-owned path without allowing an external symlink."""
    vault_root = VAULT_PATH.expanduser().resolve(strict=False)
    candidate = vault_root.joinpath(*parts).resolve(strict=False)
    try:
        candidate.relative_to(vault_root)
    except ValueError as exc:
        raise ValueError("Internal path is invalid or outside the vault.") from exc
    return candidate


def resolve_path(relative_path: str) -> Path:
    """
    Resolve a vault-relative path to an absolute path.

    Raises:
        ValueError: if the path is invalid or too long.
    """
    if not relative_path or not relative_path.strip():
        raise ValueError("Path cannot be empty.")

    relative_path = relative_path.strip()

    if len(relative_path) > MAX_PATH_LENGTH:
        raise ValueError(
            f"Path is too long ({len(relative_path)} characters). Maximum: {MAX_PATH_LENGTH}."
        )

    if not validate_relative_path(relative_path):
        raise ValueError("Path is invalid or outside the vault.")

    try:
        return resolve_internal_path(relative_path)
    except ValueError as exc:
        raise ValueError("Path is invalid or outside the vault.") from exc


def validate_content_size(content: str) -> None:
    """
    Ensure content does not exceed the configured size limit.

    Raises:
        ValueError: if the content is too large.
    """
    size = len(content.encode("utf-8"))
    if size > MAX_CONTENT_SIZE:
        size_mb = size / 1_048_576
        max_mb = MAX_CONTENT_SIZE / 1_048_576
        raise ValueError(f"Content is too large ({size_mb:.1f} MB). Maximum: {max_mb:.0f} MB.")


def validate_frontmatter_size(frontmatter: dict[str, Any]) -> None:
    """
    Bound the number of frontmatter keys as YAML-bomb defense in depth.

    Raises:
        ValueError: if frontmatter has too many keys.
    """
    if frontmatter and len(frontmatter) > MAX_FRONTMATTER_KEYS:
        raise ValueError(
            f"Frontmatter has too many keys ({len(frontmatter)}). Maximum: {MAX_FRONTMATTER_KEYS}."
        )


def get_folder(file_path: Path) -> str:
    """Extract the vault-relative folder from a path."""
    vault_root = VAULT_PATH.expanduser().resolve(strict=False)
    folder = str(file_path.parent.resolve(strict=False).relative_to(vault_root))
    return "" if folder == "." else folder


def validate_extension(relative_path: str, allow_create: bool = False) -> None:
    """
    Ensure the extension is supported for CRUD operations.

    Parameters:
        relative_path: file path
        allow_create: allow only writable .md and .canvas files
    """
    ext = Path(relative_path).suffix.lower()

    if allow_create:
        # PDFs are read-only.
        writable_extensions = {".md", ".canvas"}
        if ext not in writable_extensions:
            raise ValueError(
                f"Extension '{ext}' is not writable. Use: {', '.join(sorted(writable_extensions))}"
            )
    else:
        if ext not in INDEXABLE_EXTENSIONS:
            raise ValueError(
                f"Extension '{ext}' is not supported. "
                f"Use: {', '.join(sorted(INDEXABLE_EXTENSIONS))}"
            )


def validate_readable_text(relative_path: str) -> None:
    """
    Ensure the extension contains readable text with frontmatter support.

    PDFs are binary and Canvas files are JSON, so neither supports this path.
    """
    ext = Path(relative_path).suffix.lower()
    if ext not in READABLE_TEXT_EXTENSIONS:
        raise ValueError(
            f"Extension '{ext}' does not support plain-text reads. "
            f"Use: {', '.join(sorted(READABLE_TEXT_EXTENSIONS))}. "
            "Use search_vault to search PDFs and Canvas files."
        )


def validate_markdown_only(relative_path: str) -> None:
    """
    Require .md for operations that manipulate Markdown frontmatter.

    Canvas is JSON; adding YAML frontmatter would corrupt the format.
    """
    ext = Path(relative_path).suffix.lower()
    if ext != ".md":
        raise ValueError(
            f"Extension '{ext}' is not supported for this operation. "
            "Only .md is supported because Canvas is JSON, not Markdown."
        )


def validate_not_ignored_folder(relative_path: str) -> None:
    """
    Ensure the path is outside every ignored folder.

    This blocks operations in .trash, .obsidian, and other private folders.
    """
    path_parts = Path(relative_path).parts
    for ignored in IGNORED_FOLDERS:
        if ignored in path_parts:
            raise ValueError(f"Operation is not allowed in ignored folder: {ignored}")


def serialize_frontmatter(frontmatter: dict[str, Any]) -> str:
    """Serialize frontmatter as YAML."""
    if not frontmatter:
        return ""
    yaml_str = yaml.dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    return f"---\n{yaml_str}---\n\n"


# =============================================================================
# Safe I/O helpers
# =============================================================================


def safe_read_text(
    file_path: Path,
    relative_path: str,
) -> tuple[str | None, OperationResult | None]:
    """
    Read a file with standardized error handling.

    Returns:
        Tuple of content and error result, exactly one of which is None.
    """
    try:
        resolved_path = file_path.resolve(strict=True)
        vault_root = VAULT_PATH.expanduser().resolve(strict=False)
        resolved_path.relative_to(vault_root)
        content = file_path.read_text(encoding="utf-8")
        return content, None
    except OSError as e:
        logger.error("note_read_failed error_type=%s", type(e).__name__)
        return None, error_result(relative_path, "Failed to read file")
    except ValueError:
        logger.error("note_read_rejected reason=outside_vault")
        return None, error_result(relative_path, "Path is invalid or outside the vault")
    except UnicodeDecodeError as e:
        logger.error("note_read_failed error_type=%s", type(e).__name__)
        return None, error_result(relative_path, "Encoding error: file is not valid UTF-8")


def safe_write_text(
    file_path: Path,
    content: str,
    relative_path: str,
    *,
    expected_revision: FileRevision | None = None,
    check_revision: bool = False,
) -> OperationResult | None:
    """
    Write a file atomically with standardized error handling.

    When ``check_revision`` is active, compare inode, nanosecond mtime, and size
    with ``expected_revision`` immediately before replacement.

    Returns:
        None on success, otherwise an OperationResult containing the error.
    """
    temp_path: Path | None = None
    try:
        vault_root = VAULT_PATH.expanduser().resolve(strict=False)
        resolved_parent = file_path.parent.resolve(strict=True)
        resolved_parent.relative_to(vault_root)

        previous_mode = None
        if file_path.exists():
            resolved_file = file_path.resolve(strict=True)
            resolved_file.relative_to(vault_root)
            previous_mode = stat.S_IMODE(resolved_file.stat().st_mode)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=resolved_parent,
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)

        if previous_mode is not None:
            temp_path.chmod(previous_mode)

        if check_revision and file_revision(file_path) != expected_revision:
            logger.warning("note_write_conflict")
            return error_result(
                relative_path,
                "Write conflict: the note changed during the operation. Try again.",
            )

        os.replace(temp_path, file_path)
        temp_path = None

        try:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(resolved_parent, directory_flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            logger.debug("directory_fsync_unavailable")
        return None
    except OSError as e:
        logger.error("note_write_failed error_type=%s", type(e).__name__)
        return error_result(relative_path, "Failed to write file")
    except ValueError:
        logger.error("note_write_rejected reason=outside_vault")
        return error_result(relative_path, "Path is invalid or outside the vault")
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                recovery_dir = resolve_internal_path(".trash", "write-failures")
                recovery_dir.mkdir(parents=True, exist_ok=True)
                recovery_path = recovery_dir / f"{uuid.uuid4().hex}-{temp_path.name}"
                temp_path.replace(recovery_path)
            except OSError, ValueError:
                logger.error("temporary_write_recovery_failed")


def validate_for_write(
    relative_path: str,
    content: str | None = None,
    frontmatter: dict[str, Any] | None = None,
    markdown_only: bool = True,
) -> Path:
    """
    Run every validation required for a write operation.

    Parameters:
        relative_path: path relative to the vault
        content: optional content to validate
        frontmatter: optional frontmatter to validate
        markdown_only: accept only .md when true

    Returns:
        Resolved absolute path.

    Raises:
        ValueError: if any validation fails.
    """
    if markdown_only:
        validate_markdown_only(relative_path)
    else:
        validate_extension(relative_path, allow_create=True)
    validate_not_ignored_folder(relative_path)
    if content is not None:
        validate_content_size(content)
    if frontmatter is not None:
        validate_frontmatter_size(frontmatter)
    return resolve_path(relative_path)


# =============================================================================
# Schema validation
# =============================================================================


def get_frontmatter_validator():
    """
    Return a configured FrontmatterValidator.

    Imports lazily to avoid dependency cycles and converts YAML dictionaries
    to FieldSchema instances at runtime.
    """
    from vault_search.config import get_config
    from vault_search.frontmatter import FrontmatterValidator
    from vault_search.frontmatter.schema import FieldSchema, FrontmatterSchemaConfig

    config = get_config()
    fm_config = config.frontmatter

    # Convert YAML dictionaries to FieldSchema instances.
    schema_dict = {
        field_name: FieldSchema(**field_dict)
        for field_name, field_dict in fm_config.schema_fields.items()
    }

    schema_config = FrontmatterSchemaConfig(
        enabled=fm_config.enabled,
        mode=fm_config.mode,
        allow_extra_fields=fm_config.allow_extra_fields,
        schema=schema_dict,
    )

    return FrontmatterValidator(schema_config)


def validate_frontmatter_schema(
    frontmatter: dict[str, Any] | None,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """
    Validate frontmatter against the configured schema.

    Parameters:
        frontmatter: frontmatter data

    Returns:
        Tuple of validated data, errors, warnings, and suggestions.

    Raises:
        ValueError: if validation fails.
    """
    result = validate_frontmatter_schema_result(frontmatter)

    if not result["valid"]:
        raise ValueError(_format_frontmatter_errors(result["errors"]))

    return (
        result["validated_data"],
        result["errors"],
        result["warnings"],
        result["suggestions"],
    )


def validate_frontmatter_schema_result(
    frontmatter: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Validate frontmatter and return the raw result without raising.

    This supports flows that need to handle specific error types, such as
    deferring required fields during create_note.
    """
    validator = get_frontmatter_validator()
    return validator.validate(frontmatter)


def _format_frontmatter_errors(errors: list[dict[str, Any]]) -> str:
    """Format validation errors as a stable client-facing message."""
    error_msgs = [f"{e['field']}: {e['message']}" for e in errors]
    return f"Frontmatter validation failed: {'; '.join(error_msgs)}"
