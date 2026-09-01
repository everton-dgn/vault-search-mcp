"""
Document chunking settings.
"""

from vault_search.config.loader import get_config

_config = get_config().chunking

# Maximum size of each chunk in characters
CHUNK_SIZE = _config.size

# Overlap between chunks in characters to preserve context
CHUNK_OVERLAP = _config.overlap

# Markdown headings used for structural splitting
MARKDOWN_HEADER_LEVELS = _config.header_levels

# Hierarchical chunking separators in preference order
CHUNK_SEPARATORS = list(_config.separators)
