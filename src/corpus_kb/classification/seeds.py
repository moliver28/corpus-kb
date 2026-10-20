"""Seed label sets: parse, validate, and resolve (WP-3).

A seed file is a JSON list of ``{"chunk_id": "<uuid>", "label": "<class>"}``
objects — the hand-picked examples (or the reviewed WP-2 output) the
classifier learns from. Parsing validates shape honestly (a bad seed file
is an ``insufficient_labels`` envelope, never a guess) and resolution
maps every seed to its stored vector, naming the missing ids.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .engine import StoredVector

__all__ = ["SEED_SCHEMA_REMEDY", "SeedSet", "parse_seed_file", "resolve"]

SEED_SCHEMA_REMEDY = (
    "Write a JSON list of objects, each with a 'chunk_id' (a stored chunk "
    "uuid) and a 'label' (the class name). Pass the file path as seed_file."
)

_ERROR_LIMIT = 5

_RESOLVE_REMEDY = (
    "Seed chunk ids must match embedded chunks of this tenant. Re-ingest "
    "the seed documents, or pick ids from the stored corpus."
)


@dataclass(frozen=True)
class SeedSet:
    """A validated seed label set (ids canonical, pairs id-sorted)."""

    pairs: list[tuple[str, str]]  # (chunk_id, label), sorted by chunk_id

    @property
    def classes(self) -> list[str]:
        """The distinct labels, sorted (the stable class order)."""
        return sorted({label for _chunk_id, label in self.pairs})

    @property
    def class_counts(self) -> dict[str, int]:
        """Seeds per class, keyed by the sorted class order."""
        counts = dict.fromkeys(self.classes, 0)
        for _chunk_id, label in self.pairs:
            counts[label] += 1
        return counts


def parse_seed_file(path: str) -> SeedSet:
    """Parse and validate a seed file; a bad file raises ``ValueError``.

    The message names the first problem (and up to five offenders), so the
    caller can answer an honest envelope without re-reading the file.
    """
    raw = _read_bytes(path)
    payload: object = _decode(raw, path)
    if not isinstance(payload, list) or not payload:
        raise ValueError(
            f"seed file {Path(path).name!r} is empty or not a JSON list. {SEED_SCHEMA_REMEDY}"
        )
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    dupes: list[str] = []
    invalid: list[str] = []
    for position, item in enumerate(payload):
        chunk_id, label = _parse_entry(item, position, path)
        if chunk_id is None:
            continue
        try:
            canonical = str(uuid.UUID(chunk_id))
        except ValueError:
            if len(invalid) < _ERROR_LIMIT:
                invalid.append(chunk_id)
            continue
        if canonical in seen:
            if len(dupes) < _ERROR_LIMIT:
                dupes.append(canonical)
            continue
        seen.add(canonical)
        pairs.append((canonical, label))
    if invalid:
        raise ValueError(
            f"seed file {Path(path).name!r} has chunk ids that are not uuids: "
            f"{', '.join(invalid)}. {SEED_SCHEMA_REMEDY}"
        )
    if dupes:
        raise ValueError(
            f"seed file {Path(path).name!r} repeats chunk ids: {', '.join(dupes)}. "
            "Each chunk may carry one label."
        )
    return SeedSet(pairs=sorted(pairs))


def resolve(seed_set: SeedSet, vectors: list[StoredVector]) -> tuple[np.ndarray, np.ndarray]:
    """Map seed pairs to their stored vectors; ``X`` float32, ``y`` labels.

    Raises ``ValueError`` naming up to five missing ids when a seed has no
    stored embedding — a seed that is not in the corpus cannot train.
    """
    by_id = {vector.chunk_id: vector.vector for vector in vectors}
    missing = [chunk_id for chunk_id, _label in seed_set.pairs if chunk_id not in by_id]
    if missing:
        shown = ", ".join(sorted(missing)[:_ERROR_LIMIT])
        raise ValueError(
            f"{len(missing)} seed chunk ids have no stored embedding; first: {shown}. "
            f"{_RESOLVE_REMEDY}"
        )
    matrix = np.stack([by_id[chunk_id] for chunk_id, _label in seed_set.pairs])
    labels = np.asarray([label for _chunk_id, label in seed_set.pairs])
    return matrix.astype(np.float32), labels


def _read_bytes(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(f"seed file {path!r} is not readable: {exc.strerror}") from exc


def _decode(raw: bytes, path: str) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"seed file {Path(path).name!r} is not valid JSON: {exc}") from exc


def _parse_entry(item: object, position: int, path: str) -> tuple[str | None, str]:
    """One list entry -> (chunk_id, label); a malformed entry raises."""
    name = Path(path).name
    if not isinstance(item, dict):
        raise ValueError(
            f"seed file {name!r} entry {position} is not an object. {SEED_SCHEMA_REMEDY}"
        )
    chunk_id = item.get("chunk_id")
    label = item.get("label")
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        raise ValueError(
            f"seed file {name!r} entry {position} misses a 'chunk_id' string. {SEED_SCHEMA_REMEDY}"
        )
    if not isinstance(label, str) or not label.strip():
        raise ValueError(
            f"seed file {name!r} entry {position} misses a 'label' string. {SEED_SCHEMA_REMEDY}"
        )
    return chunk_id, label.strip()
