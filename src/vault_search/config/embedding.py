"""Configurações efetivas de modelos de embedding e reranking."""

from typing import Any

from vault_search.config.loader import get_config

_config = get_config().embedding

# Modelo de embedding configurado pelo operador.
EMBEDDING_MODEL = _config.model

# Modelo de reranking configurado pelo operador.
RERANKER_MODEL = _config.reranker_model

# Parâmetros de inferência
MODEL_USE_FP16 = _config.use_fp16
MODEL_DEVICE = _config.device
EMBEDDING_BATCH_SIZE = _config.batch_size
EMBEDDING_QUERY_MAX_LENGTH = _config.query_max_length
EMBEDDING_CORPUS_MAX_LENGTH = _config.corpus_max_length
EMBEDDING_DIMENSION = _config.dimension
RERANKER_NORMALIZE = _config.reranker_normalize
RERANKER_BATCH_SIZE = 16
RERANKER_MAX_LENGTH = 256
RERANKER_QUERY_MAX_LENGTH = 96

# Tempo (segundos) sem uso antes de descarregar modelos da memória
MODEL_IDLE_TIMEOUT = _config.idle_timeout


def resolve_model_device(
    configured: str | None = None,
    *,
    torch_module: Any | None = None,
) -> str:
    """Resolve ``auto`` com prioridade CUDA, MPS e CPU."""
    requested = configured or MODEL_DEVICE
    if requested != "auto":
        return requested

    if torch_module is None:
        try:
            import torch as torch_module
        except ImportError:
            return "cpu"

    assert torch_module is not None
    if torch_module.cuda.is_available():
        return "cuda"

    mps = getattr(getattr(torch_module, "backends", None), "mps", None)
    if mps is not None and mps.is_available():
        return "mps"

    return "cpu"


def resolve_fp16(device: str, configured: bool | None = None) -> bool:
    """Ativa FP16 automaticamente apenas em aceleradores compatíveis."""
    requested = MODEL_USE_FP16 if configured is None else configured
    if requested is None:
        return device in {"cuda", "mps"}
    return bool(requested and device != "cpu")
