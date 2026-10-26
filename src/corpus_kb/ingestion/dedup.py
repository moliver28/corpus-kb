"""Near-duplicate chunk dedup: MinHash LSH behind an opt-in config gate.

``ingestion.dedup: minhash`` turns this on (the lazy ``[dedup]`` extra
provides datasketch; CI never installs it). Before embedding, chunks are
sketched as sets of lowercased words, MinHash LSH proposes candidate pairs
(below the decision threshold, on purpose), and an exact Jaccard check
confirms them — LSH is probabilistic, the decision is not. Each duplicate
group keeps ONE survivor under the deterministic rule: lowest
(document_id, start_char, chunk_id). Dropped
chunk ids return in a stable sorted list for the ``dedup_dropped``
envelope key.

Without the config key (or without the package) the caller skips this
module entirely and the pipeline behaves exactly as before.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..errors import ConfigError
from ..utils.models import Chunk

logger = logging.getLogger(__name__)

DEDUP_MODE = "minhash"
DEFAULT_THRESHOLD = 0.9
NUM_PERM = 128

_WORD_RE = re.compile(r"[a-z0-9]+")


def dedup_config(config: Mapping[str, Any] | None) -> dict[str, float] | None:
    """Read the dedup gate. None means off; unknown values are config errors."""
    if not config:
        return None
    ingestion = config.get("ingestion")
    if not isinstance(ingestion, Mapping):
        return None
    raw = ingestion.get("dedup")
    if raw is None:
        return None
    if raw != DEDUP_MODE:
        raise ConfigError(
            f"Unknown ingestion.dedup value '{raw}'. Supported value: {DEDUP_MODE}.",
            remediation="Set ingestion.dedup to minhash, or remove the key.",
        )
    threshold = ingestion.get("dedup_threshold", DEFAULT_THRESHOLD)
    try:
        value = float(threshold)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"ingestion.dedup_threshold is not a number: '{threshold}'.",
            remediation="Set ingestion.dedup_threshold to a number between 0 and 1.",
        ) from exc
    if not 0.0 < value <= 1.0:
        raise ConfigError(
            f"ingestion.dedup_threshold must be in (0, 1], got {value}.",
            remediation="Set ingestion.dedup_threshold to a number between 0 and 1.",
        )
    return {"threshold": value}


def _load_datasketch() -> tuple[Any, Any]:
    """The lazy import seam (tests fake this; the extra stays optional).

    Dynamic import on purpose: the extra is optional and CI never installs
    it, so the import edge must stay invisible to static resolution (the
    latechunk/topics pattern).
    """
    import importlib

    module = importlib.import_module("datasketch")
    return module.MinHash, module.MinHashLSH


def _word_set(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _find_root(parent: list[int], x: int) -> int:
    """Union-find root lookup with path halving."""
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _sort_key(chunk: Chunk) -> tuple[str, int, str]:
    start = chunk.source_start_char if chunk.source_start_char is not None else -1
    return (str(chunk.document_id), start, str(chunk.chunk_id))


def _sketch_and_index(
    chunks: Sequence[Chunk],
    words: list[set[str]],
    *,
    threshold: float,
    num_perm: int,
) -> tuple[dict[int, Any], Any]:
    """MinHash-sign every non-empty chunk and index it in one LSH table.

    LSH proposes candidates below the decision threshold on purpose: the
    exact Jaccard check makes the final call, so near-threshold pairs are
    not left to MinHash sampling luck.
    """
    minhash_cls, lsh_factory = _load_datasketch()
    candidate_threshold = max(0.5, threshold - 0.2)
    lsh = lsh_factory(threshold=candidate_threshold, num_perm=num_perm)
    signatures: dict[int, Any] = {}
    for index in range(len(chunks)):
        if not words[index]:
            continue
        signature = minhash_cls(num_perm=num_perm)
        for word in sorted(words[index]):
            signature.update(word.encode("utf-8"))
        signatures[index] = signature
        lsh.insert(str(index), signature)
    return signatures, lsh


def _confirmed_groups(
    chunks: Sequence[Chunk],
    words: list[set[str]],
    signatures: dict[int, Any],
    lsh: Any,
    *,
    threshold: float,
) -> list[list[int]]:
    """Union-find over LSH candidate pairs confirmed by exact Jaccard."""
    parent = list(range(len(chunks)))

    def _union(a: int, b: int) -> None:
        ra, rb = _find_root(parent, a), _find_root(parent, b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for index, signature in signatures.items():
        for other_key in lsh.query(signature):
            other = int(other_key)
            if other <= index:
                continue
            shared = words[index] & words[other]
            total = words[index] | words[other]
            if total and len(shared) / len(total) >= threshold:
                _union(index, other)

    groups: dict[int, list[int]] = {}
    for index in range(len(chunks)):
        groups.setdefault(_find_root(parent, index), []).append(index)
    return list(groups.values())


def dedup_chunks(
    chunks: Sequence[Chunk],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    num_perm: int = NUM_PERM,
) -> tuple[list[Chunk], list[str]]:
    """Drop near-duplicate chunks; keep the deterministic survivor per group.

    Returns (kept, dropped_ids). ``kept`` preserves the caller's order so
    downstream stage behavior is unchanged apart from the dropped rows.
    """
    if len(chunks) < 2:
        return list(chunks), []
    words = [_word_set(chunk.text) for chunk in chunks]
    signatures, lsh = _sketch_and_index(chunks, words, threshold=threshold, num_perm=num_perm)
    groups = _confirmed_groups(chunks, words, signatures, lsh, threshold=threshold)

    dropped: set[int] = set()
    for members in groups:
        if len(members) < 2:
            continue
        survivor = min(members, key=lambda i: _sort_key(chunks[i]))
        dropped.update(index for index in members if index != survivor)

    if not dropped:
        return list(chunks), []
    pairs = sorted((_sort_key(chunks[index]), str(chunks[index].chunk_id)) for index in dropped)
    dropped_ids = [chunk_id for _, chunk_id in pairs]
    kept = [chunk for index, chunk in enumerate(chunks) if index not in dropped]
    return kept, dropped_ids
