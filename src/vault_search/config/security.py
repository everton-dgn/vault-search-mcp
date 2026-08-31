"""
Configuração de limites técnicos para operação local.

Mantém constantes de compatibilidade e limites de robustez do runtime.
"""

from enum import StrEnum

from vault_search.config.loader import get_config

_config = get_config().security


class RiskLevel(StrEnum):
    """Compatibilidade legada."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Limites de input (mantidos por robustez técnica)
MAX_QUERY_LENGTH = _config.max_query_length
MAX_CONTENT_SIZE = _config.max_content_size
MAX_PATH_LENGTH = _config.max_path_length
MAX_FRONTMATTER_KEYS = _config.max_frontmatter_keys

# Utilitário matemático
NORM_EPSILON = 1e-9

# Mensagem mantida por compatibilidade
INDEX_NOT_FOUND_ERROR = "Índice não encontrado. Execute 'reindex_vault()' primeiro."
