"""Regressões de concorrência nas escritas CRUD."""

from __future__ import annotations

import multiprocessing
import threading
from pathlib import Path
from typing import Any

import pytest

PROCESS_START_TIMEOUT_SECONDS = 30.0
PROCESS_RESULT_TIMEOUT_SECONDS = 30.0
PROCESS_JOIN_TIMEOUT_SECONDS = 30.0
PROCESS_HOLDER_TIMEOUT_SECONDS = 60.0
LOCK_BLOCK_OBSERVATION_SECONDS = 0.5


def _configure_vault(monkeypatch: pytest.MonkeyPatch, vault: Path) -> None:
    from vault_search.crud import validation

    monkeypatch.setattr(validation, "VAULT_PATH", vault)


def _hold_process_lock(
    vault: str,
    note: str,
    entered: Any,
    release: Any,
) -> None:
    from vault_search.crud import validation
    from vault_search.crud.locking import advisory_path_lock

    validation.VAULT_PATH = Path(vault)
    with advisory_path_lock(Path(note)):
        entered.set()
        release.wait(PROCESS_HOLDER_TIMEOUT_SECONDS)


def _observe_process_lock(vault: str, note: str, ready: Any, acquired: Any) -> None:
    from vault_search.crud import validation
    from vault_search.crud.locking import advisory_path_lock

    validation.VAULT_PATH = Path(vault)
    ready.set()
    with advisory_path_lock(Path(note)):
        acquired.set()


def _process_create_note(vault: str, start: Any, results: Any, content: str) -> None:
    from vault_search.crud import validation
    from vault_search.crud.write import create_note

    validation.VAULT_PATH = Path(vault)
    start.wait(PROCESS_START_TIMEOUT_SECONDS)
    results.put(create_note("shared.md", content, validate_schema=False))


def _process_append_note(vault: str, start: Any, results: Any, marker: str) -> None:
    from vault_search.crud import validation
    from vault_search.crud.write import append_note

    validation.VAULT_PATH = Path(vault)
    start.wait(PROCESS_START_TIMEOUT_SECONDS)
    results.put(append_note("shared.md", marker))


def _join_processes(processes: list[Any]) -> None:
    for process in processes:
        if process.pid is None:
            continue
        process.join(timeout=PROCESS_JOIN_TIMEOUT_SECONDS)
        if process.is_alive():
            process.terminate()
            process.join(timeout=PROCESS_JOIN_TIMEOUT_SECONDS)


