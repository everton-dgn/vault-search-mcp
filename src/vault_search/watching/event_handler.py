"""
Filesystem event normalization and coalescing for vault watchers.

Responsibilities:
- Filter files by extension and ignored folders
- Coalesce queued events by path
- Ignore revisions written internally, such as UUID insertion
"""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from os import fsdecode
from pathlib import Path
from typing import TypedDict

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)

from vault_search.config.paths import VAULT_PATH
from vault_search.config.search import IGNORED_FOLDERS, INDEXABLE_EXTENSIONS

# Internal event tokens are short-lived and bounded so paths are not retained
# indefinitely when the watcher misses an expected event.
IGNORE_TOKEN_TTL_SECONDS = 30.0
MAX_IGNORE_TOKENS = 2048


class PendingEvent(TypedDict):
    """Coalesced event waiting for its debounce window."""

    deleted: bool
    time: float


@dataclass(frozen=True, slots=True)
class _FileRevision:
    """Observable identity of a filesystem revision."""

    inode: int
    mtime_ns: int
    size: int


@dataclass(frozen=True, slots=True)
class _IgnoreToken:
    """Internal revision that can be ignored until a monotonic deadline."""

    revision: _FileRevision
    expires_at: float


# OrderedDict preserves creation order for deterministic eviction.
_ignore_next_change: OrderedDict[str, _IgnoreToken] = OrderedDict()
_ignore_lock = threading.Lock()


def _file_revision(path: Path) -> _FileRevision | None:
    """Read a comparable file revision without propagating transient failures."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return _FileRevision(
        inode=stat.st_ino,
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
    )


def _vault_file(relative_path: str) -> Path | None:
    """Resolve a relative path and reject vault escapes."""
    relative = Path(relative_path)
    if relative.is_absolute():
        return None

    root = VAULT_PATH.expanduser().resolve(strict=False)
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _purge_ignore_tokens(now: float) -> None:
    """Remove expired tokens while the caller holds ``_ignore_lock``."""
    expired = [path for path, token in _ignore_next_change.items() if token.expires_at <= now]
    for path in expired:
        _ignore_next_change.pop(path, None)


def ignore_next_change(relative_path: str) -> bool:
    """
    Mark the current revision so the watcher can ignore its next event.

    Use this after an internal file mutation, such as adding a UUID, when a
    second reindex would be redundant.

    A token is created only when the written revision can be read. A later
    edit to the same path therefore cannot match a stale token.

    Parameters:
        relative_path: path relative to the vault

    Returns:
        True when the revision was recorded, otherwise false.
    """
    path = _vault_file(relative_path)
    revision = _file_revision(path) if path is not None else None
    if revision is None:
        return False

    now = time.monotonic()
    with _ignore_lock:
        _purge_ignore_tokens(now)
        _ignore_next_change.pop(relative_path, None)
        while len(_ignore_next_change) >= MAX_IGNORE_TOKENS:
            _ignore_next_change.popitem(last=False)
        _ignore_next_change[relative_path] = _IgnoreToken(
            revision=revision,
            expires_at=now + IGNORE_TOKEN_TTL_SECONDS,
        )
    return True


def _check_and_clear_ignore(relative_path: str, absolute_path: Path | None = None) -> bool:
    """
    Consume a token and compare its revision with the current file.

    Returns:
        True only when an unexpired token exactly matches the current revision.
        Mismatched tokens are removed while the event continues normally.
    """
    path = absolute_path or _vault_file(relative_path)
    revision = _file_revision(path) if path is not None else None
    now = time.monotonic()
    with _ignore_lock:
        _purge_ignore_tokens(now)
        token = _ignore_next_change.pop(relative_path, None)
    return token is not None and revision is not None and token.revision == revision


class VaultEventHandler(FileSystemEventHandler):
    """
    Handle filesystem events inside the vault.

    Events are coalesced by path in a shared dictionary and processed by one
    worker thread.
    """

    def __init__(self, pending: dict[str, PendingEvent], lock: threading.Lock):
        """
        Parameters:
            pending: shared {relative_path: {"deleted": bool, "time": float}} map
            lock: lock protecting pending
        """
        super().__init__()
        self._pending = pending
        self._lock = lock

    @staticmethod
    def _get_vault_root() -> Path:
        """
        Return the normalized vault root.

        Resolve symlinks so events from the real path remain supported.
        """
        return VAULT_PATH.expanduser().resolve(strict=False)

    def _should_process(self, path: bytes | str) -> bool:
        """Return whether a file should be processed."""
        p = Path(fsdecode(path))
        # Extensions are case-insensitive.
        if p.suffix.lower() not in INDEXABLE_EXTENSIONS:
            return False
        if any(ignored in p.parts for ignored in IGNORED_FOLDERS):
            return False
        return True

    def _enqueue(self, abs_path: bytes | str, deleted: bool = False) -> None:
        """
        Enqueue an event with coalescing debounce.

        A newer event for the same path replaces the pending event.

        Revisions registered by ignore_next_change are ignored exactly once.

        Parameters:
            abs_path: absolute note path
            deleted: whether the note was deleted
        """
        p = Path(fsdecode(abs_path)).expanduser().resolve(strict=False)
        try:
            relative = str(p.relative_to(self._get_vault_root()))
        except ValueError:
            return

        # Ignore only the exact revision written by this process.
        if _check_and_clear_ignore(relative, p):
            return

        with self._lock:
            self._pending[relative] = {
                "deleted": deleted,
                "time": time.monotonic(),
            }

    def on_created(self, event: DirCreatedEvent | FileCreatedEvent) -> None:
        if not event.is_directory and self._should_process(event.src_path):
            self._enqueue(event.src_path)

    def on_modified(self, event: DirModifiedEvent | FileModifiedEvent) -> None:
        if not event.is_directory and self._should_process(event.src_path):
            self._enqueue(event.src_path)

    def on_deleted(self, event: DirDeletedEvent | FileDeletedEvent) -> None:
        if not event.is_directory and self._should_process(event.src_path):
            self._enqueue(event.src_path, deleted=True)

    def on_moved(self, event: DirMovedEvent | FileMovedEvent) -> None:
        if not event.is_directory:
            if self._should_process(event.src_path):
                self._enqueue(event.src_path, deleted=True)
            if self._should_process(event.dest_path):
                self._enqueue(event.dest_path)
