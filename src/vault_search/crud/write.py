"""
Write operations for vault notes.
"""

import logging
from pathlib import Path
from typing import Any

from vault_search.config import get_config
from vault_search.crud.locking import (
    advisory_path_lock,
    file_revision,
    return_write_lock_timeout,
)
from vault_search.crud.types import OperationResult, error_result, success_result
from vault_search.crud.validation import (
    get_frontmatter_validator,
    resolve_path,
    safe_read_text,
    safe_write_text,
    serialize_frontmatter,
    validate_content_size,
    validate_for_write,
    validate_frontmatter_schema,
    validate_frontmatter_schema_result,
    validate_frontmatter_size,
)
from vault_search.frontmatter import (
    FrontmatterEnrichmentConfigError,
    FrontmatterEnrichmentError,
    generate_required_fields_with_ai,
    get_required_schema_fields,
)
from vault_search.parsers.frontmatter import parse_frontmatter
from vault_search.utils.uuid import generate_uuid7
from vault_search.watching.event_handler import ignore_next_change

logger = logging.getLogger(__name__)


def _write_conflict(relative_path: str) -> OperationResult:
    """Return a safe conflict when the observed revision changed."""
    return error_result(
        relative_path,
        "Write conflict: the note changed during the operation. Try again.",
        error_code="write_conflict",
    )


def _read_locked_text(
    file_path: Path,
    relative_path: str,
) -> tuple[str | None, OperationResult | None]:
    """Read a stable revision while cooperative writers wait."""
    with advisory_path_lock(file_path):
        revision = file_revision(file_path)
        if revision is None:
            return None, error_result(
                relative_path,
                f"Note not found: {relative_path}",
            )
        content, read_error = safe_read_text(file_path, relative_path)
        if read_error:
            return None, read_error
        if file_revision(file_path) != revision:
            return None, _write_conflict(relative_path)
        return content, None


def _persist_generated_frontmatter(
    relative_path: str,
    file_path: Path,
    required_schema_fields: dict[str, Any],
    generated_fields: dict[str, Any],
    validate_schema: bool,
) -> OperationResult:
    """Reload, merge, and persist enrichment under one lock."""
    with advisory_path_lock(file_path):
        revision = file_revision(file_path)
        if revision is None:
            return error_result(relative_path, f"Note not found: {relative_path}")

        current_content, read_error = safe_read_text(file_path, relative_path)
        if read_error:
            return read_error
        assert current_content is not None
        if file_revision(file_path) != revision:
            return _write_conflict(relative_path)

        disk_fm, disk_body = parse_frontmatter(current_content)
        still_missing = [
            field_name
            for field_name in required_schema_fields
            if _is_field_missing_or_empty(disk_fm, field_name)
        ]
        if not still_missing:
            result = success_result(
                relative_path,
                "Fields were filled manually while enrichment was running",
            )
            result["frontmatter_enriched"] = False
            result["frontmatter_fields_filled"] = 0
            return result

        new_values = {
            field_name: value
            for field_name, value in generated_fields.items()
            if field_name in still_missing
        }
        if not new_values:
            return error_result(
                relative_path,
                "The enrichment provider did not return every required field",
                error_code="required_missing",
            )

        new_fm = {**disk_fm, **new_values}
        if validate_schema:
            try:
                validated_data, _, warnings, suggestions = validate_frontmatter_schema(new_fm)
                new_fm = validated_data
            except ValueError as exc:
                error_message = str(exc)
                is_required_missing = (
                    "required" in error_message.lower()
                    or "required_missing" in error_message.lower()
                )
                if not (is_required_missing and new_values):
                    return error_result(
                        relative_path,
                        error_message,
                        error_code="required_missing" if is_required_missing else None,
                    )
                warnings = [
                    {
                        "field": "_schema",
                        "message": (
                            "Strict validation still found required fields missing; "
                            "the partial enrichment was saved"
                        ),
                        "code": "required_missing_partial",
                        "value": None,
                    }
                ]
                suggestions = []
        else:
            warnings = []
            suggestions = []

        validate_frontmatter_size(new_fm)
        new_content = serialize_frontmatter(new_fm) + disk_body
        validate_content_size(new_content)
        if write_error := safe_write_text(
            file_path,
            new_content,
            relative_path,
            expected_revision=revision,
            check_revision=True,
        ):
            return write_error

    ignore_next_change(relative_path)
    result = success_result(relative_path, f"Frontmatter enriched: {relative_path}")
    result["frontmatter_enriched"] = True
    result["frontmatter_fields_filled"] = len(new_values)
    if warnings:
        result["_validation_warnings"] = warnings
    if suggestions:
        result["_validation_suggestions"] = suggestions
    return result


