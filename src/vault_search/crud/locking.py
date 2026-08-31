"""Lock advisory e revisões de arquivo para escritas CRUD concorrentes."""

from __future__ import annotations

import errno
import hashlib
import math
import os
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Concatenate

from vault_search.crud.types import OperationResult, error_result

try:
    import fcntl
except ImportError:  # pragma: no cover - fcntl não existe no Windows
    fcntl = None  # type: ignore[assignment]


HAS_FCNTL = fcntl is not None
WRITE_LOCK_POLL_SECONDS = 0.05


def _load_default_timeout() -> float:
    """Lê override do ambiente com fallback fechado e limitado."""
    raw_value = os.environ.get("VAULT_SEARCH_WRITE_LOCK_TIMEOUT_SECONDS")
    if raw_value is None:
        return 5.0
    try:
        timeout = float(raw_value)
    except ValueError:
        return 5.0
    if not math.isfinite(timeout) or timeout < 0 or timeout > 300:
        return 5.0
    return timeout


WRITE_LOCK_TIMEOUT_SECONDS = _load_default_timeout()


class WriteLockTimeoutError(TimeoutError):
    """Indica que outro escritor reteve o lock além do prazo seguro."""

    error_code = "write_lock_timeout"


def return_write_lock_timeout[**P](
    operation: Callable[Concatenate[str, P], OperationResult],
) -> Callable[Concatenate[str, P], OperationResult]:
    """Converte expiração interna em resultado CRUD estável e seguro."""

    @wraps(operation)
    def wrapped(relative_path: str, *args: P.args, **kwargs: P.kwargs) -> OperationResult:
        try:
            return operation(relative_path, *args, **kwargs)
        except WriteLockTimeoutError:
            return error_result(
                relative_path,
                "Tempo limite ao aguardar outra escrita. Tente novamente.",
                error_code=WriteLockTimeoutError.error_code,
            )

    return wrapped


@dataclass(frozen=True, slots=True)
class FileRevision:
    """Identidade e versão observável de um arquivo."""

    inode: int
    mtime_ns: int
    size: int


_registry_guard = threading.Lock()
_thread_locks: weakref.WeakValueDictionary[str, threading.RLock] = weakref.WeakValueDictionary()


def file_revision(file_path: Path) -> FileRevision | None:
    """Captura a revisão usada para detectar alterações entre leitura e replace."""
    try:
        metadata = file_path.stat()
    except FileNotFoundError:
        return None
    return FileRevision(
        inode=metadata.st_ino,
        mtime_ns=metadata.st_mtime_ns,
        size=metadata.st_size,
    )


def _thread_lock(key: str) -> threading.RLock:
    """Mantém exclusão mútua também em plataformas sem flock por thread."""
    with _registry_guard:
        lock = _thread_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _thread_locks[key] = lock
        return lock


def _lock_path(file_path: Path) -> Path:
    """Deriva nome opaco de lock dentro de diretório interno validado."""
    from vault_search.crud.validation import resolve_internal_path

    canonical_path = file_path.resolve(strict=False)
    vault_root = resolve_internal_path()
    try:
        canonical_path.relative_to(vault_root)
    except ValueError as exc:
        raise ValueError("Path de lock inválido ou fora do vault.") from exc
    digest = hashlib.sha256(os.fsencode(canonical_path)).hexdigest()
    lock_dir = resolve_internal_path(".vault-search-locks")
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_dir = lock_dir.resolve(strict=True)
    return lock_dir / f"{digest}.lock"


def _validated_timeout(timeout_seconds: float | None) -> float:
    """Normaliza timeout explícito sem permitir espera ilimitada ou NaN."""
    timeout = WRITE_LOCK_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    if not math.isfinite(timeout) or timeout < 0 or timeout > 300:
        raise ValueError("Timeout do lock deve estar entre 0 e 300 segundos.")
    return timeout


def _acquire_thread_lock(lock: threading.RLock, deadline: float) -> None:
    """Adquire o lock local respeitando o mesmo prazo do flock."""
    remaining = max(0.0, deadline - time.monotonic())
    if not lock.acquire(timeout=remaining):
        raise WriteLockTimeoutError("Tempo limite ao aguardar lock de escrita.")


def _acquire_flock(lock_fd: int, deadline: float) -> None:
    """Tenta LOCK_NB com pausa curta e deadline monotônico."""
    assert fcntl is not None
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WriteLockTimeoutError("Tempo limite ao aguardar lock de escrita.") from None
            time.sleep(min(WRITE_LOCK_POLL_SECONDS, remaining))


def _open_lock_file(file_path: Path, initial_lock_path: Path) -> int:
    """Abre o lock com uma única recuperação se o diretório mudar no meio."""
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    lock_path = initial_lock_path

    for attempt in range(2):
        directory_fd: int | None = None
        try:
            directory_fd = os.open(
                lock_path.parent,
                directory_flags | close_on_exec | no_follow,
            )
            return os.open(
                lock_path.name,
                os.O_CREAT | os.O_RDWR | close_on_exec | no_follow,
                0o600,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            if attempt == 1:
                raise
            lock_path = _lock_path(file_path)
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

    raise AssertionError("tentativas de abertura do lock esgotadas")  # pragma: no cover


@contextmanager
def advisory_path_lock(
    file_path: Path,
    *,
    timeout_seconds: float | None = None,
) -> Iterator[None]:
    """
    Serializa operações pelo path canônico.

    Em Unix, ``flock`` coordena processos. Em plataformas sem ``fcntl``, o
    fallback cobre somente threads do processo atual.
    """
    timeout = _validated_timeout(timeout_seconds)
    deadline = time.monotonic() + timeout
    lock_path = _lock_path(file_path)
    thread_lock = _thread_lock(lock_path.name)
    _acquire_thread_lock(thread_lock, deadline)
    try:
        if fcntl is None:
            yield
            return

        lock_fd = _open_lock_file(file_path, lock_path)

        acquired = False
        try:
            _acquire_flock(lock_fd, deadline)
            acquired = True
            yield
        finally:
            try:
                if acquired:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
    finally:
        thread_lock.release()


@contextmanager
def advisory_path_locks(
    *file_paths: Path,
    timeout_seconds: float | None = None,
) -> Iterator[None]:
    """Adquire vários paths em ordem estável usando um prazo compartilhado."""
    timeout = _validated_timeout(timeout_seconds)
    deadline = time.monotonic() + timeout
    unique_paths = {
        os.fspath(file_path.resolve(strict=False)): file_path for file_path in file_paths
    }
    with ExitStack() as stack:
        for key in sorted(unique_paths):
            remaining = max(0.0, deadline - time.monotonic())
            stack.enter_context(
                advisory_path_lock(
                    unique_paths[key],
                    timeout_seconds=remaining,
                )
            )
        yield
