"""
Handler de eventos do sistema de arquivos para o watcher.

Responsável por:
- Filtrar arquivos por extensão e pastas ignoradas
- Enfileirar eventos com coalescência por path
- Ignorar mudanças auto-geradas (ex: adição de UUID)
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

# Tokens de eventos próprios são curtos e limitados para não reter paths
# indefinidamente quando o watcher não recebe o evento esperado.
IGNORE_TOKEN_TTL_SECONDS = 30.0
MAX_IGNORE_TOKENS = 2048


class PendingEvent(TypedDict):
    """Evento coalescido aguardando o fim do debounce."""

    deleted: bool
    time: float


@dataclass(frozen=True, slots=True)
class _FileRevision:
    """Identidade observável de uma revisão no filesystem."""

    inode: int
    mtime_ns: int
    size: int


@dataclass(frozen=True, slots=True)
class _IgnoreToken:
    """Revisão própria que pode ser ignorada até um prazo monotônico."""

    revision: _FileRevision
    expires_at: float


# OrderedDict preserva a ordem de criação para expulsão determinística.
_ignore_next_change: OrderedDict[str, _IgnoreToken] = OrderedDict()
_ignore_lock = threading.Lock()


def _file_revision(path: Path) -> _FileRevision | None:
    """Lê a revisão comparável de um arquivo, sem propagar falhas transitórias."""
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
    """Resolve um path relativo e rejeita escapes do vault."""
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
    """Remove tokens vencidos. O chamador deve manter ``_ignore_lock``."""
    expired = [path for path, token in _ignore_next_change.items() if token.expires_at <= now]
    for path in expired:
        _ignore_next_change.pop(path, None)


def ignore_next_change(relative_path: str) -> bool:
    """
    Marca um path para ignorar a próxima mudança detectada pelo watcher.

    Usado quando o sistema modifica um arquivo (ex: adiciona UUID) e não
    quer que o watcher dispare uma reindexação desnecessária.

    O token só é criado quando a revisão gravada pode ser lida. Assim, uma
    edição posterior com o mesmo path nunca é ignorada por uma flag antiga.

    Parâmetros:
        relative_path: caminho relativo ao vault

    Retorna:
        True quando a revisão foi registrada; False quando o arquivo não pôde
        ser lido ou o path escapava do vault.
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
    Consome o token e compara sua revisão com o estado atual do arquivo.

    Retorna:
        True apenas quando o token existe, está válido e representa exatamente
        a revisão atual. Tokens divergentes também são removidos, mas o evento
        continua para que uma edição posterior do usuário seja processada.
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
    Handler para eventos do sistema de arquivos no vault.

    Enfileira eventos num dict compartilhado (coalescente por path).
    O worker thread processa a fila periodicamente.
    """

    def __init__(self, pending: dict[str, PendingEvent], lock: threading.Lock):
        """
        Parâmetros:
            pending: dict compartilhado {relative_path: {"deleted": bool, "time": float}}
            lock: lock compartilhado para acesso ao pending
        """
        super().__init__()
        self._pending = pending
        self._lock = lock

    @staticmethod
    def _get_vault_root() -> Path:
        """
        Retorna root normalizado do vault.

        Resolve symlinks para suportar eventos vindos do caminho real.
        """
        return VAULT_PATH.expanduser().resolve(strict=False)

    def _should_process(self, path: bytes | str) -> bool:
        """Verifica se o arquivo deve ser processado."""
        p = Path(fsdecode(path))
        # Extensão case-insensitive
        if p.suffix.lower() not in INDEXABLE_EXTENSIONS:
            return False
        if any(ignored in p.parts for ignored in IGNORED_FOLDERS):
            return False
        return True

    def _enqueue(self, abs_path: bytes | str, deleted: bool = False) -> None:
        """
        Enfileira evento para processamento com debounce coalescente.

        Se já existe evento pendente para o mesmo path, sobrescreve
        (última edição vence).

        Ignora mudanças marcadas com ignore_next_change() (ex: UUID auto-gerado).

        Parâmetros:
            abs_path: caminho absoluto da nota
            deleted: se True, a nota foi deletada
        """
        p = Path(fsdecode(abs_path)).expanduser().resolve(strict=False)
        try:
            relative = str(p.relative_to(self._get_vault_root()))
        except ValueError:
            return

        # Ignorar somente a revisão que o próprio processo acabou de gravar.
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
