"""Seeded-error list format for the TAO bench (R14 prep, TD-17 prep).

One CSV (``error_id, item_id, error_type, expected_label``) holds the
planted errors the T2 bench scores the coder against. The closed error-type
list mirrors TD-17 point 2. The seed list stays OUTSIDE what the coder
reads (S11): loaders hand it to the bench harness, never to a coding run.

Named ``bench_seeds`` to avoid the collision with
``classification/seeds.py``, whose ``parse_seed_file`` loads a different
format (JSON chunk-label pairs) for ``classification_build`` (athena G-14).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from corpus_kb.errors import SeedFileError

__all__ = ["ERROR_TYPES", "SEED_COLUMNS", "SeedError", "load_seed_file", "packaged_seed_set"]

#: The closed error-type vocabulary (TD-17 point 2).
ERROR_TYPES: frozenset[str] = frozenset(
    {
        "wrong_human_label",
        "invalid_structure",
        "unverified_quote",
        "negated_keyword",
        "bad_translation",
    }
)

SEED_COLUMNS: tuple[str, ...] = ("error_id", "item_id", "error_type", "expected_label")

_PACKAGED_SEEDS = "ta-seed-set.csv"


@dataclass(frozen=True)
class SeedError:
    """One planted error: which item, what kind, the label that should win."""

    error_id: str
    item_id: str
    error_type: str
    expected_label: str


def load_seed_file(path: str | Path) -> list[SeedError]:
    """Load and validate one seed CSV; a bad file raises ``SeedFileError``.

    The message names the file, the row, and the first problem, so the
    bench answers an honest envelope without re-reading the file. Rows
    order by ``error_id`` (deterministic snapshots, zero-drift rule 9).
    """
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SeedFileError(f"Seed file {source.name!r} is not readable: {exc.strerror}") from exc
    reader = csv.DictReader(io.StringIO(raw))
    header = tuple(reader.fieldnames or ())
    if header != SEED_COLUMNS:
        raise SeedFileError(
            f"Seed file {source.name!r} must have the header row "
            f"{','.join(SEED_COLUMNS)}; got {','.join(header) or 'none'}"
        )
    seeds: list[SeedError] = []
    seen: set[str] = set()
    for line, row in enumerate(reader, start=2):  # line 1 is the header
        values = {key: (row.get(key) or "").strip() for key in SEED_COLUMNS}
        empty = [key for key, value in values.items() if not value]
        if empty:
            raise SeedFileError(
                f"Seed file {source.name!r} line {line} misses {', '.join(empty)} (a truncated row)"
            )
        if values["error_type"] not in ERROR_TYPES:
            raise SeedFileError(
                f"Seed file {source.name!r} line {line} has unknown error_type "
                f"{values['error_type']!r}; use one of {', '.join(sorted(ERROR_TYPES))}"
            )
        if values["error_id"] in seen:
            raise SeedFileError(
                f"Seed file {source.name!r} line {line} repeats error_id {values['error_id']!r}"
            )
        seen.add(values["error_id"])
        seeds.append(
            SeedError(
                error_id=values["error_id"],
                item_id=values["item_id"],
                error_type=values["error_type"],
                expected_label=values["expected_label"],
            )
        )
    if not seeds:
        raise SeedFileError(f"Seed file {source.name!r} holds no seed rows")
    return sorted(seeds, key=lambda seed: seed.error_id)


def packaged_seed_set() -> list[SeedError]:
    """Load the seed CSV packaged inside the corpus_kb wheel."""
    ref = resources.files("corpus_kb") / "coding" / "seeds" / _PACKAGED_SEEDS
    with resources.as_file(ref) as path:
        return load_seed_file(path)
