"""Regressões para fronteiras públicas de startup e middleware."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastmcp.server.middleware import MiddlewareContext
from mcp import McpError

from vault_search.server.middleware import SafeErrorMiddleware, SafeTimingMiddleware


def test_entrypoint_hides_non_numeric_system_exit(monkeypatch, capsys):
    from vault_search import cli

    private_text = "/Users/local/My Vault/private note.md"  # publication-check: synthetic-fixture

    def fail():
        raise SystemExit(private_text)

    monkeypatch.setattr(cli.importlib, "import_module", lambda _name: SimpleNamespace(main=fail))

    assert cli._run("synthetic.module") == 1
    captured = capsys.readouterr()
    assert "startup failed: SystemExit" in captured.err
    assert private_text not in captured.err


@pytest.mark.parametrize("entrypoint", ["mcp_main", "daemon_main"])
def test_entrypoint_hides_invalid_config_traceback(tmp_path: Path, entrypoint: str):
    config_path = tmp_path / "private config.yaml"
    secret_value = "synthetic-private-value"  # publication-check: synthetic-fixture
    config_path.write_text(f"daemon:\n  timeout: {secret_value}\n", encoding="utf-8")
    env = os.environ.copy()
    env["VAULT_SEARCH_CONFIG"] = str(config_path)
    command = f"from vault_search.cli import {entrypoint}; raise SystemExit({entrypoint}())"

    result = subprocess.run(
        [sys.executable, "-c", command],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=20,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "startup failed" in result.stderr
    assert "Traceback" not in result.stderr
    assert str(config_path) not in result.stderr
    assert secret_value not in result.stderr


def test_module_entrypoint_uses_same_startup_boundary(tmp_path: Path):
    config_path = tmp_path / "private config.yaml"
    secret_value = "synthetic-private-value"  # publication-check: synthetic-fixture
    config_path.write_text(f"daemon:\n  timeout: {secret_value}\n", encoding="utf-8")
    env = os.environ.copy()
    env["VAULT_SEARCH_CONFIG"] = str(config_path)

    result = subprocess.run(
        [sys.executable, "-m", "vault_search"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=20,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "startup failed" in result.stderr
    assert "Traceback" not in result.stderr
    assert str(config_path) not in result.stderr
    assert secret_value not in result.stderr


def test_config_check_confirms_valid_config_without_printing_resolved_paths(tmp_path: Path):
    config_path = tmp_path / "runtime.yaml"
    config_path.write_text(
        "paths:\n  vault_path: synthetic-vault\n  data_dir: synthetic-index\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["VAULT_SEARCH_CONFIG"] = str(config_path)

    result = subprocess.run(
        [sys.executable, "-m", "vault_search", "config"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0
    assert result.stdout == "vault-search configuration: ok\n"
    assert result.stderr == ""
    assert str(tmp_path) not in result.stdout


def test_config_check_sanitizes_validation_failure(tmp_path: Path):
    config_path = tmp_path / "private config.yaml"
    secret_value = "synthetic-private-value"  # publication-check: synthetic-fixture
    config_path.write_text(f"search:\n  top_k: {secret_value}\n", encoding="utf-8")
    env = os.environ.copy()
    env["VAULT_SEARCH_CONFIG"] = str(config_path)

    result = subprocess.run(
        [sys.executable, "-m", "vault_search", "config"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=20,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "configuration invalid" in result.stderr
    assert "Traceback" not in result.stderr
    assert str(config_path) not in result.stderr
    assert secret_value not in result.stderr


def test_safe_error_middleware_never_serializes_exception_text():
    logger = MagicMock()
    middleware = SafeErrorMiddleware(logger=logger)
    context = MiddlewareContext(message=object(), method="tools/call")
    private_text = "/Users/local/My Vault/private note.md"  # publication-check: synthetic-fixture

    async def fail(_context):
        raise OSError(private_text)

    with pytest.raises(McpError) as captured:
        asyncio.run(middleware.on_message(context, fail))

    assert private_text not in str(captured.value)
    assert private_text not in repr(logger.error.call_args)
    assert captured.value.error.code == -32603
    assert "reference=" in captured.value.error.message


def test_safe_timing_middleware_never_logs_exception_text():
    logger = MagicMock()
    middleware = SafeTimingMiddleware(logger=logger)
    context = MiddlewareContext(message=object(), method="tools/call")
    private_text = "/Users/local/My Vault/private note.md"  # publication-check: synthetic-fixture

    async def fail(_context):
        raise OSError(private_text)

    with pytest.raises(OSError):
        asyncio.run(middleware.on_request(context, fail))

    assert private_text not in repr(logger.info.call_args)