def is_ai_enrichment_enabled() -> bool:
    """Confirm schema, external consent, and configured transport."""
    frontmatter = get_config().frontmatter
    ai = frontmatter.ai
    return bool(
        frontmatter.enabled
        and ai.enabled
        and ai.allow_external_processing
        and ai.provider
        and ai.provider.strip()
        and ai.command
    )


def _is_empty_value(value: Any) -> bool:
    """Return whether a frontmatter value is empty."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def _is_field_missing_or_empty(frontmatter: dict[str, Any], field_name: str) -> bool:
    """Return whether a field is missing or empty."""
    if field_name not in frontmatter:
        return True
    return _is_empty_value(frontmatter[field_name])


def _format_validation_errors(errors: list[dict[str, Any]]) -> str:
    """Format validation errors as one message."""
    return "Frontmatter validation failed: " + "; ".join(
        f"{error['field']}: {error['message']}" for error in errors
    )


def _can_defer_required_missing(errors: list[dict[str, Any]]) -> bool:
    """
    Return true when every error is `required_missing` and deferral is enabled.
    """
    if not errors:
        return False

    config = get_config().frontmatter
    if not config.enabled or not is_ai_enrichment_enabled():
        return False
    if not config.ai.allow_defer_required_on_create:
        return False

    return all(error.get("code") == "required_missing" for error in errors)


@return_write_lock_timeout
def create_note(
    relative_path: str,
    content: str,
    frontmatter: dict[str, Any] | None = None,
    validate_schema: bool = True,
) -> OperationResult:
    """
    Create a Markdown note and fail if it already exists.

    Only .md is supported because Canvas uses JSON.

    Parameters:
        relative_path: vault-relative path, such as 'folder/new-note.md'
        content: note body without frontmatter
        frontmatter: optional YAML metadata
        validate_schema: validate against the configured schema when true

    Returns:
        OperationResult with optional validation warnings and suggestions.
    """
    # Work with a mutable frontmatter dictionary.
    frontmatter = dict(frontmatter) if frontmatter else {}

    # Validate the schema, which may generate fields such as id.
    validation_warnings = []
    validation_suggestions = []

    if validate_schema:
        validation_result = validate_frontmatter_schema_result(frontmatter)
        validation_warnings = validation_result["warnings"]
        validation_suggestions = validation_result["suggestions"]
        frontmatter = validation_result["validated_data"]

        if not validation_result["valid"]:
            if _can_defer_required_missing(validation_result["errors"]):
                validation_warnings.append(
                    {
                        "field": "_schema",
                        "message": "Missing required fields deferred to reindex enrichment",
                        "code": "required_missing_deferred",
                        "value": None,
                    }
                )
            else:
                return error_result(
                    relative_path,
                    _format_validation_errors(validation_result["errors"]),
                )

    # Generate UUIDv7 when the schema did not provide an id.
    if "id" not in frontmatter:
        frontmatter["id"] = generate_uuid7()

    # Assemble the final note.
    fm_str = serialize_frontmatter(frontmatter or {})
    full_content = fm_str + content

    # Validate the serialized frontmatter and content together.
    validate_content_size(full_content)

    file_path = validate_for_write(relative_path, content, frontmatter)
    with advisory_path_lock(file_path):
        file_path = validate_for_write(relative_path, content, frontmatter)
        if file_revision(file_path) is not None:
            return error_result(
                relative_path,
                f"Note already exists: {relative_path}. Use write_note to overwrite it.",
            )

        file_path.parent.mkdir(parents=True, exist_ok=True)
        if write_error := safe_write_text(
            file_path,
            full_content,
            relative_path,
            expected_revision=None,
            check_revision=True,
        ):
            return write_error

    logger.info("create_note completed")
    result = success_result(relative_path, f"Note created: {relative_path}")

    # Add warnings and suggestions to the result.
    if validation_warnings:
        result["_validation_warnings"] = validation_warnings
    if validation_suggestions:
        result["_validation_suggestions"] = validation_suggestions

    return result


@return_write_lock_timeout
def write_note(relative_path: str, content: str) -> OperationResult:
    """
    Overwrite or create a Markdown note from complete content.

    Use this when the caller already has the complete note, including any
    frontmatter. Only .md is supported because Canvas uses JSON.

    Parameters:
        relative_path: vault-relative path, such as 'folder/note.md'
        content: complete note content

    Returns:
        OperationResult describing success or failure.
    """
    file_path = validate_for_write(relative_path, content)
    with advisory_path_lock(file_path):
        file_path = validate_for_write(relative_path, content)
        revision = file_revision(file_path)
        existed = revision is not None
        file_path.parent.mkdir(parents=True, exist_ok=True)

        if write_error := safe_write_text(
            file_path,
            content,
            relative_path,
            expected_revision=revision,
            check_revision=True,
        ):
            return write_error

    action = "updated" if existed else "created"
    logger.info("write_note completed action=%s", action)
    return success_result(relative_path, f"Note {action}: {relative_path}")


@return_write_lock_timeout
def append_note(
    relative_path: str,
    content: str,
    separator: str = "\n\n",
) -> OperationResult:
    """
    Append content to an existing Markdown note.

    Only .md is supported because Canvas uses JSON.

    Parameters:
        relative_path: path relative to the vault
        content: content to append
        separator: separator between existing and appended content

    Returns:
        OperationResult describing success or failure.
    """
    file_path = validate_for_write(relative_path, content)
    with advisory_path_lock(file_path):
        file_path = validate_for_write(relative_path, content)
        revision = file_revision(file_path)
        if revision is None:
            return error_result(
                relative_path,
                f"Note not found: {relative_path}. Use create_note or write_note.",
            )

        existing, read_error = safe_read_text(file_path, relative_path)
        if read_error:
            return read_error
        assert existing is not None
        if file_revision(file_path) != revision:
            return _write_conflict(relative_path)

        # Add the separator only when it is not already present.
        if existing.endswith(separator):
            new_content = existing + content
        else:
            new_content = existing + separator + content

        # Validate the final result before writing.
        validate_content_size(new_content)

        if write_error := safe_write_text(
            file_path,
            new_content,
            relative_path,
            expected_revision=revision,
            check_revision=True,
        ):
            return write_error

    logger.info("append_note completed")
    return success_result(relative_path, f"Content appended: {relative_path}")


@return_write_lock_timeout
def update_frontmatter(
    relative_path: str,
    metadata: dict[str, Any],
    merge: bool = True,
    validate_schema: bool = True,
) -> OperationResult:
    """
    Update YAML frontmatter on an existing Markdown note.

    Only .md is supported because Canvas uses JSON. Merge is shallow: arrays
    and objects are replaced instead of recursively merged.

    Parameters:
        relative_path: path relative to the vault
        metadata: new metadata
        merge: shallow-merge when true, otherwise replace all frontmatter
        validate_schema: validate resulting frontmatter against the schema when true

    Returns:
        OperationResult with optional validation warnings and suggestions.
    """
    # Require a metadata dictionary.
    if not isinstance(metadata, dict):
        raise ValueError(f"metadata must be a dictionary, got {type(metadata).__name__}")

    file_path = validate_for_write(relative_path, frontmatter=metadata)
    with advisory_path_lock(file_path):
        file_path = validate_for_write(relative_path, frontmatter=metadata)
        revision = file_revision(file_path)
        if revision is None:
            return error_result(relative_path, f"Note not found: {relative_path}")

        content, read_error = safe_read_text(file_path, relative_path)
        if read_error:
            return read_error
        assert content is not None
        if file_revision(file_path) != revision:
            return _write_conflict(relative_path)

        existing_fm, body = parse_frontmatter(content)

        if merge:
            new_fm = {**existing_fm, **metadata}
        else:
            new_fm = metadata

        # Validate the schema and apply configured coercions.
        validation_warnings = []
        validation_suggestions = []

        if validate_schema:
            try:
                validated_data, errors, warnings, suggestions = validate_frontmatter_schema(new_fm)
                new_fm = validated_data
                validation_warnings = warnings
                validation_suggestions = suggestions
            except ValueError as e:
                return error_result(relative_path, str(e))

        # Validate the final size.
        validate_frontmatter_size(new_fm)

        fm_str = serialize_frontmatter(new_fm)
        new_content = fm_str + body

        if write_error := safe_write_text(
            file_path,
            new_content,
            relative_path,
            expected_revision=revision,
            check_revision=True,
        ):
            return write_error

    action = "merged" if merge else "replaced"
    logger.info("update_frontmatter completed action=%s", action)
    result = success_result(relative_path, f"Frontmatter {action}: {relative_path}")

    # Add warnings and suggestions to the result.
    if validation_warnings:
        result["_validation_warnings"] = validation_warnings
    if validation_suggestions:
        result["_validation_suggestions"] = validation_suggestions

    return result


@return_write_lock_timeout
def enrich_note_frontmatter_required(
    relative_path: str,
    validate_schema: bool = True,
) -> OperationResult:
    """
    Enrich missing required frontmatter fields through the configured provider.

    The asynchronous reindex_note watcher flow uses this function. Existing
    fields are never overwritten.
    """
    if not relative_path.lower().endswith(".md"):
        return success_result(relative_path, "Enrichment skipped: unsupported extension")

    file_path = resolve_path(relative_path)
    content, read_error = _read_locked_text(file_path, relative_path)
    if read_error:
        return read_error
    assert content is not None

    existing_fm, body = parse_frontmatter(content)

    # A disabled schema has no required-field contract to enrich.
    validator = get_frontmatter_validator()
    if not validator.config.enabled:
        result = success_result(relative_path, "Frontmatter schema is disabled")
        result["frontmatter_enriched"] = False
        result["frontmatter_fields_filled"] = 0
        return result

    required_schema_fields = get_required_schema_fields(validator.config.schema)
    if not required_schema_fields:
        result = success_result(relative_path, "Schema has no required fields")
        result["frontmatter_enriched"] = False
        result["frontmatter_fields_filled"] = 0
        return result

    missing_required = [
        field_name
        for field_name in required_schema_fields
        if _is_field_missing_or_empty(existing_fm, field_name)
    ]
    if not missing_required:
        result = success_result(relative_path, "Frontmatter already contains every required field")
        result["frontmatter_enriched"] = False
        result["frontmatter_fields_filled"] = 0
        return result

    logger.info(
        "frontmatter_enrichment_started",
        extra={"missing_field_count": len(missing_required)},
    )

    try:
        generated_fields = generate_required_fields_with_ai(
            note_path=relative_path,
            note_body=body,
            current_frontmatter=existing_fm,
            required_schema_fields=required_schema_fields,
        )
    except (FrontmatterEnrichmentError, FrontmatterEnrichmentConfigError) as exc:
        return error_result(relative_path, str(exc))

    return _persist_generated_frontmatter(
        relative_path,
        file_path,
        required_schema_fields,
        generated_fields,
        validate_schema,
    )


@return_write_lock_timeout
def ensure_note_id(
    relative_path: str,
    validate_schema: bool = True,
) -> OperationResult:
    """Ensure an id while holding the path lock for the full read-modify-write."""
    if not relative_path.lower().endswith(".md"):
        return error_result(relative_path, "Only .md is supported")
    file_path = resolve_path(relative_path)
    with advisory_path_lock(file_path):
        return _ensure_note_id_locked(relative_path, validate_schema)


def _ensure_note_id_locked(
    relative_path: str,
    validate_schema: bool = True,
) -> OperationResult:
    """
    Ensure a note has a unique frontmatter id.

    Existing ids are preserved. Otherwise, generate and add a UUIDv7. The
    complete frontmatter can optionally be validated against the schema.

    Parameters:
        relative_path: path relative to the vault
        validate_schema: validate against the configured schema when true

    Returns:
        OperationResult with id_added indicating whether an id was added.
        Includes validation warnings and suggestions when present.
    """
    # Validate the extension before resolving the path.
    if not relative_path.lower().endswith(".md"):
        return error_result(relative_path, "Only .md is supported")

    file_path = resolve_path(relative_path)

    revision = file_revision(file_path)
    if revision is None:
        return error_result(relative_path, f"Note not found: {relative_path}")

    content, read_error = safe_read_text(file_path, relative_path)
    if read_error:
        return read_error
    assert content is not None
    if file_revision(file_path) != revision:
        return _write_conflict(relative_path)

    existing_fm, body = parse_frontmatter(content)

    # Preserve an existing id while optionally validating the schema.
    if "id" in existing_fm:
        result = success_result(relative_path, f"Note already has an id: {relative_path}")
        result["id_added"] = False
        result["id"] = existing_fm["id"]

        # Validate existing frontmatter to report warnings and suggestions.
        if validate_schema:
            try:
                _, _, warnings, suggestions = validate_frontmatter_schema(existing_fm)
                if warnings:
                    result["_validation_warnings"] = warnings
                if suggestions:
                    result["_validation_suggestions"] = suggestions
            except ValueError:
                pass  # Preserve existing notes even when schema validation fails.

        return result

    # Validate the schema and obtain a generated id when configured.
    validation_warnings = []
    validation_suggestions = []

    if validate_schema:
        try:
            validated_data, errors, warnings, suggestions = validate_frontmatter_schema(existing_fm)
            # Use an id generated by the schema when available.
            if "id" in validated_data and "id" not in existing_fm:
                new_fm = {
                    "id": validated_data["id"],
                    **{k: v for k, v in existing_fm.items() if k != "id"},
                }
                new_fm.update(
                    {k: v for k, v in validated_data.items() if k != "id" and k not in existing_fm}
                )
            else:
                # Generate an id directly.
                new_id = generate_uuid7()
                new_fm = {"id": new_id, **existing_fm}
            validation_warnings = warnings
            validation_suggestions = suggestions
        except ValueError as e:
            return error_result(relative_path, str(e))
    else:
        # Generate and add an id without schema validation.
        new_id = generate_uuid7()
        new_fm = {"id": new_id, **existing_fm}  # Keep id first in serialized YAML.

    fm_str = serialize_frontmatter(new_fm)
    new_content = fm_str + body

    if write_error := safe_write_text(
        file_path,
        new_content,
        relative_path,
        expected_revision=revision,
        check_revision=True,
    ):
        return write_error

    # Let the watcher ignore this successfully persisted internal change.
    ignore_next_change(relative_path)

    logger.info("ensure_note_id completed")
    result = success_result(relative_path, f"ID added: {relative_path}")
    result["id_added"] = True
    result["id"] = new_fm["id"]

    # Add warnings and suggestions to the result.
    if validation_warnings:
        result["_validation_warnings"] = validation_warnings
    if validation_suggestions:
        result["_validation_suggestions"] = validation_suggestions

    return result