def test_concurrent_create_has_one_success_and_one_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud.write import create_note

    _configure_vault(monkeypatch, tmp_path)
    barrier = threading.Barrier(2)
    results: list[dict[str, Any]] = []

    def create(content: str) -> None:
        barrier.wait()
        results.append(create_note("same.md", content, validate_schema=False))

    threads = [
        threading.Thread(target=create, args=("first",)),
        threading.Thread(target=create, args=("second",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert sorted(result["success"] for result in results) == [False, True]
    assert sum("já existe" in result["message"] for result in results) == 1
    assert (tmp_path / "same.md").read_text(encoding="utf-8").endswith(("first", "second"))


def test_concurrent_appends_are_each_persisted_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud.write import append_note

    _configure_vault(monkeypatch, tmp_path)
    note = tmp_path / "note.md"
    note.write_text("base", encoding="utf-8")
    barrier = threading.Barrier(2)
    results: list[dict[str, Any]] = []

    def append(marker: str) -> None:
        barrier.wait()
        results.append(append_note("note.md", marker))

    threads = [
        threading.Thread(target=append, args=("alpha",)),
        threading.Thread(target=append, args=("beta",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    content = note.read_text(encoding="utf-8")
    assert not any(thread.is_alive() for thread in threads)
    assert all(result["success"] for result in results)
    assert content.count("alpha") == 1
    assert content.count("beta") == 1


def test_concurrent_frontmatter_merges_preserve_both_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud.write import update_frontmatter
    from vault_search.parsers.frontmatter import parse_frontmatter

    _configure_vault(monkeypatch, tmp_path)
    note = tmp_path / "note.md"
    note.write_text("---\ntitle: Base\n---\n\nbody", encoding="utf-8")
    barrier = threading.Barrier(2)
    results: list[dict[str, Any]] = []

    def update(metadata: dict[str, str]) -> None:
        barrier.wait()
        results.append(
            update_frontmatter(
                "note.md",
                metadata,
                merge=True,
                validate_schema=False,
            )
        )

    threads = [
        threading.Thread(target=update, args=({"owner": "Ada"},)),
        threading.Thread(target=update, args=({"status": "done"},)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    frontmatter, _ = parse_frontmatter(note.read_text(encoding="utf-8"))
    assert not any(thread.is_alive() for thread in threads)
    assert all(result["success"] for result in results)
    assert frontmatter["owner"] == "Ada"
    assert frontmatter["status"] == "done"


def test_external_change_causes_safe_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud import validation, write

    _configure_vault(monkeypatch, tmp_path)
    note = tmp_path / "note.md"
    note.write_text("original", encoding="utf-8")
    original_safe_write = write.safe_write_text

    def change_before_replace(*args: Any, **kwargs: Any):
        note.write_text("external edit", encoding="utf-8")
        return original_safe_write(*args, **kwargs)

    monkeypatch.setattr(write, "safe_write_text", change_before_replace)

    result = write.append_note("note.md", "agent edit")

    assert result["success"] is False
    assert "Conflito de escrita" in result["message"]
    assert note.read_text(encoding="utf-8") == "external edit"
    assert validation.file_revision(note) is not None


def test_failed_write_releases_lock_and_preserves_previous_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud import validation
    from vault_search.crud.write import append_note

    _configure_vault(monkeypatch, tmp_path)
    note = tmp_path / "note.md"
    note.write_text("original", encoding="utf-8")
    real_replace = validation.os.replace
    calls = 0

    def fail_once(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated failure")
        real_replace(source, destination)

    monkeypatch.setattr(validation.os, "replace", fail_once)

    failed = append_note("note.md", "lost")
    assert failed["success"] is False
    assert note.read_text(encoding="utf-8") == "original"

    succeeded = append_note("note.md", "kept")
    assert succeeded["success"] is True
    content = note.read_text(encoding="utf-8")
    assert "lost" not in content
    assert content.count("kept") == 1


@pytest.mark.skipif(
    "spawn" not in multiprocessing.get_all_start_methods(),
    reason="cross-process test requires multiprocessing spawn",
)
def test_advisory_lock_blocks_another_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud.locking import HAS_FCNTL

    if not HAS_FCNTL:
        pytest.skip("fcntl is unavailable")

    _configure_vault(monkeypatch, tmp_path)
    note = tmp_path / "note.md"
    note.write_text("content", encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    observer_ready = context.Event()
    acquired = context.Event()
    holder = context.Process(
        target=_hold_process_lock,
        args=(str(tmp_path), str(note), entered, release),
    )
    observer = context.Process(
        target=_observe_process_lock,
        args=(str(tmp_path), str(note), observer_ready, acquired),
    )

    holder.start()
    try:
        assert entered.wait(PROCESS_START_TIMEOUT_SECONDS)
        observer.start()
        assert observer_ready.wait(PROCESS_START_TIMEOUT_SECONDS)
        assert not acquired.wait(LOCK_BLOCK_OBSERVATION_SECONDS)
        release.set()
        assert acquired.wait(PROCESS_START_TIMEOUT_SECONDS)
    finally:
        release.set()
        _join_processes([holder, observer])

    assert holder.exitcode == 0
    assert observer.exitcode == 0


def test_advisory_lock_recovers_when_lock_directory_changes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud import locking

    if not locking.HAS_FCNTL:
        pytest.skip("fcntl is unavailable")

    _configure_vault(monkeypatch, tmp_path)
    real_open = locking.os.open
    injected_failures = 0

    def fail_first_relative_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal injected_failures
        if dir_fd is not None and injected_failures == 0:
            injected_failures += 1
            raise FileNotFoundError(2, "simulated lock directory replacement")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(locking.os, "open", fail_first_relative_open)

    with locking.advisory_path_lock(tmp_path / "note.md"):
        pass

    assert injected_failures == 1


@pytest.mark.skipif(
    "spawn" not in multiprocessing.get_all_start_methods(),
    reason="real CRUD process test requires multiprocessing spawn",
)
def test_real_create_and_append_are_serialized_across_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud.locking import HAS_FCNTL

    if not HAS_FCNTL:
        pytest.skip("fcntl is unavailable")
    _configure_vault(monkeypatch, tmp_path)
    context = multiprocessing.get_context("spawn")

    create_start = context.Event()
    create_results = context.Queue()
    creators = [
        context.Process(
            target=_process_create_note,
            args=(str(tmp_path), create_start, create_results, marker),
        )
        for marker in ("first", "second")
    ]
    for process in creators:
        process.start()
    try:
        create_start.set()
        create_outcomes = [
            create_results.get(timeout=PROCESS_RESULT_TIMEOUT_SECONDS) for _ in creators
        ]
    finally:
        create_start.set()
        _join_processes(creators)

    assert all(process.exitcode == 0 for process in creators)
    assert sorted(result["success"] for result in create_outcomes) == [False, True]

    append_start = context.Event()
    append_results = context.Queue()
    appenders = [
        context.Process(
            target=_process_append_note,
            args=(str(tmp_path), append_start, append_results, marker),
        )
        for marker in ("alpha-process", "beta-process")
    ]
    for process in appenders:
        process.start()
    try:
        append_start.set()
        append_outcomes = [
            append_results.get(timeout=PROCESS_RESULT_TIMEOUT_SECONDS) for _ in appenders
        ]
    finally:
        append_start.set()
        _join_processes(appenders)

    content = (tmp_path / "shared.md").read_text(encoding="utf-8")
    assert all(process.exitcode == 0 for process in appenders)
    assert all(result["success"] for result in append_outcomes)
    assert content.count("alpha-process") == 1
    assert content.count("beta-process") == 1


@pytest.mark.skipif(
    "spawn" not in multiprocessing.get_all_start_methods(),
    reason="lock timeout test requires multiprocessing spawn",
)
def test_crud_returns_write_lock_timeout_when_holder_exceeds_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud import locking
    from vault_search.crud.write import append_note

    if not locking.HAS_FCNTL:
        pytest.skip("fcntl is unavailable")
    _configure_vault(monkeypatch, tmp_path)
    monkeypatch.setattr(locking, "WRITE_LOCK_TIMEOUT_SECONDS", 0.1)
    note = tmp_path / "note.md"
    note.write_text("original", encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_process_lock,
        args=(str(tmp_path), str(note), entered, release),
    )

    holder.start()
    try:
        assert entered.wait(PROCESS_START_TIMEOUT_SECONDS)
        result = append_note("note.md", "blocked")
        assert result["success"] is False
        assert result["error_code"] == "write_lock_timeout"
        assert note.read_text(encoding="utf-8") == "original"
    finally:
        release.set()
        _join_processes([holder])

    assert holder.exitcode == 0


@pytest.mark.skipif(
    "spawn" not in multiprocessing.get_all_start_methods(),
    reason="residual CRUD timeout test requires multiprocessing spawn",
)
def test_all_residual_mutators_respect_the_same_lock_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud import locking
    from vault_search.crud.delete import delete_note, move_note
    from vault_search.crud.write import (
        enrich_note_frontmatter_required,
        ensure_note_id,
        write_note,
    )

    if not locking.HAS_FCNTL:
        pytest.skip("fcntl is unavailable")
    _configure_vault(monkeypatch, tmp_path)
    monkeypatch.setattr("vault_search.crud.delete.VAULT_PATH", tmp_path)
    monkeypatch.setattr(locking, "WRITE_LOCK_TIMEOUT_SECONDS", 0.05)
    note = tmp_path / "note.md"
    note.write_text("original", encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_process_lock,
        args=(str(tmp_path), str(note), entered, release),
    )

    holder.start()
    try:
        assert entered.wait(PROCESS_START_TIMEOUT_SECONDS)
        results = [
            write_note("note.md", "replacement"),
            enrich_note_frontmatter_required("note.md"),
            ensure_note_id("note.md", validate_schema=False),
            move_note("note.md", "moved.md"),
            delete_note("note.md"),
        ]
        assert all(result["success"] is False for result in results)
        assert all(result["error_code"] == "write_lock_timeout" for result in results)
        assert note.read_text(encoding="utf-8") == "original"
        assert not (tmp_path / "moved.md").exists()
    finally:
        release.set()
        _join_processes([holder])

    assert holder.exitcode == 0


def test_lock_directory_symlink_cannot_escape_vault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud.locking import advisory_path_lock

    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    (vault / ".vault-search-locks").symlink_to(
        outside,
        target_is_directory=True,
    )
    _configure_vault(monkeypatch, vault)

    with pytest.raises(ValueError, match="fora do vault"):
        with advisory_path_lock(vault / "note.md"):
            pytest.fail("lock externo não pode ser adquirido")

    assert list(outside.iterdir()) == []


def test_trash_nested_symlink_cannot_escape_vault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vault_search.crud.delete import delete_note

    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    note = vault / "folder" / "note.md"
    note.parent.mkdir()
    note.write_text("private content", encoding="utf-8")
    trash = vault / ".trash"
    trash.mkdir()
    (trash / "folder").symlink_to(outside, target_is_directory=True)
    _configure_vault(monkeypatch, vault)
    monkeypatch.setattr("vault_search.crud.delete.VAULT_PATH", vault)

    with pytest.raises(ValueError, match="fora do vault"):
        delete_note("folder/note.md")

    assert note.read_text(encoding="utf-8") == "private content"
    assert list(outside.iterdir()) == []
