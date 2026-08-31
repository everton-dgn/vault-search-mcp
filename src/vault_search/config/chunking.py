"""
Configurações de chunking de documentos.
"""

from vault_search.config.loader import get_config

_config = get_config().chunking

# Tamanho máximo de cada chunk em caracteres
CHUNK_SIZE = _config.size

# Sobreposição entre chunks em caracteres para manter contexto
CHUNK_OVERLAP = _config.overlap

# Headers markdown usados para split estrutural
MARKDOWN_HEADER_LEVELS = _config.header_levels

# Separadores hierárquicos para chunking (ordem de preferência)
CHUNK_SEPARATORS = list(_config.separators)
