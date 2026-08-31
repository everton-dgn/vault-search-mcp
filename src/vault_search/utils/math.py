"""
Utilitários matemáticos para embeddings e scores.
"""

import numpy as np

from vault_search.config.search import SCORE_PRECISION
from vault_search.config.security import NORM_EPSILON


def normalize_embeddings(
    embeddings: np.ndarray,
    epsilon: float = NORM_EPSILON,
) -> np.ndarray:
    """
    Normaliza embeddings para L2 norm = 1 (unit vectors).

    Parâmetros:
        embeddings: array 2D (N, D) com embeddings
        epsilon: valor mínimo para evitar divisão por zero

    Retorna:
        Array normalizado (N, D) onde cada vetor tem norm = 1.
    """
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    # Evitar divisão por zero
    norms = np.where(norms > epsilon, norms, 1.0)
    return embeddings / norms


def distance_to_score(distance: float) -> float:
    """
    Converte distância vetorial em score de similaridade.

    Usa fórmula: score = 1 / (1 + distance)
    - distance=0 → score=1.0 (idêntico)
    - distance=1 → score=0.5
    - distance→∞ → score→0

    Parâmetros:
        distance: distância vetorial (>=0)

    Retorna:
        Score de similaridade 0-1, arredondado.
    """
    return round(1 / (1 + distance), SCORE_PRECISION)
