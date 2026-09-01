"""
Technical limits for local operation.

Keeps compatibility constants and runtime safety limits.
"""

from enum import StrEnum

from vault_search.config.loader import get_config

_config = get_config().security


class RiskLevel(StrEnum):
    """Legacy compatibility alias."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Input limits retained for runtime safety
MAX_QUERY_LENGTH = _config.max_query_length
MAX_CONTENT_SIZE = _config.max_content_size
MAX_PATH_LENGTH = _config.max_path_length
MAX_FRONTMATTER_KEYS = _config.max_frontmatter_keys

# Mathematical helper
NORM_EPSILON = 1e-9

# Message retained for compatibility
INDEX_NOT_FOUND_ERROR = "Index not found. Run 'reindex_vault()' first."
