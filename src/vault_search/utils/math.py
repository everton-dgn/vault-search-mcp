"""
Mathematical utilities for embeddings and scores.
"""

import numpy as np

from vault_search.config.search import SCORE_PRECISION
from vault_search.config.security import NORM_EPSILON


def normalize_embeddings(
    embeddings: np.ndarray,
    epsilon: float = NORM_EPSILON,
) -> np.ndarray:
    """
    Normalize embeddings to an L2 norm of 1.

    Parameters:
        embeddings: Two-dimensional ``(N, D)`` embedding array.
        epsilon: Minimum value used to avoid division by zero.

    Returns:
        A normalized ``(N, D)`` array where each vector has a norm of 1.
    """
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    # Avoid division by zero.
    norms = np.where(norms > epsilon, norms, 1.0)
    return embeddings / norms


def distance_to_score(distance: float) -> float:
    """
    Convert vector distance into a similarity score.

    Use ``score = 1 / (1 + distance)``:
    - distance=0 produces score=1.0 for identical vectors
    - distance=1 → score=0.5
    - distance→∞ → score→0

    Parameters:
        distance: Vector distance greater than or equal to zero.

    Returns:
        A rounded similarity score between 0 and 1.
    """
    return round(1 / (1 + distance), SCORE_PRECISION)
