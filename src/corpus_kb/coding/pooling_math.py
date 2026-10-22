"""Vector math for similarity pooling (pure move out of ``pooling.py``).

``_as_array`` and ``cosine_similarity`` are byte-identical;
``pooling`` re-imports ``cosine_similarity`` so its import surface (and
``tests/test_pooling.py``) keeps working.
"""

from __future__ import annotations

import json

import numpy as np


def _as_array(vector: np.ndarray | list[float] | str) -> np.ndarray:
    """Coerce a vector to float32 ndarray.

    asyncpg has no pgvector codec registered here, so a `vector` column comes
    back as its text literal ("[0.1,0.2,...]"); parse that shape too.
    """
    if isinstance(vector, str):
        return np.array(json.loads(vector), dtype=np.float32)
    if isinstance(vector, np.ndarray):
        return vector.astype(np.float32, copy=False)
    return np.array(vector, dtype=np.float32)


def cosine_similarity(
    v1: np.ndarray | list[float] | str, v2: np.ndarray | list[float] | str
) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        v1: First vector (numpy array, list, or pgvector text literal)
        v2: Second vector (numpy array, list, or pgvector text literal)

    Returns:
        Cosine similarity value between -1.0 and 1.0 (higher is more similar)
    """
    v1 = _as_array(v1)
    v2 = _as_array(v2)

    dot = np.dot(v1, v2)
    norm = np.linalg.norm(v1) * np.linalg.norm(v2)
    if norm < 1e-9:
        return 0.0
    return float(np.clip(dot / norm, -1.0, 1.0))
