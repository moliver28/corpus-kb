"""The ``calibrate:`` block of a profile: its spec and its parser.

The block baselines ONE ingestion strategy against labeled golden
queries. ``sample`` and ``golden`` resolve against the profiles
directory, so a profile stays portable with its corpus. Unknown keys
fail loudly — the house pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import ValidationError

KNOWN_CALIBRATE_KEYS: frozenset[str] = frozenset(
    {"candidates", "sample", "golden", "k", "min_overlap"}
)


@dataclass(frozen=True)
class CalibrateSpec:
    """The calibrate-then-lock sweep block (Wave 7).

    ``sample`` and ``golden`` resolve against the profiles directory, so a
    profile stays portable with its corpus.
    """

    candidates: tuple[str, ...]
    sample: str
    golden: str
    k: int = 10
    min_overlap: float = 0.5


def parse_calibrate(raw: Any, name: str) -> CalibrateSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError(
            f"Profile {name!r} calibrate must be a mapping.",
            remediation="Use candidates, sample, golden, and optional k / min_overlap.",
        )
    _reject_unknown_calibrate_keys(raw, KNOWN_CALIBRATE_KEYS, name, "calibrate")
    return CalibrateSpec(
        candidates=_calibrate_candidates(raw.get("candidates"), name),
        sample=_calibrate_path(raw.get("sample"), "sample", name, "a file"),
        golden=_calibrate_path(raw.get("golden"), "golden", name, "a JSONL file"),
        k=_calibrate_k(raw.get("k", 10), name),
        min_overlap=_calibrate_min_overlap(raw.get("min_overlap", 0.5), name),
    )


def _reject_unknown_calibrate_keys(
    raw: dict[str, Any], known: frozenset[str], name: str, block: str
) -> None:
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValidationError(
            f"Profile {name!r} {block} has unknown keys: {', '.join(unknown)}.",
            remediation=f"Allowed keys: {', '.join(sorted(known))}.",
        )


def _calibrate_candidates(raw: Any, name: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValidationError(
            f"Profile {name!r} calibrate.candidates must be a non-empty list.",
            remediation="Name at least one ingestion strategy, e.g. [default, markdown_sections].",
        )
    if not all(isinstance(c, str) and c for c in raw):
        raise ValidationError(
            f"Profile {name!r} calibrate.candidates must hold strategy name strings.",
            remediation="Use registered strategy names, e.g. [default, markdown_sections].",
        )
    duplicates = sorted({c for c in raw if raw.count(c) > 1})
    if duplicates:
        raise ValidationError(
            f"Profile {name!r} calibrate.candidates repeats: {', '.join(duplicates)}.",
            remediation="List each strategy once.",
        )
    return tuple(raw)


def _calibrate_path(raw: Any, key: str, name: str, kind: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValidationError(
            f"Profile {name!r} calibrate.{key} must be a path.",
            remediation=f"Point calibrate.{key} at {kind} in the profiles directory.",
        )
    return raw


def _calibrate_k(raw: Any, name: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise ValidationError(
            f"Profile {name!r} calibrate.k must be a positive integer.",
            remediation="Set calibrate.k to a positive integer, e.g. 10.",
        )
    return raw


def _calibrate_min_overlap(raw: Any, name: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0.0 < float(raw) <= 1.0:
        raise ValidationError(
            f"Profile {name!r} calibrate.min_overlap must fall in (0, 1].",
            remediation="Set calibrate.min_overlap to a fraction, e.g. 0.5.",
        )
    return float(raw)
