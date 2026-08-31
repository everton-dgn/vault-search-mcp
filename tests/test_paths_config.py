"""
Tests for resolution of paths via config/env.
"""

import importlib


def test_vault_path_uses_env_override(tmp_path, monkeypatch):
    """VAULT_SEARCH_VAULT_PATH must override the vault path."""
    real_vault = tmp_path / "real_vault"
    real_vault.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("VAULT_SEARCH_VAULT_PATH", str(real_vault))

    import vault_search.config.paths as paths_module

    reloaded = importlib.reload(paths_module)

    assert reloaded.VAULT_PATH == real_vault.resolve(strict=False)

    # Avoid leak of state of the module for other tests.
    monkeypatch.delenv("VAULT_SEARCH_VAULT_PATH", raising=False)
    importlib.reload(paths_module)
