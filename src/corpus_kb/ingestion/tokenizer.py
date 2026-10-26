"""Token-budget splitting seam: semchunk when configured, chars/4 otherwise.

The sanctioned budget approximation (``ingestion.base.TOKEN_BUDGET_CHARS``)
counts tokens as chars/4. When ``ingestion.tokenizer: semchunk`` is set AND
the optional ``semchunk`` package is installed (the lazy ``[semchunk]``
extra), oversized text is split by semchunk instead: chunks land on
sentence or word boundaries and come back with native char offsets, so the
offset invariant ``text[start:end] == chunk text`` holds by construction.
The token counter stays the sanctioned chars/4 basis (Ollama exposes no
tokenizer); semchunk supplies the boundaries, not the count. When the
package is missing the old slice behavior runs and the strategy records a
warning — the degradation is honest, never silent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from ..errors import ConfigError

logger = logging.getLogger(__name__)

_TOKENIZER_KEY = "tokenizer"
_SUPPORTED = ("semchunk",)


def tokenizer_name(config: Mapping[str, Any] | None) -> str | None:
    """The configured tokenizer, or None for the default chars/4 heuristic.

    An unknown value is a config error: the run stops before any write.
    """
    if not config:
        return None
    ingestion = config.get("ingestion")
    if not isinstance(ingestion, Mapping):
        return None
    raw = ingestion.get(_TOKENIZER_KEY)
    if raw is None:
        return None
    if raw in _SUPPORTED:
        return str(raw)
    raise ConfigError(
        f"Unknown ingestion.tokenizer value '{raw}'. Supported values: "
        + ", ".join(_SUPPORTED)
        + ".",
        remediation="Set ingestion.tokenizer to semchunk, or remove the key.",
    )


def _load_semchunk() -> Any:
    """The lazy import seam (tests fake this; the extra stays optional).

    Dynamic import on purpose: the extra is optional and CI never installs
    it, so the import edge must stay invisible to static resolution (the
    latechunk/topics pattern).
    """
    import importlib

    return importlib.import_module("semchunk")


def _default_token_counter(text: str) -> int:
    """The sanctioned approximation: one token per four characters."""
    return (len(text) + 3) // 4


def hard_split_text(
    text: str,
    *,
    budget_chars: int,
    config: Mapping[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> list[str]:
    """Split ``text`` into pieces that each fit the char budget.

    Default (no tokenizer configured): the historical fixed char slices,
    byte for byte. With ``ingestion.tokenizer: semchunk`` and the package
    present: semchunk picks the boundaries and returns native offsets, and
    the chunk size in tokens maps back to the same char budget, so every
    piece still passes the char-budget conformance check. Without the
    package: the historical slices plus one ``warnings`` entry.
    """
    if not text:
        return []

    def char_slices() -> list[str]:
        return [
            text[offset : offset + budget_chars] for offset in range(0, len(text), budget_chars)
        ]

    name = tokenizer_name(config)
    if name is None:
        return char_slices()
    try:
        semchunk = _load_semchunk()
    except ImportError:
        if warnings is not None:
            warnings.append(
                f"ingestion.tokenizer '{name}' requested but the package is not "
                "installed; using the chars/4 split (pip install 'corpus-kb[semchunk]')"
            )
        return char_slices()
    # ceil counter + floor token budget keeps every piece <= budget_chars,
    # so the char-budget conformance check cannot trip on a rounding edge.
    chunk_size_tokens = max(1, budget_chars // 4)
    counter: Callable[[str], int] = _default_token_counter
    pieces, spans = semchunk.chunk(
        text,
        chunk_size_tokens,
        counter,
        offsets=True,
        memoize=False,
    )
    # Defensive determinism contract: sorted, in-bounds, slice-exact.
    if len(pieces) == len(spans) and all(
        text[start:end] == piece for piece, (start, end) in zip(pieces, spans, strict=True)
    ):
        return list(pieces)
    logger.warning("semchunk offsets failed the slice check; falling back to char slices")
    return char_slices()
