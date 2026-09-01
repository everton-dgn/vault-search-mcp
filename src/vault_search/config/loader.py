"""
YAML configuration loader with Pydantic validation.

Precedence order:
1. ``VAULT_SEARCH_CONFIG`` environment variable, pointing to a YAML file
2. ``config.yaml`` or ``config.yml`` in the working directory
3. ``config.yaml`` or ``config.yml`` in the installation root, when different
4. Defaults defined in settings.py

Usage:
    from vault_search.config.loader import get_config

    config = get_config()
    print(config.search.top_k)
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from vault_search.config.settings import VaultSearchConfig

logger = logging.getLogger(__name__)

# Project root, four levels above this file
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# Environment variable override
_CONFIG_ENV_VAR = "VAULT_SEARCH_CONFIG"


def _default_config_paths() -> tuple[Path, ...]:
    """Return portable locations, removing duplicates without OS-specific behavior."""
    candidates = (
        Path.cwd() / "config.yaml",
        Path.cwd() / "config.yml",
        _PROJECT_ROOT / "config.yaml",
        _PROJECT_ROOT / "config.yml",
    )
    return tuple(dict.fromkeys(candidates))


def _find_config_file() -> Path | None:
    """
    Find the configuration file.

    Precedence order:
    1. ``VAULT_SEARCH_CONFIG`` environment variable
    2. ``config.yaml`` in the working directory
    3. ``config.yml`` in the working directory
    4. ``config.yaml`` in the installation root, when different
    5. ``config.yml`` in the installation root, when different

    Returns:
        The file path when found, otherwise ``None``.
    """
    # Check the environment variable.
    env_path = os.environ.get(_CONFIG_ENV_VAR)
    if env_path:
        path = Path(env_path).expanduser()
        if path.exists():
            return path
        raise FileNotFoundError(f"File defined by {_CONFIG_ENV_VAR} was not found")

    # Check default paths.
    for path in _default_config_paths():
        if path.exists():
            return path

    return None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively merge dictionaries.

    Override values replace base values.
    Nested dictionaries are merged recursively.

    Parameters:
        base: Base dictionary.
        override: Dictionary containing replacement values.

    Returns:
        The merged dictionary.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config_from_file(path: Path) -> VaultSearchConfig:
    """
    Load configuration from a YAML file.

    Parameters:
        path: Path to the YAML file.

    Returns:
        The validated configuration.

    Raises:
        FileNotFoundError: The file does not exist.
        yaml.YAMLError: The YAML is invalid.
        pydantic.ValidationError: Configuration values are invalid.
    """
    path = path.expanduser()
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    logger.info("configuration_loaded")
    config = VaultSearchConfig.model_validate(data)
    return config.resolve_paths(path.parent)


def load_config_from_dict(
    data: dict[str, Any],
    base_dir: Path | None = None,
) -> VaultSearchConfig:
    """
    Load configuration from a dictionary.

    Useful for tests or programmatic configuration.

    Parameters:
        data: Configuration dictionary.

    Returns:
        The validated configuration.
    """
    config = VaultSearchConfig.model_validate(data)
    return config.resolve_paths(base_dir or Path.cwd())


@lru_cache(maxsize=1)
def get_config() -> VaultSearchConfig:
    """
    Get the cached configuration.

    First call:
    - Look for a configuration file.
    - Load and validate it when found.
    - Use defaults when no file exists.

    Subsequent calls return the same cached instance.

    Use ``reload_config()`` to reload it.

    Returns:
        The validated, cached configuration.
    """
    config_path = _find_config_file()

    if config_path:
        return load_config_from_file(config_path)

    # Defaults
    config = VaultSearchConfig()
    return config.resolve_paths(Path.cwd())


def reload_config() -> VaultSearchConfig:
    """
    Reload configuration after clearing the cache.

    Useful after modifying ``config.yaml``.

    Returns:
        The newly loaded configuration.
    """
    get_config.cache_clear()
    return get_config()


def get_project_root() -> Path:
    """
    Return the project root.

    Returns:
        Absolute path to the project root.
    """
    return _PROJECT_ROOT
