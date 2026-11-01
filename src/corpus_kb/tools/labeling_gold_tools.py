"""MCP/HTTP/socket/CLI handler for the labeling gold-check op (F6).

One handler shared by every surface — the ``tools/labeling_tools.py``
pattern. :func:`labeling_gold_check` reads the tenant's built chunk
labels, loads a small gold dev set from a JSON file (chunk id -> label
name), and validates one class at a time through
``labeling/ppi_check.py`` (PPI over ``coding/ppi.py``).

The gold set must be a random draw from the built chunks; a
hand-picked set breaks the PPI inclusion assumption. The handler
reports BOTH numbers per class — the raw agreement and the corrected
prevalence estimate — so the corrected number never travels alone.

Envelope: ``{status, class_count, validations}`` guaranteed on every
outcome, plus ``reason`` everywhere and ``remedy`` on the guard paths.
Statuses: ``success``, ``insufficient_data`` (no gold file, unreadable
file, oversized file, no built labels, no gold overlap, off-vocabulary
labels), ``unavailable`` (migration 028 not applied).

The gold file is read ONCE: the reported sha256 and the parsed JSON
come from the same bytes, so the record can never describe a different
file than the one that was scored.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import asyncpg
import numpy as np

from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["MAX_GOLD_FILE_BYTES", "labeling_gold_check"]

# The labeled-corpus read bound (the engine's read-batch scale; the
# gold check needs the FULL built population for an honest prevalence).
LABEL_READ_LIMIT = 200_000

# A gold dev set is a small audited sample by design; this cap keeps a
# runaway file from being parsed as one (one MiB is generous).
MAX_GOLD_FILE_BYTES = 1_048_576

_UNAVAILABLE_REASON = "labeling tables absent (migration 028 not applied)"
_UNAVAILABLE_REMEDY = "run corpus-kb setup"
_GOLD_REMEDY = "Pass gold_file: a JSON object that maps built chunk ids to label names."


async def _load_labels(pg_pool: asyncpg.Pool, tenant_id: str) -> list[dict[str, object]]:
    """The label seam: the labeling store's built labels (tests fake it)."""
    from corpus_kb.labeling.writer import read_labels

    return await read_labels(pg_pool, tenant_id, LABEL_READ_LIMIT)


async def _tables_present(pg_pool: asyncpg.Pool) -> bool:
    """The store seam (tests substitute fakes here)."""
    from corpus_kb.labeling.writer import labeling_tables_present

    return await labeling_tables_present(pg_pool)


async def labeling_gold_check(
    pg_pool: asyncpg.Pool,
    gold_file: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    confidence_level: float = 0.95,
) -> dict[str, object]:
    """Score the built labels against the gold dev set, per class.

    ``gold_file`` names a JSON object that maps built chunk ids to label
    names. Returns ``{status, class_count, validations}`` plus
    ``reason`` and ``params``; each validation row is one class's
    ``GoldValidation`` (raw agreement and the PPI-corrected prevalence
    with its interval).
    """
    del config  # accepted for MCP-tool signature symmetry; reads are config-free
    level = _validated_level(confidence_level)
    if not gold_file:
        raise ValidationError("gold_file is required: point it at the gold dev set JSON file.")
    if not await _tables_present(pg_pool):
        return _honest("unavailable", _UNAVAILABLE_REASON, _UNAVAILABLE_REMEDY, level, gold_file)
    loaded = _load_gold(gold_file)
    if isinstance(loaded, str):
        return _honest("insufficient_data", loaded, _GOLD_REMEDY, level, gold_file)
    gold, sha12 = loaded
    rows = await _load_labels(pg_pool, tenant_id)
    if not rows:
        return _honest(
            "insufficient_data",
            "no built labels for this tenant.",
            "Run labeling-build to label chunks, then retry.",
            level,
            gold_file,
        )
    return _validate_classes(rows, gold, level, gold_file, sha12)


