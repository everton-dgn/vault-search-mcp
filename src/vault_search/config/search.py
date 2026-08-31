"""
Configurações de busca e indexação.

NOTA: Este módulo existe para compatibilidade com imports legados.
Para nova config centralizada: from vault_search.config import get_config
"""

from typing import TypedDict

from vault_search.config.loader import get_config
from vault_search.config.settings import VectorIndexConfig


class VectorIndexRuntimeConfig(TypedDict):
    """Parâmetros validados usados para criar o índice ANN."""

    index_type: str
    num_partitions: int
    num_sub_vectors: int
    distance_type: str


_config = get_config()

# Candidatos mínimos na busca vetorial (antes do reranking)
SEARCH_CANDIDATES = _config.search.candidates
SEARCH_CANDIDATES_MAX = _config.search.candidates_max
SEARCH_CANDIDATES_MULTIPLIER = _config.search.candidates_multiplier
# Limite de candidatos enviados ao cross-encoder.
# Mantém latência previsível nas buscas comuns (top_k=10).
RERANK_CANDIDATES_MAX = 10
RERANK_CANDIDATES_MULTIPLIER = 2
SEARCH_TOP_K = _config.search.top_k
SEARCH_TOP_K_MIN = _config.search.top_k_min
SEARCH_TOP_K_MAX = _config.search.top_k_max
LIST_NOTES_DEFAULT_LIMIT = _config.search.list_notes_default_limit
LIST_NOTES_MAX_LIMIT = _config.search.list_notes_max_limit
SCORE_PRECISION = _config.search.score_precision

# Colunas retornadas nas buscas (sem vetor para economizar memória)
SEARCH_COLUMNS = [
    "note_path",
    "note_title",
    "folder",
    "headers",
    "tags",
    "modified_at",
    "text",
    "_distance",
]
FTS_SEARCH_COLUMNS = [column for column in SEARCH_COLUMNS if column != "_distance"] + ["_score"]

# Constante de Reciprocal Rank Fusion. O valor 60 é o default consolidado na
# literatura de RRF e evita que uma única origem domine o pool de reranking.
HYBRID_RRF_K = 60

# === Indexing (delegado para config) ===
REINDEX_BATCH_SIZE = _config.indexing.batch_size
REINDEX_WORKERS = _config.indexing.workers
MAX_CHUNKS_PER_NOTE = _config.indexing.max_chunks_per_note
INDEXABLE_EXTENSIONS = set(_config.indexing.extensions)
READABLE_TEXT_EXTENSIONS = {".md"}

# === FTS ===
FTS_LANGUAGE = _config.fts.language

# === Ignored folders ===
IGNORED_FOLDERS = set(_config.indexing.ignored_folders)

# === Navigation ===
FOLDER_TREE_MAX_DEPTH = _config.navigation.folder_tree_max_depth
FOLDER_TREE_MAX_DEPTH_LIMIT = _config.navigation.folder_tree_max_depth_limit

# === Prewarm ===
PREWARM_ENABLED = _config.prewarm.enabled
PREWARM_MAX_RAM_PERCENT = _config.prewarm.max_ram_percent
PREWARM_MIN_AVAILABLE_RAM = _config.prewarm.min_available_ram
PREWARM_BYTES_PER_CHUNK = _config.prewarm.bytes_per_chunk

# === Vector Index ===
# Aliases legados refletem a configuração disponível no primeiro import.
VECTOR_INDEX_MIN_CHUNKS = _config.vector_index.min_chunks
VECTOR_INDEX_AUTO_CREATE = _config.vector_index.auto_create
VECTOR_INDEX_TYPE = _config.vector_index.index_type
VECTOR_INDEX_NUM_SUB_VECTORS = _config.vector_index.num_sub_vectors
VECTOR_INDEX_DISTANCE_TYPE = _config.vector_index.distance_type


def get_optimal_batch_size() -> int:
    """
    Calcula batch size otimizado baseado na RAM disponível.

    Retorna:
        Batch size: 16, 32 ou 64 dependendo da RAM.
    """
    try:
        import psutil

        available_ram = psutil.virtual_memory().available

        if available_ram > 16_000_000_000:  # >16GB
            return 64
        elif available_ram > 8_000_000_000:  # >8GB
            return 32
        else:
            return 16
    except ImportError:
        return 32


def get_vector_index_settings() -> VectorIndexConfig:
    """Retorna a configuração ANN efetiva, incluindo overrides do YAML."""
    return get_config().vector_index


def get_vector_index_distance_type() -> str:
    """Retorna a métrica ANN efetiva para manter indexação e query alinhadas."""
    return get_vector_index_settings().distance_type


def get_vector_index_config(total_chunks: int) -> VectorIndexRuntimeConfig | None:
    """
    Retorna configuração de índice vetorial apropriada para o tamanho do dataset.

    Calcula num_partitions dinamicamente baseado no número de chunks.
    Heurística oficial LanceDB: num_partitions = num_rows / 8192

    Parâmetros:
        total_chunks: número total de chunks no índice

    Retorna:
        Dict com configuração do índice, ou None se não deve criar índice.
    """
    settings = get_vector_index_settings()

    if not settings.auto_create:
        return None

    if total_chunks < settings.min_chunks:
        return None

    # Calcular partições dinamicamente (heurística oficial LanceDB)
    # Mínimo 1, máximo 256 para evitar overhead excessivo
    num_partitions = max(1, min(256, total_chunks // 8192))

    return {
        "index_type": settings.index_type,
        "num_partitions": num_partitions,
        "num_sub_vectors": settings.num_sub_vectors,
        "distance_type": settings.distance_type,
    }
