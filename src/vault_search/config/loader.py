"""
Carregador de configuração YAML com validação Pydantic.

Ordem de precedência:
1. Variável de ambiente VAULT_SEARCH_CONFIG (path para arquivo YAML)
2. config.yaml ou config.yml no diretório de trabalho
3. config.yaml ou config.yml na raiz da instalação, se diferente
4. Defaults definidos em settings.py

Uso:
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

# Raiz do projeto (4 níveis acima deste arquivo)
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# Variável de ambiente para override
_CONFIG_ENV_VAR = "VAULT_SEARCH_CONFIG"


def _default_config_paths() -> tuple[Path, ...]:
    """Retorna locais portáveis, removendo duplicatas sem depender do SO."""
    candidates = (
        Path.cwd() / "config.yaml",
        Path.cwd() / "config.yml",
        _PROJECT_ROOT / "config.yaml",
        _PROJECT_ROOT / "config.yml",
    )
    return tuple(dict.fromkeys(candidates))


def _find_config_file() -> Path | None:
    """
    Encontra arquivo de configuração.

    Ordem de precedência:
    1. Variável de ambiente VAULT_SEARCH_CONFIG
    2. config.yaml no diretório de trabalho
    3. config.yml no diretório de trabalho
    4. config.yaml na raiz da instalação, se diferente
    5. config.yml na raiz da instalação, se diferente

    Retorna:
        Path do arquivo se encontrado, None caso contrário.
    """
    # Check variável de ambiente
    env_path = os.environ.get(_CONFIG_ENV_VAR)
    if env_path:
        path = Path(env_path).expanduser()
        if path.exists():
            return path
        raise FileNotFoundError(f"Arquivo definido por {_CONFIG_ENV_VAR} não foi encontrado")

    # Check paths padrão
    for path in _default_config_paths():
        if path.exists():
            return path

    return None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Merge recursivo de dicionários.

    Valores do override substituem valores do base.
    Para dicts aninhados, merge é recursivo.

    Parâmetros:
        base: Dicionário base
        override: Dicionário com valores para sobrescrever

    Retorna:
        Dicionário merged.
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
    Carrega configuração de um arquivo YAML.

    Parâmetros:
        path: Caminho para o arquivo YAML

    Retorna:
        Configuração validada.

    Raises:
        FileNotFoundError: Arquivo não existe
        yaml.YAMLError: YAML inválido
        pydantic.ValidationError: Valores inválidos
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
    Carrega configuração de um dicionário.

    Útil para testes ou configuração programática.

    Parâmetros:
        data: Dicionário com configuração

    Retorna:
        Configuração validada.
    """
    config = VaultSearchConfig.model_validate(data)
    return config.resolve_paths(base_dir or Path.cwd())


@lru_cache(maxsize=1)
def get_config() -> VaultSearchConfig:
    """
    Obtém configuração com cache.

    Primeira chamada:
    - Procura arquivo de configuração
    - Se encontrado, carrega e valida
    - Se não encontrado, usa defaults

    Chamadas subsequentes retornam a mesma instância (cached).

    Para recarregar, use reload_config().

    Retorna:
        Configuração validada e cacheada.
    """
    config_path = _find_config_file()

    if config_path:
        return load_config_from_file(config_path)

    # Defaults
    config = VaultSearchConfig()
    return config.resolve_paths(Path.cwd())


def reload_config() -> VaultSearchConfig:
    """
    Recarrega configuração limpando o cache.

    Útil após modificar o arquivo config.yaml.

    Retorna:
        Nova configuração carregada.
    """
    get_config.cache_clear()
    return get_config()


def get_project_root() -> Path:
    """
    Retorna a raiz do projeto.

    Retorna:
        Path absoluto da raiz do projeto.
    """
    return _PROJECT_ROOT