def _validate_classes(
    rows: list[dict[str, object]],
    gold: dict[str, str],
    level: float,
    gold_file: str,
    sha12: str,
) -> dict[str, object]:
    """Run one PPI validation per gold-covered class (pure once read)."""
    from corpus_kb.labeling.ppi_check import ppi_validate

    # One row per chunk is the store's key invariant; the first row wins
    # defensively so a duplicate can never trip ppi_validate's uniqueness
    # check as a raw error.
    by_id: dict[str, dict[str, object]] = {}
    for row in rows:
        chunk_id = str(row["chunk_id"])
        if chunk_id not in by_id:
            by_id[chunk_id] = row
    chunk_ids = list(by_id)
    classes = sorted({str(row["label"]) for row in by_id.values()})
    index_of = {name: position for position, name in enumerate(classes)}
    labels = [index_of[str(by_id[chunk_id]["label"])] for chunk_id in chunk_ids]
    built = set(chunk_ids)
    matched: dict[str, int] = {}
    ignored = 0
    for chunk_id, label in gold.items():
        if chunk_id not in built or label not in index_of:
            ignored += 1
            continue
        matched[chunk_id] = index_of[label]
    if not matched:
        return _honest(
            "insufficient_data",
            "no gold entries match a built chunk with a known label name.",
            "Pass built chunk ids and label names; labeling-list shows both.",
            level,
            gold_file,
        )
    labels_array = np.asarray(labels, dtype=int)
    validations = [
        ppi_validate(
            labels_array,
            chunk_ids,
            matched,
            class_index=index,
            class_name=classes[index],
            confidence_level=level,
        ).to_dict()
        for index in sorted(set(matched.values()))
    ]
    return {
        "status": "success",
        "class_count": len(validations),
        "validations": validations,
        "reason": f"{len(matched)} gold chunks matched the build; {ignored} ignored.",
        "params": {
            "gold_file": Path(gold_file).name,
            "gold_file_sha256": sha12,
            "confidence_level": level,
            "classes": classes,
            "gold_matched": len(matched),
            "gold_ignored": ignored,
            "n_population": len(chunk_ids),
        },
    }


def _load_gold(gold_file: str) -> tuple[dict[str, str], str] | str:
    """Read + shape-check the gold file; a str return is the reason.

    One read feeds both the parse and the reported sha256, so the
    identity record can never lag the file that was scored.
    """
    path = Path(gold_file)
    if not path.is_file():
        return f"the gold file was not found: {path.name}."
    try:
        raw = path.read_bytes()
    except OSError:
        return f"the gold file is not readable: {path.name}."
    if len(raw) > MAX_GOLD_FILE_BYTES:
        return (
            f"the gold file is over the gold-set cap "
            f"({MAX_GOLD_FILE_BYTES // 1048576} MiB). A gold dev set stays small."
        )
    try:
        parsed: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "the gold file is not readable JSON (UTF-8 JSON expected)."
    if not isinstance(parsed, dict) or not parsed:
        return "the gold file must hold a non-empty JSON object (chunk id -> label name)."
    for chunk_id, label in parsed.items():
        if not isinstance(label, str) or not label:
            return f"gold[{chunk_id!r}]: the label must be a non-empty label name."
    gold = {str(chunk_id): str(label) for chunk_id, label in parsed.items()}
    return gold, hashlib.sha256(raw).hexdigest()[:12]


def _honest(
    status: str, reason: str, remedy: str, level: float, gold_file: str
) -> dict[str, object]:
    """The guard envelope on the gold keys (reason + remedy named)."""
    return {
        "status": status,
        "class_count": 0,
        "validations": [],
        "reason": reason,
        "remedy": remedy,
        "params": {
            "gold_file": Path(gold_file).name if gold_file else "",
            "confidence_level": level,
        },
    }


def _validated_level(confidence_level: float) -> float:
    """A two-sided coverage level lives strictly inside (0, 1)."""
    if (
        isinstance(confidence_level, bool)
        or not isinstance(confidence_level, (int, float))
        or not 0.0 < float(confidence_level) < 1.0
    ):
        raise ValidationError("confidence_level must be a number strictly between 0 and 1.")
    return float(confidence_level)
