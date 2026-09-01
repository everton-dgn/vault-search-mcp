"""
Delete and move operations for vault notes.
"""

import logging
import shutil
import uuid
from pathlib import Path

from vault_search.config.paths import VAULT_PATH
from vault_search.crud.locking import (
    advisory_path_lock,
    advisory_path_locks,
    file_revision,
    return_write_lock_timeout,
)
from vault_search.crud.types import OperationResult, error_result, success_result
from vault_search.crud.validation import (
    resolve_internal_path,
    resolve_path,
    validate_extension,
    validate_not_ignored_folder,
)

logger = logging.getLogger(__name__)


def _write_conflict(relative_path: str) -> OperationResult:
    """Return a safe conflict when the source or destination changed."""
    return error_result(
        relative_path,
        "Write conflict: the note changed during the operation. Try again.",
        error_code="write_conflict",
    )


@return_write_lock_timeout
def delete_note(relative_path: str) -> OperationResult:
    """
    Delete a note by moving it to the vault's .trash directory.

    Permanent deletion is intentionally unsupported. Files remain recoverable.

    Parameters:
        relative_path: path relative to the vault

    Returns:
        OperationResult describing success or failure.
    """
    validate_extension(relative_path)
    validate_not_ignored_folder(relative_path)  # Do not delete from ignored folders.
    file_path = resolve_path(relative_path)
    with advisory_path_lock(file_path):
        file_path = resolve_path(relative_path)
        revision = file_revision(file_path)
        if revision is None:
            return error_result(relative_path, f"Note not found: {relative_path}")

        trash_dir = resolve_internal_path(".trash")
        trash_dir.mkdir(exist_ok=True)
        relative = Path(relative_path)
        # Resolving internal components prevents a symlink in .trash from
        # redirecting the move outside the vault.
        trash_path = resolve_internal_path(".trash", *relative.parts)
        trash_path.parent.mkdir(parents=True, exist_ok=True)

        final_trash_path = trash_path
        max_attempts = 10
        for attempt in range(max_attempts):
            if not final_trash_path.exists():
                break
            unique_id = uuid.uuid4().hex[:8]
            final_trash_path = trash_path.with_stem(f"{trash_path.stem}_{unique_id}")
            logger.debug(
                "delete_note collision attempt=%d max_attempts=%d",
                attempt + 1,
                max_attempts,
            )
        else:
            logger.error("delete_note collision_limit=%d", max_attempts)
            return error_result(
                relative_path,
                f"Could not generate a unique trash name after {max_attempts} attempts",
            )

        if file_revision(file_path) != revision:
            return _write_conflict(relative_path)
        try:
            shutil.move(str(file_path), str(final_trash_path))
        except (OSError, shutil.Error) as e:
            logger.error("delete_note_failed error_type=%s", type(e).__name__)
            return error_result(relative_path, "Failed to move note to trash")

    logger.info("delete_note completed destination=trash")
    return success_result(
        relative_path,
        "Note moved to trash: "
        f"{final_trash_path.relative_to(VAULT_PATH.expanduser().resolve(strict=False))}",
    )


@return_write_lock_timeout
def move_note(from_path: str, to_path: str) -> OperationResult:
    """
    Move or rename a note.

    Parameters:
        from_path: current vault-relative path
        to_path: new vault-relative path outside ignored folders

    Returns:
        OperationResult describing success or failure.
    """
    validate_extension(from_path)
    validate_extension(to_path)  # Destination must have a valid extension.
    validate_not_ignored_folder(to_path)
    validate_not_ignored_folder(from_path)

    # Reject extension changes that could corrupt the file format.
    from_ext = Path(from_path).suffix.lower()
    to_ext = Path(to_path).suffix.lower()
    if from_ext != to_ext:
        return error_result(
            from_path,
            f"Cannot change extension from '{from_ext}' to '{to_ext}'. "
            "This could corrupt the file.",
        )

    from_file = resolve_path(from_path)
    to_file = resolve_path(to_path)
    with advisory_path_locks(from_file, to_file):
        from_file = resolve_path(from_path)
        to_file = resolve_path(to_path)
        source_revision = file_revision(from_file)
        if source_revision is None:
            return error_result(from_path, f"Source note not found: {from_path}")
        if file_revision(to_file) is not None:
            return error_result(to_path, f"Destination already exists: {to_path}")

        to_file.parent.mkdir(parents=True, exist_ok=True)
        if file_revision(from_file) != source_revision or file_revision(to_file) is not None:
            return _write_conflict(from_path)
        try:
            shutil.move(str(from_file), str(to_file))
        except (OSError, shutil.Error) as e:
            logger.error("move_note_failed error_type=%s", type(e).__name__)
            return error_result(from_path, "Failed to move note")

    logger.info("move_note completed")
    return success_result(to_path, f"Note moved: {from_path} -> {to_path}")
