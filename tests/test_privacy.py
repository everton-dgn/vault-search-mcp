"""Privacy, path-containment, and atomic-write regressions."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vault_search.server.errors import public_error
from vault_search.utils.logging import PrivacyFilter, configure_logging, get_logger
from vault_search.utils.privacy import redact_mapping, redact_text


def test_redact_text_removes_local_paths() -> None:
    message = "failed in /Users/local/private/vault/note.md and C:\\Users\\local\\note.md"  # publication-check: synthetic-fixture

    redacted = redact_text(message)

    assert "/Users/local" not in redacted  # publication-check: synthetic-fixture
    assert "C:\\Users\\local" not in redacted  # publication-check: synthetic-fixture
    assert "[REDACTED_PATH]" in redacted


def test_redact_mapping_is_recursive() -> None:
    payload = {
        "query": "content private",
        "nested": {
            "token": "secret",
            "message": "/home/local/vault/note.md",  # publication-check: synthetic-fixture
        },
    }

    redacted = redact_mapping(payload)

    assert redacted["query"] == "[REDACTED]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    home_prefix = "/home/local"  # publication-check: synthetic-fixture
    assert home_prefix not in redacted["nested"]["message"]


def test_redaction_handles_path_objects_and_spaces() -> None:
    private_path = "/Users/local/My Vault/private note.md"  # publication-check: synthetic-fixture
    payload = {
        "path_object": Path(private_path),
        "message": "failed in /Users/local/My Vault/private note.md",  # publication-check: synthetic-fixture
    }

    redacted = redact_mapping(payload)

    assert redacted["path_object"] == "[REDACTED_PATH]"
    assert "/Users/local" not in redacted["message"]  # publication-check: synthetic-fixture
    assert "My Vault" not in redacted["message"]


@pytest.mark.parametrize(
    "message",
    [
        "/Users/local/My Vault",  # publication-check: synthetic-fixture
        "/Users/local/My Vault/README",  # publication-check: synthetic-fixture
        'failed at "/Users/local/My Vault/note.md"',  # publication-check: synthetic-fixture
        "C:\\Users\\local\\My Vault",  # publication-check: synthetic-fixture
        "C:\\Users\\local\\My Vault\\README",  # publication-check: synthetic-fixture
        'failed at "C:\\Users\\local\\My Vault\\note.md"',  # publication-check: synthetic-fixture
    ],
)
def test_redact_text_handles_paths_without_extensions_and_quoted(message: str) -> None:
    redacted = redact_text(message)

    assert "Users" not in redacted
    assert "My Vault" not in redacted
    assert "[REDACTED_PATH]" in redacted


@pytest.mark.parametrize("json_output", [True, False])
def test_structlog_keeps_privacy_processors_and_stderr(
    capfd,
    json_output: bool,
) -> None:
    configure_logging(json_output=json_output, level="INFO")
    logger = get_logger(f"privacy-{json_output}")

    try:
        raise OSError(
            "failed in /Users/local/My Vault/private note.md"  # publication-check: synthetic-fixture
        )
    except OSError:
        logger.exception(
            "provider_failed",
            query="content private",
            token="secret-token",  # publication-check: synthetic-fixture
            note_body="body private",
        )

    captured = capfd.readouterr()
    assert captured.out == ""
    assert "content private" not in captured.err
    assert "secret-token" not in captured.err  # publication-check: synthetic-fixture
    assert "body private" not in captured.err
    assert "/Users/local" not in captured.err  # publication-check: synthetic-fixture
    assert "private note.md" not in captured.err
    assert "Traceback" not in captured.err


def test_public_error_does_not_expose_exception_details() -> None:
    error = OSError(
        "failed at /Users/local/private/vault/note.md"  # publication-check: synthetic-fixture
    )

    response = public_error(logging.getLogger("test"), "read_note", error)

    assert "/Users/local" not in response  # publication-check: synthetic-fixture
    assert "note.md" not in response
    assert "OSError" not in response
    assert "Reference:" in response


def test_stdlib_log_filter_removes_paths_and_tracebacks() -> None:
    try:
        raise OSError(
            "failed at /Users/local/private/vault/note.md"  # publication-check: synthetic-fixture
        )
    except OSError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="/Users/local/project/module.py",  # publication-check: synthetic-fixture
        lineno=10,
        msg="read failed: %s",
        args=(
            "/Users/local/private/vault/note.md",  # publication-check: synthetic-fixture
        ),
        exc_info=exc_info,
    )

    assert PrivacyFilter().filter(record) is True
    assert "/Users/local" not in record.getMessage()  # publication-check: synthetic-fixture
    assert record.exc_info is None
    assert record.stack_info is None


def test_resolve_path_rejects_symlink_escape(tmp_path: Path, monkeypatch) -> None:
    from vault_search.crud import validation

    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    (vault / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(validation, "VAULT_PATH", vault)

    with pytest.raises(ValueError, match="outside the vault"):
        validation.resolve_path("escape/private.md")


def test_list_notes_fallback_rejects_symlink_folder(tmp_path: Path, monkeypatch) -> None:
    from vault_search.crud import read, validation

    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    (outside / "private.md").write_text("private", encoding="utf-8")
    (vault / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(read, "VAULT_PATH", vault)
    monkeypatch.setattr(validation, "VAULT_PATH", vault)
    monkeypatch.setattr(read, "USE_CATALOG", False)

    with pytest.raises(ValueError, match="outside the vault"):
        read.list_notes(folder="escape")


def test_delete_rejects_external_trash_symlink(tmp_path: Path, monkeypatch) -> None:
    from vault_search.crud import delete, validation

    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    note = vault / "note.md"
    note.write_text("private content", encoding="utf-8")
    (vault / ".trash").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(validation, "VAULT_PATH", vault)
    monkeypatch.setattr(delete, "VAULT_PATH", vault)

    with pytest.raises(ValueError, match="outside the vault"):
        delete.delete_note("note.md")

    assert note.exists()
    assert list(outside.iterdir()) == []


def test_safe_write_text_is_atomic_on_replace_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from vault_search.crud import validation

    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("original", encoding="utf-8")
    monkeypatch.setattr(validation, "VAULT_PATH", vault)

    real_replace = os.replace
    call_count = 0

    def fail_first_replace(source, destination):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("simulated failure")
        return real_replace(source, destination)

    monkeypatch.setattr(validation.os, "replace", fail_first_replace)

    error = validation.safe_write_text(note, "new content", "note.md")

    assert error is not None
    assert note.read_text(encoding="utf-8") == "original"
    recovery_files = list((vault / ".trash" / "write-failures").iterdir())
    assert len(recovery_files) == 1
    assert recovery_files[0].read_text(encoding="utf-8") == "new content"


def test_safe_write_text_replaces_content_without_temp_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from vault_search.crud import validation

    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("original", encoding="utf-8")
    monkeypatch.setattr(validation, "VAULT_PATH", vault)

    error = validation.safe_write_text(note, "new content", "note.md")

    assert error is None
    assert note.read_text(encoding="utf-8") == "new content"
    assert list(vault.glob(".*.tmp")) == []


def test_enrichment_prompt_is_transported_only_by_stdin() -> None:
    from vault_search.frontmatter.enrichment import _resolve_command

    command, stdin_data = _resolve_command(
        ["provider-cli", "--model", "{model}"],
        model="model-name",
        prompt="private note content",
    )

    assert command == ["provider-cli", "--model", "model-name"]
    assert all("private note content" not in argument for argument in command)
    assert stdin_data == "private note content"


def test_enrichment_timeout_does_not_expose_command_or_prompt() -> None:
    from vault_search.frontmatter.enrichment import (
        FrontmatterEnrichmentError,
        _run_cli_command,
    )

    timeout = subprocess.TimeoutExpired(
        cmd=["provider-cli", "private note content"],
        timeout=1,
    )
    with patch("vault_search.frontmatter.enrichment.subprocess.run", side_effect=timeout):
        with pytest.raises(FrontmatterEnrichmentError) as captured:
            _run_cli_command(
                ["provider-cli", "--model", "model-name"],
                "private note content",
                1,
            )

    message = str(captured.value)
    assert "private note content" not in message
    assert "provider-cli" not in message


def test_ai_enrichment_requires_explicit_external_consent() -> None:
    from vault_search.crud.write import is_ai_enrichment_enabled

    ai = SimpleNamespace(
        enabled=True,
        allow_external_processing=False,
        provider="provider-name",
        command=["provider-cli"],
    )
    config = SimpleNamespace(frontmatter=SimpleNamespace(enabled=True, ai=ai))

    with patch("vault_search.crud.write.get_config", return_value=config):
        assert is_ai_enrichment_enabled() is False

    ai.allow_external_processing = True
    with patch("vault_search.crud.write.get_config", return_value=config):
        assert is_ai_enrichment_enabled() is True
